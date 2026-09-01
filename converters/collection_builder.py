"""Assign a list of products to a Shopify collection.

Two output modes, because the right one depends on how the store's collections
are configured and getting it wrong produces a file that imports cleanly and
does nothing:

``--mode smart-tags`` (preferred)
    Emits an Altera file adding the tag that the target Smart Collection's rule
    selects on, with ``Tags Command = REPLACE``. Membership then follows from
    the rule, which means it stays correct when the rule changes and it cannot
    reference a collection that does not exist.

``--mode manual``
    Emits a manual collection-membership file, for stores whose collection is
    hand-curated rather than rule-driven.

Read ``common/altera.py`` before changing this. A previous version wrote
invented collection names into a manual column, and both the mechanism and the
names were wrong.

Input hygiene
-------------
Bestseller exports contain junk. A row carrying a bare quantity with no title
was observed in a real top-500 export. Those rows are validated out and
reported rather than emitted as empty products -- an empty product in an Altera
file will happily blank a real product's title on import.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field

from common.altera import COLUMNS, MERGE, REPLACE

SMART_TAGS, MANUAL = "smart-tags", "manual"

MANUAL_COLUMNS = ["Handle", "Title", "Collection", "Command"]


@dataclass
class InputRow:
    title: str = ""
    handle: str = ""
    quantity: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class LoadResult:
    rows: list[InputRow] = field(default_factory=list)
    skipped: list[tuple[int, str]] = field(default_factory=list)

    def note_skip(self, line: int, reason: str) -> None:
        self.skipped.append((line, reason))


def load_products(handle, title_field: str = "Title") -> LoadResult:
    """Read a bestseller-style export, validating as it goes.

    A row with no usable title is junk, whatever else it carries. Reported with
    its line number so it can be found in the source file.
    """
    result = LoadResult()
    reader = csv.DictReader(handle)
    for line, row in enumerate(reader, start=2):     # line 1 is the header
        title = ""
        for candidate in (title_field, "Title", "TITLE", "title", "Product"):
            title = (row.get(candidate) or "").strip()
            if title:
                break

        if not title:
            quantity = next((v for v in row.values() if (v or "").strip()), "")
            result.note_skip(line, "no title (row carried only %r)" % quantity[:30])
            continue

        result.rows.append(InputRow(
            title=title,
            handle=(row.get("Handle") or "").strip(),
            quantity=(row.get("Quantity") or row.get("QUANTITY") or "").strip(),
            tags=[t.strip() for t in (row.get("Tags") or "").split(",") if t.strip()],
        ))
    return result


def build_smart_tag_rows(rows: list[InputRow], tag: str) -> list[dict]:
    """Altera rows adding `tag`, with Tags Command = REPLACE.

    REPLACE writes the full tag set, so existing tags must be carried through
    or they are destroyed. That is why :class:`InputRow` reads the current tags
    rather than only the title -- a REPLACE that drops a product's other tags
    also drops it out of every other Smart Collection it belonged to.
    """
    out = []
    for row in rows:
        tags = list(row.tags)
        if tag not in tags:
            tags.append(tag)
        record = {column: "" for column in COLUMNS}
        record.update({
            "Handle": row.handle or _slug(row.title),
            "Command": MERGE,
            "Title": row.title,
            "Tags": ", ".join(tags),
            "Tags Command": REPLACE,
            "Top Row": "TRUE",
        })
        out.append(record)
    return out


def build_manual_rows(rows: list[InputRow], collection: str) -> list[dict]:
    """Manual collection-membership rows."""
    return [{
        "Handle": row.handle or _slug(row.title),
        "Title": row.title,
        "Collection": collection,
        "Command": MERGE,
    } for row in rows]


def _slug(title: str) -> str:
    import re
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    return re.sub(r"[\s_-]+", "-", slug).strip("-")


def write_rows(rows: list[dict], columns: list[str], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def summary(load: LoadResult, rows: list[dict], mode: str, target: str) -> str:
    lines = ["", "=== Collection builder ===",
             "mode:         %s" % mode,
             "target:       %s" % target,
             "rows in:      %d" % (len(load.rows) + len(load.skipped)),
             "rows out:     %d" % len(rows),
             "rows skipped: %d" % len(load.skipped)]
    if load.skipped:
        lines += ["", "Skipped rows -- junk in the source export:"]
        for line, reason in load.skipped:
            lines.append("  line %-5d %s" % (line, reason))
    if mode == SMART_TAGS:
        lines += ["", "Tags Command = REPLACE writes the complete tag set, so "
                      "existing tags were carried through. If a product loses "
                      "an unrelated collection after import, that is where to "
                      "look."]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="collection_builder",
        description="Assign a product list to a Shopify collection.")
    parser.add_argument("--source", required=True,
                        help="product list, e.g. a bestsellers export")
    parser.add_argument("--mode", choices=(SMART_TAGS, MANUAL), default=SMART_TAGS,
                        help="smart-tags writes the tag a Smart Collection rule "
                             "selects on; manual writes membership directly")
    parser.add_argument("--tag", help="tag to add (--mode smart-tags)")
    parser.add_argument("--collection", help="collection name (--mode manual)")
    parser.add_argument("--out", default="collection_rows.csv")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.mode == SMART_TAGS and not args.tag:
        print("--mode smart-tags needs --tag: the tag the target Smart "
              "Collection's rule selects on.", file=sys.stderr)
        return 2
    if args.mode == MANUAL and not args.collection:
        print("--mode manual needs --collection.", file=sys.stderr)
        return 2

    with open(args.source, newline="", encoding="utf-8-sig") as handle:
        load = load_products(handle)

    if args.mode == SMART_TAGS:
        rows, columns, target = build_smart_tag_rows(load.rows, args.tag), COLUMNS, args.tag
    else:
        rows, columns, target = (build_manual_rows(load.rows, args.collection),
                                 MANUAL_COLUMNS, args.collection)

    print(summary(load, rows, args.mode, target))

    if args.dry_run:
        print("\n--dry-run: nothing written. First 3 rows:")
        for row in rows[:3]:
            print("   ", {k: v for k, v in row.items() if v})
        return 0

    write_rows(rows, columns, args.out)
    print("\nWrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
