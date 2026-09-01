"""Altera/Matrixify CSV schema and writer, for bulk-editing Shopify products.

Row model
---------
A product is a ``Handle``-grouped block of rows. The first row carries the
product and first-variant data; extra rows carry additional variants and
images. ``Top Row = TRUE`` marks the first row of each block and only that row.

Collections: tags, never a manual column
----------------------------------------
An earlier version of this pipeline wrote a manual ``Custom Collections`` column
containing invented collection names. Both the mechanism and the names were
wrong, and it took a re-import to find out.

The correct approach is to write **structured tags matching the store's existing
Smart Collection rules** and set ``Tags Command = REPLACE``. Collection
membership then follows automatically from the tags, which means it stays
correct when the store's rules change and it cannot invent a collection that
does not exist.

The tag-to-collection mapping is store-specific and belongs in ``config.yaml``,
never in code. :func:`resolve_collections` exists only to *report* which
collections a tag set will land in, so a build can be eyeballed before import --
it does not write them.

Vendor is not Creator
---------------------
``Vendor`` is always the parent shop name, from config. The model's original
designer is referenced as ``Creator:`` inside the description body. Conflating
these two has caused rework more than once.

Images
------
Shopify's media fetcher is bot-blocked by some CDNs. Where a source host is
known to be blocked, :func:`check_image_hosts` fails at build time rather than
producing a file that imports with missing media -- a loud failure now is much
cheaper than discovering half a catalogue has no pictures after the import.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field

COLUMNS = [
    "ID", "Handle", "Command", "Title", "Body HTML", "Vendor", "Tags",
    "Tags Command", "Smart Collections", "Variant SKU", "Variant Price",
    "Option1 Name", "Option1 Value", "Image Src", "Image Command",
    "Image Position", "Top Row",
]

MERGE = "MERGE"
REPLACE = "REPLACE"

#: CDNs Shopify's media fetcher cannot read. Adding a host here turns a silent
#: import-with-no-images into a build-time error.
BLOCKED_IMAGE_HOSTS = ["cults3d.com", "cdn.cults3d.com"]

#: Known-good: Etsy's CDN serves Shopify's fetcher without complaint.
KNOWN_GOOD_IMAGE_HOSTS = ["i.etsystatic.com"]

#: The store's description format. Compact on purpose.
#:
#: Generated descriptions have a strong pull toward marketing filler -- tariff
#: notices, "Features" sections, "Why Choose Us", emoji blocks. Resist it. The
#: template is literal, and anything added here has to earn its place against
#: the fact that nobody reads product copy.
DESCRIPTION_TEMPLATE = """{title} — by {creator}

Creator: {creator}
Scale: {scale}
Material: {material}
Category: {category}

{lead}"""


@dataclass
class Variant:
    sku: str = ""
    price: str = ""
    option_name: str = ""
    option_value: str = ""


@dataclass
class Product:
    handle: str
    title: str = ""
    body_html: str = ""
    vendor: str = ""
    tags: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    variants: list[Variant] = field(default_factory=list)
    product_id: str = ""


def render_description(title: str, creator: str, scale: str, material: str,
                       category: str, lead: str) -> str:
    """Fill the store's description template. No embellishment."""
    return DESCRIPTION_TEMPLATE.format(
        title=title, creator=creator, scale=scale,
        material=material, category=category, lead=lead.strip(),
    )


def blocked_hosts(urls: list[str],
                  blocked: list[str] | None = None) -> list[str]:
    """URLs whose host Shopify's fetcher cannot read."""
    hosts = blocked if blocked is not None else BLOCKED_IMAGE_HOSTS
    return [url for url in urls if any(host in url for host in hosts)]


class BlockedImageHost(ValueError):
    """A build would have produced a file with unfetchable images."""


def check_image_hosts(products: list[Product],
                      blocked: list[str] | None = None) -> None:
    """Raise if any product references a known-blocked image host.

    Fails the build rather than warning. A warning in a 5,000-line log is not
    seen; an import that silently drops media is found weeks later by a
    customer.
    """
    offenders = []
    for product in products:
        bad = blocked_hosts(product.images, blocked)
        if bad:
            offenders.append((product.handle, bad[0]))
    if offenders:
        raise BlockedImageHost(
            "%d product(s) reference an image host Shopify cannot fetch; "
            "rehost these before importing. First: %s -> %s"
            % (len(offenders), offenders[0][0], offenders[0][1])
        )


def resolve_collections(tags: list[str], mapping: list[dict]) -> list[str]:
    """Which Smart Collections a tag set will land in.

    `mapping` mirrors ``config.yaml``::

        collections:
          - tags: [Infantry, Imperial Guard]
            resolves_to: Sci-Fi Imperial Infantry

    A rule matches when **every** one of its tags is present, because Smart
    Collection rules are conjunctive. Reporting only; nothing here is written to
    the CSV.
    """
    present = {tag.strip().lower() for tag in tags}
    resolved = []
    for rule in mapping:
        required = {t.strip().lower() for t in rule.get("tags", [])}
        if required and required <= present:
            resolved.append(rule["resolves_to"])
    return resolved


def blank_row() -> dict:
    return {column: "" for column in COLUMNS}


def build_rows(product: Product) -> list[dict]:
    """Expand one product into its handle-grouped block.

    ``Top Row`` is TRUE on the first row only; every later row in the block
    carries the handle and nothing else that identifies the product.
    """
    rows = []

    first = blank_row()
    first.update({
        "ID": product.product_id,
        "Handle": product.handle,
        "Command": MERGE,
        "Title": product.title,
        "Body HTML": product.body_html,
        "Vendor": product.vendor,
        "Tags": ", ".join(product.tags),
        "Tags Command": REPLACE,
        "Top Row": "TRUE",
    })
    if product.variants:
        variant = product.variants[0]
        first.update({
            "Variant SKU": variant.sku,
            "Variant Price": variant.price,
            "Option1 Name": variant.option_name,
            "Option1 Value": variant.option_value,
        })
    if product.images:
        first.update({
            "Image Src": product.images[0],
            "Image Command": MERGE,
            "Image Position": "1",
        })
    rows.append(first)

    for variant in product.variants[1:]:
        row = blank_row()
        row.update({
            "Handle": product.handle,
            "Command": MERGE,
            "Variant SKU": variant.sku,
            "Variant Price": variant.price,
            "Option1 Name": variant.option_name,
            "Option1 Value": variant.option_value,
        })
        rows.append(row)

    for position, url in enumerate(product.images[1:], start=2):
        row = blank_row()
        row.update({
            "Handle": product.handle,
            "Command": MERGE,
            "Image Src": url,
            "Image Command": MERGE,
            "Image Position": str(position),
        })
        rows.append(row)

    return rows


def write_altera_csv(products: list[Product], path: str,
                     check_hosts: bool = True) -> list[dict]:
    """Write an Altera import CSV. Returns the rows written."""
    if check_hosts:
        check_image_hosts(products)
    rows = [row for product in products for row in build_rows(product)]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def video_note(products: list[Product]) -> str:
    """Videos are not handled by URL import the way images are.

    Reported in the build summary rather than attempted, so nobody spends an
    afternoon working out why the Video 1 column did nothing.
    """
    return ("Note: Video columns are not imported from URLs the way images are. "
            "Any video must be attached by hand after import. %d products in "
            "this build." % len(products))
