"""Compare two platform catalogues and report how well they are linked.

Motivating case: an Etsy and a Shopify catalogue that were supposed to hold
identical inventory turned out to share **948 of 14,792 SKUs -- 6.4% linked**.
The two stores had been running as separate inventories for months without
anyone noticing, because nothing had ever compared them.

Two modes, and picking the wrong one wastes a day:

``--by sku``
    Set intersection on the SKU field. Fast, exact, and the right first
    question. But when the two platforms use different SKU conventions it
    reports ~93% unlinked, which is *technically true and completely useless* --
    the products are the same, the naming is not.

``--by title``
    Normalised title matching, for when SKU schemes have diverged. This is the
    mode that answers "are these the same catalogue" rather than "do these use
    the same codes".

Title matching produces false positives across creators, so anything below the
review threshold is reported as ``REVIEW`` rather than ``MATCHED``. A row a
human has to check is cheap; a wrong link acted on is not.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass

from .product import Product, all_skus, normalise_title, title_similarity

LINKED, A_ONLY, B_ONLY, REVIEW = "LINKED", "A_ONLY", "B_ONLY", "REVIEW"

#: Below this, a title match is reported for human review rather than asserted.
#: Set high on purpose: the cost of a wrong link is a mis-synced inventory,
#: the cost of a REVIEW row is thirty seconds of someone's attention.
REVIEW_THRESHOLD = 0.85


@dataclass
class Row:
    status: str
    key: str
    a_title: str = ""
    b_title: str = ""
    score: float = 0.0


def reconcile_by_sku(catalogue_a: list[Product],
                     catalogue_b: list[Product]) -> list[Row]:
    """Set intersection and differences on SKU.

    Handles comma-separated SKU fields and stray whitespace: a product counts as
    linked if *any* of its SKUs appears on the other side, because Etsy's
    variation rows scatter a product's SKUs across several cells.
    """
    index_a: dict[str, Product] = {}
    for product in catalogue_a:
        for sku in all_skus(product.sku):
            index_a.setdefault(sku, product)
    index_b: dict[str, Product] = {}
    for product in catalogue_b:
        for sku in all_skus(product.sku):
            index_b.setdefault(sku, product)

    rows = []
    for sku in sorted(set(index_a) & set(index_b)):
        rows.append(Row(LINKED, sku, index_a[sku].title, index_b[sku].title, 1.0))
    for sku in sorted(set(index_a) - set(index_b)):
        rows.append(Row(A_ONLY, sku, index_a[sku].title))
    for sku in sorted(set(index_b) - set(index_a)):
        rows.append(Row(B_ONLY, sku, "", index_b[sku].title))
    return rows


def reconcile_by_title(catalogue_a: list[Product], catalogue_b: list[Product],
                       threshold: float = REVIEW_THRESHOLD) -> list[Row]:
    """Normalised title matching, with everything uncertain flagged REVIEW.

    Exact normalised equality is taken first and consumes its target, so one B
    product cannot be claimed by several A products -- without that, a generic
    title like "Terrain Set" links to everything that resembles it.
    """
    remaining = list(catalogue_b)
    exact_index: dict[str, list[Product]] = {}
    for product in remaining:
        exact_index.setdefault(normalise_title(product.title), []).append(product)

    rows = []
    claimed = set()

    for product in catalogue_a:
        key = normalise_title(product.title)
        bucket = [p for p in exact_index.get(key, []) if id(p) not in claimed]
        if bucket:
            twin = bucket[0]
            claimed.add(id(twin))
            rows.append(Row(LINKED, product.primary_sku or product.title,
                            product.title, twin.title, 1.0))
            continue

        best, best_score = None, 0.0
        for candidate in remaining:
            if id(candidate) in claimed:
                continue
            score = title_similarity(product.title, candidate.title)
            if score > best_score:
                best, best_score = candidate, score

        if best is not None and best_score > 0:
            claimed.add(id(best))
            status = LINKED if best_score >= threshold else REVIEW
            rows.append(Row(status, product.primary_sku or product.title,
                            product.title, best.title, round(best_score, 3)))
        else:
            rows.append(Row(A_ONLY, product.primary_sku or product.title,
                            product.title))

    for candidate in remaining:
        if id(candidate) not in claimed:
            rows.append(Row(B_ONLY, candidate.primary_sku or candidate.title,
                            "", candidate.title))
    return rows


def summarise(rows: list[Row], sample: int = 10) -> str:
    """The printed summary. The CSV is acted on; this is what gets read."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    total = len(rows) or 1

    lines = ["", "=== Reconciliation ==="]
    for status in (LINKED, REVIEW, A_ONLY, B_ONLY):
        count = counts.get(status, 0)
        lines.append("  %-8s %6d  (%.1f%%)" % (status, count, count / total * 100))

    linked = counts.get(LINKED, 0)
    if linked / total < 0.5:
        lines.append("")
        lines.append("  Fewer than half of all products are linked. If the two "
                     "catalogues are meant to hold the same inventory, they are "
                     "not. Try --by title before concluding that: divergent SKU "
                     "conventions produce exactly this result on catalogues that "
                     "are in fact identical.")

    for status in (REVIEW, A_ONLY, B_ONLY):
        examples = [r for r in rows if r.status == status][:sample]
        if examples:
            lines.append("")
            lines.append("  %s, first %d:" % (status, len(examples)))
            for row in examples:
                lines.append("    %-28s %s" % (row.key[:28],
                                               (row.a_title or row.b_title)[:50]))
    return "\n".join(lines)


def write_csv(rows: list[Row], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["status", "key", "a_title", "b_title", "score"])
        for row in rows:
            writer.writerow([row.status, row.key, row.a_title, row.b_title,
                             row.score or ""])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reconcile",
        description="Compare two platform catalogues and report linkage.")
    parser.add_argument("--a", required=True, help="catalogue A export")
    parser.add_argument("--b", required=True, help="catalogue B export")
    parser.add_argument("--by", choices=("sku", "title"), default="sku",
                        help="join on SKU (exact) or normalised title (fuzzy)")
    parser.add_argument("--threshold", type=float, default=REVIEW_THRESHOLD,
                        help="title score below which a match is REVIEW")
    parser.add_argument("--out", default="reconciliation.csv")
    parser.add_argument("--dry-run", action="store_true",
                        help="print counts and a 3-row sample, write nothing")
    return parser


def run(catalogue_a: list[Product], catalogue_b: list[Product],
        by: str = "sku", threshold: float = REVIEW_THRESHOLD) -> list[Row]:
    if by == "sku":
        return reconcile_by_sku(catalogue_a, catalogue_b)
    return reconcile_by_title(catalogue_a, catalogue_b, threshold)


def main(argv=None) -> int:
    from .readers import read_catalogue

    args = build_parser().parse_args(argv)
    catalogue_a = read_catalogue(args.a)
    catalogue_b = read_catalogue(args.b)
    print("rows in: A %d, B %d" % (len(catalogue_a), len(catalogue_b)))

    rows = run(catalogue_a, catalogue_b, args.by, args.threshold)
    print(summarise(rows))

    if args.dry_run:
        print("\n--dry-run: nothing written. First 3 rows:")
        for row in rows[:3]:
            print("   ", row)
        return 0

    write_csv(rows, args.out)
    print("\nrows out: %d -> %s" % (len(rows), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
