"""Convert an Etsy listings export into eBay's bulk-listing template.

The template is the awkward part. eBay hands you a ~93-column CSV whose shape
is not a plain header-plus-rows file:

    Info,Version=1.0.0,Template=fx_category_template...
    Info,Action and Category ID are required...
    *Action(SiteID=US|Country=US|Currency=USD|Version=1193),*Category,Title,...
    Add,12345,My Product,...

Those leading ``Info`` rows are not decoration -- they carry the template's
category and site context, and eBay rejects a file without them. They are
copied through **verbatim**, including any trailing commas, because eBay's
parser is unforgiving about the exact byte content of the preamble.

The header is read from **the user's own downloaded template**, never
hardcoded. eBay revises the column set, and a hardcoded 93-column list is a
time bomb that goes off silently the next time they add a field.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field

from .product import Product, first_sku
from .readers import read_catalogue

#: eBay's image column. Observed at index 40 in one template, but resolved by
#: name everywhere in this module -- see the module docstring on revisions.
PIC_URL = "PicURL"

#: Default keyword rules. Real ones belong in config; these document the shape
#: and the ordering rule. First match wins, so more specific rules go first and
#: the fallback goes last.
DEFAULT_CATEGORY_RULES = [
    {"keywords": ["ww2", "wwii", "tank", "panzer", "sherman", "artillery",
                  "halftrack", "stug"],
     "category": "Model Vehicle"},
    {"keywords": ["sci-fi", "scifi", "space", "marine", "mech", "cyber",
                  "alien", "laser"],
     "category": "Sci-Fi Miniature"},
]
#: Applied when nothing matches. Fantasy is the largest bucket in this
#: catalogue, so it is the fallback rather than a rule.
DEFAULT_CATEGORY_FALLBACK = "Fantasy Miniature"


@dataclass
class Template:
    """A parsed eBay bulk template: preamble, header, and nothing else."""

    info_rows: list[list[str]] = field(default_factory=list)
    header: list[str] = field(default_factory=list)

    def has_column(self, name: str) -> bool:
        return name in self.header


def read_template(path: str) -> Template:
    """Read the preamble and header out of a downloaded eBay template.

    The header is the first row that is not an ``Info`` row. Everything before
    it is preamble and is preserved exactly as read.
    """
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))

    template = Template()
    for row in rows:
        if row and row[0].strip().lower() == "info":
            template.info_rows.append(row)
            continue
        if row:
            template.header = row
            break

    if not template.header:
        raise ValueError(
            "%s has no header row. Expected one or more 'Info,...' rows "
            "followed by the column header -- download a fresh bulk template "
            "from eBay's Seller Hub rather than editing an old one." % path
        )
    return template


def categorise(title: str, rules: list[dict] | None = None,
               fallback: str = DEFAULT_CATEGORY_FALLBACK) -> str:
    """Keyword-driven category assignment. First matching rule wins."""
    text = (title or "").lower()
    for rule in (rules if rules is not None else DEFAULT_CATEGORY_RULES):
        if any(keyword.lower() in text for keyword in rule["keywords"]):
            return rule["category"]
    return fallback


def convert_price(value: str, rate: float) -> str:
    """EUR -> USD at a supplied rate.

    The rate is a parameter, never a constant. The original used 1.08, and a
    hardcoded rate silently misprices an entire catalogue the moment it drifts.
    :func:`summary` records the rate and the date it was set alongside the
    counts, so a file can be traced back to the assumption that priced it.
    """
    try:
        return "%.2f" % round(float(str(value).replace(",", ".")) * rate, 2)
    except (TypeError, ValueError):
        return ""


@dataclass
class ConversionResult:
    rows: list[dict] = field(default_factory=list)
    skipped_no_image: list[str] = field(default_factory=list)
    skipped_duplicate: list[str] = field(default_factory=list)
    category_counts: dict[str, int] = field(default_factory=dict)

    @property
    def total_skipped(self) -> int:
        return len(self.skipped_no_image) + len(self.skipped_duplicate)


def convert(products: list[Product], template: Template, rate: float,
            rules: list[dict] | None = None,
            fallback: str = DEFAULT_CATEGORY_FALLBACK,
            limit: int | None = None) -> ConversionResult:
    """Map Products onto eBay template rows.

    Two skip conditions, both reported rather than silently applied:

    - **No image.** eBay rejects an listing without one, so a row with an empty
      PicURL fails the whole import rather than just itself.
    - **Duplicate SKU.** Etsy exports repeat rows per variation, so the same
      product arrives many times. Deduplication is on the *first* SKU, since
      the field can hold a comma-separated list.
    """
    result = ConversionResult()
    seen: set[str] = set()

    for product in products:
        if limit is not None and len(result.rows) >= limit:
            break

        key = first_sku(product.sku) or product.title
        if key in seen:
            result.skipped_duplicate.append(product.title)
            continue
        seen.add(key)

        if not product.images:
            result.skipped_no_image.append(product.title)
            continue

        category = categorise(product.title, rules, fallback)
        result.category_counts[category] = result.category_counts.get(category, 0) + 1

        row = {column: "" for column in template.header}
        _set(row, template, "Title", product.title[:80])   # eBay's title cap
        _set(row, template, "Description", product.description)
        _set(row, template, "CustomLabel", key)
        _set(row, template, PIC_URL, "|".join(product.images))
        _set(row, template, "StartPrice", convert_price(product.price, rate))
        _set(row, template, "Quantity", product.quantity or "1")
        _set(row, template, "Category", category)
        # The Action column's real name carries site parameters, so match on
        # prefix rather than equality.
        for column in template.header:
            if column.lstrip("*").startswith("Action"):
                row[column] = "Add"
                break
        result.rows.append(row)

    return result


def _set(row: dict, template: Template, name: str, value: str) -> None:
    """Write a value into whichever column matches `name`.

    eBay prefixes required columns with ``*`` and appends parameters in
    brackets, so exact equality misses ``*Title`` and
    ``*Action(SiteID=US|...)``. Matching by normalised prefix keeps this working
    across template revisions.
    """
    target = name.lower()
    for column in template.header:
        cleaned = column.lstrip("*").split("(")[0].strip().lower()
        if cleaned == target:
            row[column] = value
            return


def write_ebay_csv(result: ConversionResult, template: Template, path: str) -> None:
    """Write preamble, header and rows in eBay's expected order."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        for info in template.info_rows:
            writer.writerow(info)          # verbatim -- eBay rejects edits here
        writer.writerow(template.header)
        for row in result.rows:
            writer.writerow([row.get(column, "") for column in template.header])


