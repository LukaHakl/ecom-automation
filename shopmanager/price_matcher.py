"""Stage 2: join a scraped marketplace library against an existing catalogue.

Stage 1 scrapes which Creator x 3D-object combinations the account owns. This
joins that list against an existing catalogue export to carry across titles and
prices, and stage 3 drives the form with the result.

Kept as its own stage on purpose. Stage 1 is slow and occasionally needs
rerunning; this one is instant and gets rerun constantly while tuning the
threshold. Fusing them would mean re-scraping the whole library every time a
number changes.

The false-positive problem
--------------------------
Fuzzy title matching produced false positives **across different creators** in a
related project -- two studios both selling a "Stone Golem" is not a coincidence
worth matching on. Two mitigations, both on by default:

1. Where the source side carries creator information, a match is only allowed
   within the same creator. This removes the entire class of cross-creator error
   rather than trying to score around it.
2. The lowest-scoring 20 matches are printed at the end for eyeball review.
   Threshold tuning without seeing the marginal cases is guesswork.

Three output files, and the split matters: the unmatched sets are how catalogue
gaps get found. ``unmatched_library`` is stock you own and are not selling;
``unmatched_source`` is listings with no owned asset behind them.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field

from common.progress import timestamp
from converters.product import normalise_title, title_similarity

#: Deliberately permissive. This feeds a stage that saves drafts, not live
#: listings, so a marginal match costs a draft to delete; a missed one costs a
#: product created by hand. Tune upward if the review list looks noisy.
DEFAULT_THRESHOLD = 0.60

#: How many of the weakest matches to print for review.
REVIEW_SAMPLE = 20


@dataclass
class LibraryItem:
    """One Creator x 3D-object pair the account owns."""

    creator: str
    creator_value: str
    object_text: str
    object_value: str


@dataclass
class SourceItem:
    """One row from the existing catalogue export."""

    title: str
    price: str = ""
    sku: str = ""
    creator: str = ""


@dataclass
class Match:
    creator: str
    creator_value: str
    object_text: str
    object_value: str
    title: str
    sku: str
    price: str
    match_score: float


@dataclass
class MatchResult:
    matched: list[Match] = field(default_factory=list)
    unmatched_library: list[LibraryItem] = field(default_factory=list)
    unmatched_source: list[SourceItem] = field(default_factory=list)


def read_library(handle) -> list[LibraryItem]:
    """Read stage 1's output.

    ``*_value`` is the dropdown's internal option value and is what stage 3
    selects on. Matching on display text alone is fragile -- the text is
    localised and occasionally re-worded, the value is not.
    """
    return [LibraryItem(
        creator=(row.get("creator") or "").strip(),
        creator_value=(row.get("creator_value") or "").strip(),
        object_text=(row.get("object_text") or "").strip(),
        object_value=(row.get("object_value") or "").strip(),
    ) for row in csv.DictReader(handle) if (row.get("object_text") or "").strip()]


def read_source(handle) -> list[SourceItem]:
    """Read an existing catalogue export (needs TITLE and PRICE)."""
    items = []
    for row in csv.DictReader(handle):
        title = next((( row.get(k) or "").strip()
                      for k in ("TITLE", "Title", "title")
                      if (row.get(k) or "").strip()), "")
        if not title:
            continue
        items.append(SourceItem(
            title=title,
            price=next(((row.get(k) or "").strip()
                        for k in ("PRICE", "Price", "price")
                        if (row.get(k) or "").strip()), ""),
            sku=next(((row.get(k) or "").strip()
                      for k in ("SKU", "Sku", "sku")
                      if (row.get(k) or "").strip()), ""),
            creator=next(((row.get(k) or "").strip()
                          for k in ("CREATOR", "Creator", "creator", "VENDOR")
                          if (row.get(k) or "").strip()), ""),
        ))
    return items


def match(library: list[LibraryItem], source: list[SourceItem],
          threshold: float = DEFAULT_THRESHOLD,
          same_creator_only: bool = True) -> MatchResult:
    """Join library items to source listings on normalised title similarity.

    Each source listing is claimed at most once, so two library objects with
    similar names cannot both take the same listing's price.
    """
    result = MatchResult()
    claimed: set[int] = set()

    for item in library:
        candidates = source
        if same_creator_only and item.creator:
            # Only constrain where the source actually carries creator data --
            # applying it to a source with the column blank would match nothing.
            with_creator = [s for s in source if s.creator]
            if with_creator:
                candidates = [s for s in with_creator
                              if normalise_title(s.creator) == normalise_title(item.creator)]

        best, best_score = None, 0.0
        for candidate in candidates:
            if id(candidate) in claimed:
                continue
            score = title_similarity(item.object_text, candidate.title)
            if score > best_score:
                best, best_score = candidate, score

        if best is not None and best_score >= threshold:
            claimed.add(id(best))
            result.matched.append(Match(
                creator=item.creator, creator_value=item.creator_value,
                object_text=item.object_text, object_value=item.object_value,
                title=best.title, sku=best.sku, price=best.price,
                match_score=round(best_score, 3),
            ))
        else:
            result.unmatched_library.append(item)

    result.unmatched_source = [s for s in source if id(s) not in claimed]
    return result


MATCH_COLUMNS = ["creator", "creator_value", "object_text", "object_value",
                 "title", "sku", "price", "match_score"]


def write_outputs(result: MatchResult, directory: str = ".",
                  stamp: str | None = None) -> dict[str, str]:
    """Write the three files. Returns {name: path}."""
    import os

    stamp = stamp or timestamp()
    paths = {
        "matched": os.path.join(directory, "mmf_matched_products_%s.csv" % stamp),
        "unmatched_library": os.path.join(
            directory, "mmf_unmatched_library_%s.csv" % stamp),
        "unmatched_source": os.path.join(
            directory, "mmf_unmatched_source_%s.csv" % stamp),
    }

    with open(paths["matched"], "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATCH_COLUMNS)
        writer.writeheader()
        for row in result.matched:
            writer.writerow(row.__dict__)

    with open(paths["unmatched_library"], "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["creator", "creator_value",
                                                    "object_text", "object_value"])
        writer.writeheader()
        for item in result.unmatched_library:
            writer.writerow(item.__dict__)

    with open(paths["unmatched_source"], "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["title", "price", "sku",
                                                    "creator"])
        writer.writeheader()
        for item in result.unmatched_source:
            writer.writerow(item.__dict__)

    return paths


def summary(result: MatchResult, sample: int = REVIEW_SAMPLE) -> str:
    total = len(result.matched) + len(result.unmatched_library)
    lines = ["", "=== Library <-> catalogue join ===",
             "library items:      %d" % total,
             "matched:            %d" % len(result.matched),
             "unmatched library:  %d  (owned, not listed -- catalogue gaps)"
             % len(result.unmatched_library),
             "unmatched source:   %d  (listed, no owned asset)"
             % len(result.unmatched_source)]

    weakest = sorted(result.matched, key=lambda m: m.match_score)[:sample]
    if weakest:
        lines += ["", "Weakest %d matches -- eyeball these before running "
                      "stage 3:" % len(weakest), ""]
        for row in weakest:
            lines.append("  %.2f  %-38s -> %s"
                         % (row.match_score, row.object_text[:38], row.title[:38]))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="price_matcher",
        description="Join a scraped library against an existing catalogue.")
    parser.add_argument("--library", required=True, help="stage 1 output CSV")
    parser.add_argument("--source", required=True,
                        help="existing catalogue export (TITLE, PRICE, [SKU])")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help="minimum title similarity to accept a match")
    parser.add_argument("--any-creator", action="store_true",
                        help="allow matches across creators. Off by default: "
                             "cross-creator false positives are the main "
                             "failure mode of fuzzy title matching here")
    parser.add_argument("--out-dir", default=".")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    with open(args.library, newline="", encoding="utf-8-sig") as handle:
        library = read_library(handle)
    with open(args.source, newline="", encoding="utf-8-sig") as handle:
        source = read_source(handle)
    print("library: %d items, source: %d listings" % (len(library), len(source)))

    result = match(library, source, args.threshold, not args.any_creator)
    print(summary(result))

    paths = write_outputs(result, args.out_dir)
    print("")
    for name, path in paths.items():
        print("  %-18s %s" % (name, path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
