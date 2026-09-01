"""Build an Altera image-attachment file, matching images to products strictly.

The easy half is the output: for each product handle, one row per image with
``Image Src``, ``Image Command = MERGE``, ``Image Position``, and ``Top Row``
set only on the first.

The hard half is deciding which images belong to which product when they come
from different exports. This module is deliberately, aggressively strict about
that, on evidence:

    A permissive matcher assigned images to 649 of 586 products -- more matches
    than products, because it matched several sources to the same target. About
    160 of them were wrong. The strict version matched 489 and skipped 97, and
    that was the better outcome.

**Skipping is cheap and wrong matches are expensive.** A skipped product simply
keeps the images it already has; nobody notices and nothing breaks. A wrong
image is a product page showing someone else's product, and it is far harder to
detect than a missing one because the page looks fine.

So the match order is: exact normalised title, then an explicit override table,
then a strict keyword match with a high threshold. There is deliberately **no
fallback to loose fuzzy matching**. If none of the three fire, the product is
skipped and named in the report.

The skip list is the deliverable as much as the CSV.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field

from common.altera import MERGE
from .product import Product, normalise_title, title_similarity

#: A keyword match must clear this to count. High on purpose -- this is the
#: last resort before skipping, not a general-purpose matcher.
STRICT_THRESHOLD = 0.90

#: Marks a product as deliberately having no images here, so it stops appearing
#: in the unmatched report. Without this the skip list accumulates known-fine
#: entries and stops being read.
NO_IMAGES = "__none__"

EXACT, OVERRIDE, KEYWORD, SKIPPED = "exact", "override", "keyword", "skipped"


@dataclass
class MatchResult:
    handle: str
    title: str
    images: list[str] = field(default_factory=list)
    method: str = SKIPPED
    score: float = 0.0


def match_images(products: list[Product], sources: list[Product],
                 overrides: dict[str, str] | None = None,
                 threshold: float = STRICT_THRESHOLD) -> list[MatchResult]:
    """Match each target product to a source product's images.

    `overrides` maps a target handle (or title) to a source title, or to
    :data:`NO_IMAGES`. It is checked before keyword matching but after exact
    title equality, so an override corrects the fuzzy step without having to
    restate matches that already work.

    A source is consumed once matched. Without that, one source's images attach
    to every product resembling it -- which is precisely how the permissive
    version produced more matches than there were products.
    """
    overrides = overrides or {}
    by_title: dict[str, list[Product]] = {}
    for source in sources:
        by_title.setdefault(normalise_title(source.title), []).append(source)

    claimed: set[int] = set()
    results = []

    for product in products:
        key = product.title
        handle = _handle(product)
        override = overrides.get(handle, overrides.get(key))

        if override == NO_IMAGES:
            results.append(MatchResult(handle, product.title, [], SKIPPED, 0.0))
            continue

        # 1. exact normalised title
        bucket = [s for s in by_title.get(normalise_title(product.title), [])
                  if id(s) not in claimed]
        if bucket:
            claimed.add(id(bucket[0]))
            results.append(MatchResult(handle, product.title,
                                       list(bucket[0].images), EXACT, 1.0))
            continue

        # 2. explicit override table
        if override:
            bucket = [s for s in by_title.get(normalise_title(override), [])
                      if id(s) not in claimed]
            if bucket:
                claimed.add(id(bucket[0]))
                results.append(MatchResult(handle, product.title,
                                           list(bucket[0].images), OVERRIDE, 1.0))
                continue

        # 3. strict keyword match, high threshold, no fallback below it
        best, best_score = None, 0.0
        for source in sources:
            if id(source) in claimed:
                continue
            score = title_similarity(product.title, source.title)
            if score > best_score:
                best, best_score = source, score

        if best is not None and best_score >= threshold:
            claimed.add(id(best))
            results.append(MatchResult(handle, product.title, list(best.images),
                                       KEYWORD, round(best_score, 3)))
        else:
            results.append(MatchResult(handle, product.title, [], SKIPPED,
                                       round(best_score, 3)))

    return results


def _handle(product: Product) -> str:
    from .product import first_sku
    return first_sku(product.sku) or normalise_title(product.title).replace(" ", "-")


IMAGE_COLUMNS = ["Handle", "Image Src", "Image Command", "Image Position",
                 "Top Row"]


def build_image_rows(results: list[MatchResult]) -> list[dict]:
    """One row per image, Top Row on the first row of each handle block only."""
    rows = []
    for result in results:
        for position, url in enumerate(result.images, start=1):
            rows.append({
                "Handle": result.handle,
                "Image Src": url,
                "Image Command": MERGE,
                "Image Position": str(position),
                "Top Row": "TRUE" if position == 1 else "",
            })
    return rows


def write_image_csv(rows: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=IMAGE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def load_overrides(path: str | None) -> dict[str, str]:
    """Two-column CSV: target handle-or-title, source title (or ``__none__``)."""
    if not path:
        return {}
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            return {row[0].strip(): row[1].strip()
                    for row in csv.reader(handle)
                    if len(row) >= 2 and row[0].strip()
                    and not row[0].startswith("#")}
    except FileNotFoundError:
        return {}


def summary(results: list[MatchResult]) -> str:
    """Counts by method, then **every** skipped title.

    Printed in full rather than sampled. The skip list is what gets acted on,
    and a truncated one silently becomes a shorter to-do list than the truth.
    """
    counts: dict[str, int] = {}
    for result in results:
        counts[result.method] = counts.get(result.method, 0) + 1

    matched = sum(v for k, v in counts.items() if k != SKIPPED)
    images = sum(len(r.images) for r in results)

    lines = ["", "=== Image matching ===",
             "products in:  %d" % len(results),
             "matched:      %d  (%d image rows)" % (matched, images)]
    for method in (EXACT, OVERRIDE, KEYWORD):
        lines.append("  %-9s   %d" % (method, counts.get(method, 0)))
    lines.append("skipped:      %d" % counts.get(SKIPPED, 0))

    skipped = [r for r in results if r.method == SKIPPED]
    if skipped:
        lines += ["", "Skipped -- these keep their existing images. Add an "
                      "override to fix one, or __none__ to silence it:", ""]
        for result in skipped:
            lines.append("  %-50s (best score %.2f)"
                         % (result.title[:50], result.score))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="image_rows",
        description="Build an Altera image-attachment file with strict matching.")
    parser.add_argument("--products", required=True,
                        help="target catalogue needing images")
    parser.add_argument("--images-from", required=True,
                        help="source catalogue that has the images")
    parser.add_argument("--overrides", help="two-column CSV of manual matches")
    parser.add_argument("--threshold", type=float, default=STRICT_THRESHOLD,
                        help="keyword match floor. Lowering this is how the "
                             "permissive version got ~160 products wrong")
    parser.add_argument("--out", default="image_rows.csv")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv=None) -> int:
    from .readers import read_catalogue

    args = build_parser().parse_args(argv)
    products = read_catalogue(args.products)
    sources = read_catalogue(args.images_from)

    results = match_images(products, sources, load_overrides(args.overrides),
                           args.threshold)
    rows = build_image_rows(results)
    print(summary(results))

    if args.dry_run:
        print("\n--dry-run: nothing written. First 3 rows:")
        for row in rows[:3]:
            print("   ", row)
        return 0

    write_image_csv(rows, args.out)
    print("\nrows out: %d -> %s" % (len(rows), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