def summary(result: ConversionResult, products_in: int, rate: float,
            rate_date: str) -> str:
    lines = ["", "=== Etsy -> eBay ===",
             "rows in:      %d" % products_in,
             "rows out:     %d" % len(result.rows),
             "rows skipped: %d" % result.total_skipped,
             "  no image:   %d  (eBay rejects these)" % len(result.skipped_no_image),
             "  duplicate:  %d  (Etsy repeats rows per variation)"
             % len(result.skipped_duplicate),
             "",
             "EUR -> USD at %.4f, rate set %s" % (rate, rate_date),
             "",
             "categories:"]
    for category, count in sorted(result.category_counts.items(),
                                  key=lambda kv: -kv[1]):
        lines.append("  %-22s %5d" % (category, count))
    lines.append("")
    lines.append("Check those counts. Keyword categorisation is the step that "
                 "misclassifies quietly, and the count per category is the only "
                 "place it shows.")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="etsy_to_ebay",
        description="Convert an Etsy export into eBay's bulk-listing template.")
    parser.add_argument("--source", required=True, help="Etsy export")
    parser.add_argument("--template", required=True,
                        help="eBay bulk template downloaded from Seller Hub")
    parser.add_argument("--out", default="ebay_upload.csv")
    parser.add_argument("--rate", type=float, required=True,
                        help="EUR->USD rate. Required on purpose: a default "
                             "here would silently misprice a catalogue")
    parser.add_argument("--rate-date", default="unspecified",
                        help="the date the rate was taken, recorded in the summary")
    parser.add_argument("--limit", type=int,
                        help="stop after N rows. Validate on 100 before "
                             "attempting 5,000 -- eBay import failures on a "
                             "large file are miserable to diagnose")
    parser.add_argument("--dry-run", action="store_true",
                        help="print counts and a 3-row sample, write nothing")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    products = read_catalogue(args.source)
    template = read_template(args.template)

    if not template.has_column(PIC_URL):
        print("WARNING: the template has no %r column. Images will not be "
              "attached. Check you downloaded a product template rather than "
              "a category or inventory one." % PIC_URL, file=sys.stderr)

    result = convert(products, template, args.rate, limit=args.limit)
    print(summary(result, len(products), args.rate, args.rate_date))

    if args.dry_run:
        print("\n--dry-run: nothing written. First 3 rows:")
        for row in result.rows[:3]:
            print("   ", {k: v for k, v in row.items() if v})
        return 0

    write_ebay_csv(result, template, args.out)
    print("\nWrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
