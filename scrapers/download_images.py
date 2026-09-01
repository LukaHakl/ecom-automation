"""Bulk-download image URLs from a produced CSV into ``images/{sku}/``.

Only needed for archival, or for a platform that requires uploads rather than
URLs. Vela and Shopify both fetch from URLs, so the common path skips this
entirely -- which is why it is a separate script rather than a step in the
scraper.

    python -m scrapers.download_images --csv listings.csv --out images/
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from common.progress import pause_if_interactive
from common.vela import PHOTO_COLUMNS

#: Some CDNs reject the default python-requests User-Agent outright.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

#: Modest on purpose. This is somebody else's CDN and the job is not urgent.
DEFAULT_WORKERS = 4
DEFAULT_DELAY = 0.25


def extension_for(url: str) -> str:
    tail = url.split("?")[0].rsplit(".", 1)
    return ("." + tail[-1][:4]) if len(tail) == 2 and len(tail[-1]) <= 4 else ".jpg"


def plan_downloads(rows: list[dict], out_dir: str) -> list[tuple[str, str]]:
    """(url, destination) pairs, skipping files already on disk.

    Resuming matters: the original run died at 171 of 224.
    """
    jobs = []
    for row in rows:
        sku = (row.get("SKU") or "").strip() or (row.get("TITLE") or "").strip()
        if not sku:
            continue
        folder = os.path.join(out_dir, "".join(
            c if c.isalnum() or c in "-_" else "_" for c in sku))
        for index, column in enumerate(PHOTO_COLUMNS, start=1):
            url = (row.get(column) or "").strip()
            if not url:
                continue
            destination = os.path.join(folder, "%02d%s" % (index, extension_for(url)))
            if not os.path.exists(destination):
                jobs.append((url, destination))
    return jobs


def download_one(url: str, destination: str, delay: float) -> tuple[str, bool, str]:
    import requests

    os.makedirs(os.path.dirname(destination), exist_ok=True)
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        response.raise_for_status()
        with open(destination, "wb") as handle:
            handle.write(response.content)
        time.sleep(delay)
        return url, True, ""
    except Exception as exc:                              # noqa: BLE001
        return url, False, type(exc).__name__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="download_images",
        description="Download image URLs from a Vela CSV into images/{sku}/.")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--out", default="images")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help="per-request pause, per worker")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    with open(args.csv, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    jobs = plan_downloads(rows, args.out)
    print("%d images to fetch (already-present files skipped)" % len(jobs))

    ok = failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(download_one, url, destination, args.delay)
                   for url, destination in jobs]
        for done, future in enumerate(as_completed(futures), start=1):
            _url, success, error = future.result()
            ok, failed = ok + bool(success), failed + (not success)
            if not success:
                print("  failed (%s): %s" % (error, _url[:80]))
            if done % 25 == 0:
                print("  %d/%d" % (done, len(jobs)), flush=True)

    print("\ndownloaded %d, failed %d" % (ok, failed))
    return 0


if __name__ == "__main__":
    code = main()
    pause_if_interactive()
    sys.exit(code)
