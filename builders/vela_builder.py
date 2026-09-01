"""Turn a structured product definition into a correct Vela import.

Almost all of this tool's difficulty is in two places, and both fail *silently*
when wrong: the variation row layout (delegated to :mod:`common.vela`, which
enforces it) and photo slot ordering.

Photo order is a merchandising decision
---------------------------------------
Not an implementation detail, and not alphabetical. The established layout::

    slot 1      AI-rendered scene shot
    slots 2-3   original photos 1-2
    slots 4-6   infographics (for listings that have them)
    slots 7-9   original photos 3-6
    slot 10     original cover photo

Listings without infographics **collapse the gap** rather than leaving empty
slots -- Etsy renders an empty slot as a missing image, not as a skipped one.
The layout lives in config so it can be changed without touching code.

Combination counts explode
--------------------------
Two axes multiply. One real listing reached 88 combinations before being
trimmed to 64 by dropping an outlier quantity tier and three low-demand colour
pairings. So trimming is declared in the input as an ``exclude`` list, never
done by hand in the CSV afterwards -- a hand-edited CSV cannot be rebuilt, and
every rebuild silently reintroduces what was trimmed.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from common.vela import Axis, Listing, Variation

#: Etsy's hard ceiling.
MAX_PHOTOS = 10

#: Warn above this many combinations. Not an error -- Etsy's own limit is
#: higher -- but past this a listing becomes unmanageable for the shopper.
VARIATION_CEILING = 70

#: The default slot layout, overridable from config. Each entry names a photo
#: group and how many slots it may occupy.
#: Slots 1, 2-3, 4-6, 7-9, 10 -- ten in total, which is Etsy's limit.
#:
#: The written spec described the fourth block as "slots 7-9, original photos
#: 3-6": three slots, four photos. Three wins, because ten is a hard ceiling and
#: the alternative silently pushes the cover shot off the end. The consequence
#: is that a listing with six originals places five of them, and the sixth is
#: counted as dropped rather than vanishing.
DEFAULT_PHOTO_LAYOUT = [
    ("ai_scene", 1),
    ("originals", 2),
    ("infographics", 3),
    ("originals", 3),
    ("cover", 1),
]


@dataclass
class ProductSpec:
    """One listing as declared in the products YAML."""

    sku: str
    title: str
    description: str = ""
    base_price: float = 0.0
    quantity: str = "100"
    tags: list[str] = field(default_factory=list)
    materials: list[str] = field(default_factory=list)
    axis_1: dict | None = None
    axis_2: dict | None = None
    exclude: list[list[str]] = field(default_factory=list)
    photos: dict = field(default_factory=dict)
    video: str = ""
    profiles: list[str] = field(default_factory=list)


def final_price(base: float, multiplier: float) -> str:
    """Apply the shop's fixed multiplier, in one place.

    Kept as a single function so the arithmetic is auditable: the build summary
    prints base and final side by side, and a wrong multiplier is visible rather
    than baked invisibly into every row.
    """
    return "%.2f" % round(base * multiplier, 2)


def order_photos(groups: dict, layout=None, max_photos: int = MAX_PHOTOS
                 ) -> tuple[list[str], int]:
    """Lay photo groups into slots. Returns (urls, dropped_count).

    Groups are consumed in layout order, taking up to the declared number of
    slots from each. A group that is absent or short simply contributes fewer
    photos and everything after it moves up -- that is the "collapse the gap"
    rule, and it falls out of appending rather than assigning to fixed indices.

    ``dropped`` counts every supplied photo that did **not** make it into a
    slot, for either reason: the layout had no room for it, or it fell past
    Etsy's ten. Both are reported because a photo that was handed to the
    builder and silently discarded is precisely the class of failure this
    repository exists to make visible.
    """
    layout = layout or DEFAULT_PHOTO_LAYOUT
    consumed: dict[str, int] = {}
    urls: list[str] = []

    def as_list(value):
        return [value] if isinstance(value, str) else (value or [])

    for name, slots in layout:
        available = as_list(groups.get(name))
        start = consumed.get(name, 0)
        take = available[start:start + slots]
        consumed[name] = start + len(take)
        urls.extend(take)

    placed = urls[:max_photos]
    supplied = sum(len(as_list(value)) for value in groups.values())
    return placed, supplied - len(placed)


def combinations(spec: ProductSpec) -> list[tuple[str, str]]:
    """Every (value_1, value_2) pair, minus the declared exclusions.

    Exclusions are matched as pairs, so ``[["XL", "Gold"]]`` removes exactly
    that combination rather than every XL or every Gold.
    """
    values_1 = list((spec.axis_1 or {}).get("values", []))
    if not values_1:
        return []
    values_2 = list((spec.axis_2 or {}).get("values", []))

    excluded = {tuple(pair) for pair in spec.exclude}
    if not values_2:
        return [(v, "") for v in values_1 if (v,) not in excluded
                and (v, "") not in excluded]

    return [(a, b) for a, b in itertools.product(values_1, values_2)
            if (a, b) not in excluded]


def build_listing(spec: ProductSpec, multiplier: float,
                  photo_layout=None) -> tuple[Listing, dict]:
    """Turn a spec into a :class:`common.vela.Listing` plus a summary dict."""
    photos, truncated = order_photos(spec.photos, photo_layout)
    price = final_price(spec.base_price, multiplier)
    pairs = combinations(spec)

    listing = Listing(
        title=spec.title,
        description=spec.description,
        price=price,
        quantity=spec.quantity,
        tags=spec.tags,
        materials=spec.materials,
        photos=photos,
        sku=spec.sku,
        profiles=spec.profiles,
    )

    if pairs:
        listing.axis_1 = Axis(type=spec.axis_1.get("type", ""),
                              name=spec.axis_1.get("name", ""))
        if spec.axis_2:
            listing.axis_2 = Axis(type=spec.axis_2.get("type", ""),
                                  name=spec.axis_2.get("name", ""))
        listing.variations = [
            Variation(value_1=a, value_2=b, price=price, quantity=spec.quantity,
                      sku="%s-%s" % (spec.sku, len(listing.variations) + index + 1))
            for index, (a, b) in enumerate(pairs)
        ]

    summary = {
        "sku": spec.sku,
        "title": spec.title,
        "variations": len(pairs),
        "photos": len(photos),
        "photos_truncated": truncated,
        "base_price": "%.2f" % spec.base_price,
        "final_price": price,
        "video_mapped": bool(spec.video),
        "over_ceiling": len(pairs) > VARIATION_CEILING,
    }
    return listing, summary


def format_summary(summaries: list[dict]) -> str:
    """Per-listing build summary: variations, photos, price, video."""
    lines = ["", "=== Build summary ===",
             "%-22s %5s %6s %9s %9s %5s" % ("SKU", "vars", "photos",
                                            "base", "final", "video")]
    for row in summaries:
        lines.append("%-22s %5d %6d %9s %9s %5s"
                     % (row["sku"][:22], row["variations"], row["photos"],
                        row["base_price"], row["final_price"],
                        "yes" if row["video_mapped"] else "-"))
        if row["over_ceiling"]:
            lines.append("    WARNING: %d variations exceeds the ceiling of %d. "
                         "Trim with an 'exclude' list in the input rather than "
                         "by editing the CSV." % (row["variations"],
                                                  VARIATION_CEILING))
        if row["photos_truncated"]:
            lines.append("    WARNING: %d photos supplied but not placed. The "
                         "slot layout has room for %d; anything beyond that, or "
                         "beyond a group's allotted slots, is not uploaded."
                         % (row["photos_truncated"], MAX_PHOTOS))

    total_vars = sum(r["variations"] for r in summaries)
    lines.append("")
    lines.append("%d listings, %d variation rows." % (len(summaries), total_vars))
    return "\n".join(lines)
