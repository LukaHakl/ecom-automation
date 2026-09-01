"""Vela CSV schema and writer, for bulk-creating Etsy listings.

Etsy has no native bulk import for *new* listings, so Vela's CSV is the target
format for any catalogue build. Almost every rule in this module was learned by
having an import fail, usually silently, so they are hard requirements rather
than style preferences.

The two traps that cost the most time
-------------------------------------

**The photo column trap.** Etsy's own *export* names image columns
``IMAGE1``..``IMAGE10``. Vela's *import* expects ``Photo 1``..``Photo 10`` --
the word "Photo", a space, then the number. Feed Vela a file with ``IMAGE1`` or
``Image 1`` headers and the import **succeeds with zero photos attached**. No
error, no warning, just a catalogue of listings with no pictures. Hence
:func:`normalise_photo_columns`, which accepts all three spellings, and
:func:`photo_coverage`, which reports what fraction of rows actually carry a
photo so the failure cannot pass unnoticed.

**The variation row layout.** Listings with variations are a parent row plus
child rows, and four rules break the import when violated. They are enforced
here in :func:`build_rows` rather than left to callers, because every caller
that has been trusted to get them right has eventually got them wrong:

1. The first variation is **embedded in the parent row**, not written as its
   own row. A parent plus N variation rows yields N+1 variations on Etsy, and
   the phantom extra is the parent's own values.
2. Every child row **repeats both axis label fields**. Leaving them blank on
   child rows fails the import.
3. Parent rows have every ``Var*`` field **explicitly written**, empty where
   they should be empty. Building rows by mutating one shared dict lets stale
   values from the previous listing leak into the next parent -- which produces
   a valid-looking file that imports the wrong data.
4. ``Var Visibility`` is the literal string ``On``. Not ``show``, not ``TRUE``,
   not ``1``.

Values are human-readable strings, not underscore-coded enums for every field.
``i_did`` and ``made_to_order`` are correct as written because Etsy defines
those two that way; do not "helpfully" convert other fields into similar coded
forms. A reference CSV from another shop once suggested otherwise and it was
wrong -- a direct Etsy export settled it.

What cannot be imported at all
------------------------------
Custom buyer-input option lists ("Name to write", "Font colour") cannot be set
through Vela's CSV. They are applied after import via Vela Profiles. Rather
than dropping that information silently, :func:`write_post_import_todo` emits a
file naming which listings need which profile.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field

MAX_PHOTOS = 10

# --- defaults for 3D-printed made-to-order goods ---------------------------
#: Etsy's coded value for "I made it". One of the two fields that genuinely is
#: an underscore-coded enum.
WHO_MADE = "i_did"
#: Likewise coded. Made-to-order, not a finished item held in stock.
WHEN_MADE = "made_to_order"
#: Toys & Games > Miniatures & Figurines.
TAXONOMY_ID = "1239"

#: Vela applies the shop default when this is blank, which is what you want --
#: naming a profile here means every listing breaks when the profile is renamed.
SHIPPING_PROFILE = ""
PRODUCTION_PARTNER_IDS = ""

#: Rule 4. The literal string, exactly this.
VAR_VISIBILITY_ON = "On"

BASE_COLUMNS = [
    "TITLE", "DESCRIPTION", "PRICE", "CURRENCY_CODE", "QUANTITY", "TAGS",
    "MATERIALS", "WHO_MADE", "WHEN_MADE", "TAXONOMY_ID", "SHIPPING_PROFILE",
    "PRODUCTION_PARTNER_IDS",
]
PHOTO_COLUMNS = ["Photo %d" % i for i in range(1, MAX_PHOTOS + 1)]
VARIATION_COLUMNS = [
    "VARIATION 1 TYPE", "VARIATION 1 NAME", "VARIATION 1 VALUES",
    "VARIATION 2 TYPE", "VARIATION 2 NAME", "VARIATION 2 VALUES",
]
VAR_COLUMNS = ["Var Visibility", "Var Price", "Var Quantity", "Var SKU"]
COLUMNS = BASE_COLUMNS + PHOTO_COLUMNS + VARIATION_COLUMNS + VAR_COLUMNS + ["SKU"]

#: Accepts Vela's own spelling, Etsy's export spelling, and the mixed-case
#: variant that turns up in hand-edited files.
_PHOTO_HEADER = re.compile(r"^\s*(?:photo|image)\s*_?(\d{1,2})\s*$", re.I)


def photo_column_index(name: str) -> int | None:
    """Slot number if `name` is any spelling of a photo column, else None."""
    match = _PHOTO_HEADER.match(name or "")
    return int(match.group(1)) if match else None


def normalise_photo_columns(header: list[str]) -> list[str]:
    """Rewrite any accepted photo-column spelling to Vela's ``Photo N``.

    Non-photo columns pass through untouched. This is the single most
    load-bearing function in the module: getting it wrong produces an import
    that reports success and attaches no images.
    """
    out = []
    for name in header:
        slot = photo_column_index(name)
        out.append("Photo %d" % slot if slot else name)
    return out


def photo_coverage(rows: list[dict]) -> tuple[int, int, float]:
    """(rows_with_a_photo, total_rows, fraction).

    Only parent rows are counted -- child variation rows legitimately carry no
    photos, and including them would drag the fraction down and make a healthy
    file look broken.
    """
    parents = [r for r in rows if r.get("TITLE")]
    if not parents:
        return 0, 0, 0.0
    with_photo = sum(
        1 for r in parents
        if any((r.get(c) or "").strip() for c in PHOTO_COLUMNS)
    )
    return with_photo, len(parents), with_photo / len(parents)


class PhotoCoverageWarning(UserWarning):
    """Raised as a warning when too few listings carry a photo."""


def check_photo_coverage(rows: list[dict], minimum: float = 0.95) -> str | None:
    """Return a loud message if photo coverage is below `minimum`, else None."""
    with_photo, total, fraction = photo_coverage(rows)
    if total and fraction < minimum:
        return (
            "WARNING: only %d of %d listings (%.0f%%) have at least one photo. "
            "Vela imports a file with the wrong photo column names successfully "
            "and silently attaches nothing -- check the source header spells "
            "them 'Photo 1'..'Photo 10' before uploading."
            % (with_photo, total, fraction * 100)
        )
    return None


# ---------------------------------------------------------------------------
# Listing model
# ---------------------------------------------------------------------------

@dataclass
class Axis:
    """One variation axis, e.g. type="Size", name="Size"."""

    type: str
    name: str


@dataclass
class Variation:
    """One combination of axis values, with its own price/quantity/SKU."""

    value_1: str
    value_2: str = ""
    price: str = ""
    quantity: str = ""
    sku: str = ""


@dataclass
class Listing:
    """One Etsy listing. `variations` may be empty."""

    title: str
    description: str = ""
    price: str = ""
    currency_code: str = "EUR"
    quantity: str = "100"
    tags: list[str] = field(default_factory=list)
    materials: list[str] = field(default_factory=list)
    photos: list[str] = field(default_factory=list)
    sku: str = ""
    axis_1: Axis | None = None
    axis_2: Axis | None = None
    variations: list[Variation] = field(default_factory=list)
    #: Names of Vela Profiles that must be applied by hand after import.
    profiles: list[str] = field(default_factory=list)


def blank_row() -> dict:
    """A fresh row with every column present and empty.

    Rule 3 lives here. Every row is built from a new dict rather than by
    mutating a shared one, so a value can never survive from the previous
    listing into this one. That bug produces a file that looks correct and
    imports the wrong data, which is the worst combination available.
    """
    return {column: "" for column in COLUMNS}


def build_rows(listing: Listing) -> list[dict]:
    """Expand one listing into its parent row plus any child variation rows.

    Rule 1: the first variation is embedded in the parent, so N variations
    produce N rows in total, not N+1.
    Rule 2: child rows repeat both axis label fields.
    Rule 4: Var Visibility is the string "On" on every row that carries one.
    """
    parent = blank_row()
    parent.update({
        "TITLE": listing.title,
        "DESCRIPTION": listing.description,
        "PRICE": listing.price,
        "CURRENCY_CODE": listing.currency_code,
        "QUANTITY": listing.quantity,
        "TAGS": ",".join(listing.tags),
        "MATERIALS": ",".join(listing.materials),
        "WHO_MADE": WHO_MADE,
        "WHEN_MADE": WHEN_MADE,
        "TAXONOMY_ID": TAXONOMY_ID,
        "SHIPPING_PROFILE": SHIPPING_PROFILE,
        "PRODUCTION_PARTNER_IDS": PRODUCTION_PARTNER_IDS,
        "SKU": listing.sku,
    })
    for index, url in enumerate(listing.photos[:MAX_PHOTOS], start=1):
        parent["Photo %d" % index] = url

    if not listing.variations:
        # Every VARIATION*/Var* column stays exactly as blank_row() left it.
        return [parent]

    if listing.axis_1 is None:
        raise ValueError(
            "listing %r has variations but no axis_1; the axis labels are "
            "required on every row and cannot be inferred from the values."
            % listing.title
        )

    labels = _axis_labels(listing)
    first, rest = listing.variations[0], listing.variations[1:]

    parent.update(labels)
    parent.update(_variation_cells(listing, first))

    rows = [parent]
    for variation in rest:
        child = blank_row()          # fresh dict: rule 3
        child.update(labels)         # rule 2: both axes repeated
        child.update(_variation_cells(listing, variation))
        rows.append(child)
    return rows


def _axis_labels(listing: Listing) -> dict:
    """The axis TYPE/NAME cells, repeated identically on every row."""
    labels = {
        "VARIATION 1 TYPE": listing.axis_1.type,
        "VARIATION 1 NAME": listing.axis_1.name,
    }
    if listing.axis_2 is not None:
        labels["VARIATION 2 TYPE"] = listing.axis_2.type
        labels["VARIATION 2 NAME"] = listing.axis_2.name
    return labels


def _variation_cells(listing: Listing, variation: Variation) -> dict:
    """The per-combination value cells."""
    cells = {
        "VARIATION 1 VALUES": variation.value_1,
        "Var Visibility": VAR_VISIBILITY_ON,
        "Var Price": variation.price,
        "Var Quantity": variation.quantity,
        "Var SKU": variation.sku,
    }
    if listing.axis_2 is not None:
        cells["VARIATION 2 VALUES"] = variation.value_2
    return cells


def write_vela_csv(listings: list[Listing], path: str) -> list[dict]:
    """Write a Vela import CSV. Returns the rows written, for inspection."""
    rows = [row for listing in listings for row in build_rows(listing)]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def write_post_import_todo(listings: list[Listing], path: str) -> dict[str, list[str]]:
    """Emit the manual follow-up list, grouped by profile.

    Custom buyer-input options cannot be set through the CSV at all. Grouping by
    profile rather than by listing is deliberate: the post-import workflow is to
    search the listing set for one shared keyword and bulk-apply a profile to
    everything it returns, so the useful unit is the profile, not the product.
    """
    by_profile: dict[str, list[str]] = {}
    for listing in listings:
        for profile in listing.profiles:
            by_profile.setdefault(profile, []).append(listing.title)

    with open(path, "w", encoding="utf-8") as handle:
        if not by_profile:
            handle.write("No Vela Profiles needed for this batch.\n")
            return by_profile
        handle.write(
            "Vela Profiles to apply by hand after import.\n"
            "Custom buyer-input options (name-to-write fields, colour pickers)\n"
            "cannot be set through the CSV -- these are not optional polish,\n"
            "the listings are incomplete until they are applied.\n\n"
        )
        for profile, titles in sorted(by_profile.items()):
            handle.write("%s  (%d listings)\n" % (profile, len(titles)))
            for title in titles:
                handle.write("    %s\n" % title)
            handle.write("\n")
    return by_profile
