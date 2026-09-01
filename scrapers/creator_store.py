"""One parameterised creator-store scraper, not one script per creator.

    python -m scrapers.creator_store --site myminifactory --creator NAME --out out.csv
    python -m scrapers.creator_store --site cults3d --from-folder ./saved --out out.csv

Three acquisition modes, because the sites fight back differently. All three
produce the same artifact -- a deduplicated list of product URLs -- so stage 2
consumes any of them and they are interchangeable:

**live**
    Load the storefront and auto-scroll until the product count *stops growing*.
    Not a fixed number of scrolls: the previous version scrolled five times and
    silently returned zero products.

**folder**
    Point at a directory of pages saved as "Complete Webpage" and regex the IDs
    out. The reliable fallback when scrolling fails.

**tabs**
    Enumerate the tabs already open in the attached Chrome. Useful for a
    hand-picked selection across several creators in one pass.

Stage 2 then fetches each product page and parses it. The guard rails there are
not optional -- see :class:`common.progress.ConsecutiveFailures` for what
happens without them.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time

from common.browser import attach_to_chrome, survives_disconnect
from common.progress import (Checkpoint, ConsecutiveFailures,
                             pause_if_interactive, timestamp)
from common.text_clean import clean_description, clean_title, load_denylist, make_sku
from common.vela import Listing, write_post_import_todo, write_vela_csv
from .sites.base import diagnose_empty
from .sites.generic import get_adapter

SCROLL_PAUSE = 2.0
#: Stop scrolling after this many consecutive scrolls that add nothing.
SCROLL_IDLE_LIMIT = 3


def collect_from_folder(adapter, folder: str) -> list[str]:
    """Mode B: extract product URLs from saved "Complete Webpage" files."""
    urls, seen = [], set()
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith((".html", ".htm")):
            continue
        with open(os.path.join(folder, name), encoding="utf-8",
                  errors="replace") as handle:
            for url in adapter.product_urls_from_html(handle.read()):
                key = adapter.product_id(url) or url
                if key not in seen:
                    seen.add(key)
                    urls.append(url)
        print("  %s -> %d unique so far" % (name, len(urls)))
    return urls


def collect_from_live(adapter, driver, url: str) -> list[str]:
    """Mode A: scroll until the product count stops growing."""
    driver.get(url)
    time.sleep(SCROLL_PAUSE)

    seen_count, idle = 0, 0
    while idle < SCROLL_IDLE_LIMIT:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(SCROLL_PAUSE)
        found = len(adapter.product_urls_from_html(driver.page_source))
        if found > seen_count:
            seen_count, idle = found, 0
            print("  %d products visible ..." % found, flush=True)
        else:
            idle += 1

    urls = adapter.product_urls_from_html(driver.page_source)
    if not urls:
        # Never just error out: the anchor dump is the difference between a
        # ten-minute selector fix and an hour of guessing.
        print(diagnose_empty(driver.page_source))
    return urls


def collect_from_tabs(adapter, driver) -> list[str]:
    """Mode C: scrape whatever is already open."""
    urls, seen = [], set()
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        for url in adapter.product_urls_from_html(driver.page_source) or [driver.current_url]:
            key = adapter.product_id(url) or url
            if key and key not in seen:
                seen.add(key)
                urls.append(url)
    return urls


def scrape_products(adapter, driver, urls: list[str], checkpoint: Checkpoint,
                    delay_min: float, delay_max: float, halt_after: int):
    """Stage 2. Returns (products, failures) and stops on sustained failure."""
    failures = ConsecutiveFailures(limit=halt_after)
    products = []

    pending = checkpoint.pending(urls)
    print(checkpoint.startup_message(len(urls)))

    for index, url in enumerate(pending, start=1):
        driver.get(url)
        time.sleep(random.uniform(delay_min, delay_max))
        product = adapter.parse_product(driver.page_source, url)

        if product.is_valid():
            products.append(product)
            checkpoint.mark_done(url)
            failures.record(True)
        else:
            checkpoint.mark_failed(url, product.failure_reason())
            failures.record(False)
            print("  invalid (%s): %s" % (product.failure_reason(), url[:80]))

        if index % 10 == 0:
            checkpoint.save()
            print("  %d/%d done" % (index, len(pending)), flush=True)

        if failures.tripped:
            print("\n" + failures.message())
            break

    checkpoint.save()
    return products, failures


def to_listings(products, creator: str, denylist, glossary, price="") -> list[Listing]:
    """Shape scraped products into Vela listings, reporting every edit."""
    listings = []
    for product in products:
        title = clean_title(product.title, denylist=denylist)
        description = clean_description(product.description, denylist, glossary)
        for entry in title.removed + description.removed:
            print("    [%s] %s" % (title.text[:28], entry))
        listings.append(Listing(
            title=title.text,
            description=description.text,
            price=price,
            photos=product.images,
            # Slug from the *cleaned* title: a SKU derived from the original
            # while the listing shows the cleaned name is a mismatch that
            # surfaces later, in whichever system joins on SKU.
            sku=make_sku(creator, title.text),
        ))
    return listings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="creator_store",
        description="Scrape a creator storefront into a Vela import CSV.")
    parser.add_argument("--site", required=True, choices=("myminifactory", "cults3d"))
    parser.add_argument("--creator", default="",
                        help="creator handle, used for the storefront URL and "
                             "as the SKU prefix")
    parser.add_argument("--url", help="storefront URL (overrides --creator)")
    parser.add_argument("--from-folder", help="mode B: directory of saved pages")
    parser.add_argument("--from-tabs", action="store_true", help="mode C: open tabs")
    parser.add_argument("--out", default="listings.csv")
    parser.add_argument("--price", default="",
                        help="price for every listing; blank by default because "
                             "pricing is a separate decision")
    parser.add_argument("--denylist", help="IP denylist file (never committed)")
    parser.add_argument("--delay-min", type=float, default=1.5)
    parser.add_argument("--delay-max", type=float, default=4.0)
    parser.add_argument("--halt-after", type=int, default=10,
                        help="stop after N consecutive invalid parses")
    parser.add_argument("--limit", type=int, help="stop after N products")
    return parser


@survives_disconnect
def run(args) -> int:
    adapter = get_adapter(args.site)
    driver = None
    stamp = timestamp()

    if args.from_folder:
        urls = collect_from_folder(adapter, args.from_folder)
    else:
        driver = attach_to_chrome(os.path.dirname(os.path.abspath(__file__)))
        if args.from_tabs:
            urls = collect_from_tabs(adapter, driver)
        else:
            url = args.url or "https://www.%s.com/users/%s" % (args.site, args.creator)
            urls = collect_from_live(adapter, driver, url)

    print("\n%d product URLs" % len(urls))
    if not urls:
        return 1
    if args.limit:
        urls = urls[:args.limit]

    if driver is None:
        driver = attach_to_chrome(os.path.dirname(os.path.abspath(__file__)))

    checkpoint = Checkpoint.load("creator_store_progress_%s.json" % stamp)
    products, failures = scrape_products(adapter, driver, urls, checkpoint,
                                         args.delay_min, args.delay_max,
                                         args.halt_after)

    listings = to_listings(products, args.creator or args.site,
                           load_denylist(args.denylist), {}, args.price)
    rows = write_vela_csv(listings, args.out)
    write_post_import_todo(listings, "post_import_todo.txt")

    with_images = sum(1 for listing in listings if listing.photos)
    print("\n=== Summary ===")
    print("total URLs:  %d" % len(urls))
    print("succeeded:   %d" % len(products))
    print("failed:      %d" % (failures.total - len(products)))
    # Never report success without this number. A run that "worked" and wrote
    # rows with no images is the failure this scraper exists to prevent.
    print("with images: %d of %d listings" % (with_images, len(listings)))
    print("rows out:    %d -> %s" % (len(rows), args.out))
    return 0


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    code = main()
    pause_if_interactive()
    sys.exit(code)
