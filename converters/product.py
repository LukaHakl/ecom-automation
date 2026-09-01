"""The normalised internal product model.

Every reader parses a platform export into :class:`Product` objects; every
writer emits :class:`Product` objects into a platform format. **No reader ever
talks to a writer directly.**

That rule is the whole architecture. With N platforms, direct conversion means
N*(N-1) converters, each with its own quirks, each fixed separately when a
platform changes a column name. Through a normalised model it is N readers plus
N writers, and a platform change touches exactly one file.

The cost is real and worth stating: anything a platform expresses that this
model does not carry is lost in transit. That is why :func:`round_trip_report`
exists -- it makes field loss measurable rather than something discovered later
by a customer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields


@dataclass
class Variant:
    option_name: str = ""
    option_value: str = ""
    price: str = ""
    sku: str = ""


@dataclass
class Product:
    """One product, in whatever detail the source export carried."""

    title: str = ""
    description: str = ""
    sku: str = ""
    price: str = ""
    currency: str = ""
    quantity: str = ""
    tags: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    variants: list[Variant] = field(default_factory=list)
    vendor: str = ""
    creator: str = ""
    scale: str = ""
    material: str = ""
    category: str = ""

    @property
    def primary_sku(self) -> str:
        """The first SKU, for use as a join key.

        Etsy exports repeat rows per variation and its SKU field can hold a
        comma-separated list, so the raw value is frequently several SKUs in a
        trench coat. Everything that joins on SKU joins on this.
        """
        return first_sku(self.sku)


def first_sku(value: str) -> str:
    """First entry of a possibly comma-separated SKU field."""
    return (value or "").split(",")[0].strip()


def all_skus(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


#: Suffixes and markers stripped before comparing titles across platforms.
#: Deliberately conservative: over-normalising makes different products
#: identical, which is worse than failing to match.
_TITLE_NOISE = re.compile(r"""(?xi)
    \b\d+\s*mm\b                 # 32mm, 75 mm
  | \b1[:/]\d{1,3}\b             # 1:56, 1/72
  | \b(?:pre-?supported|supported|unsupported)\b
  | \b(?:stl|3d\s*print(?:able)?)\b
  | \b(?:miniature|mini|figure|model)s?\b
""")
_PUNCT = re.compile(r"[^\w\s]")
_SPACE = re.compile(r"\s+")


def normalise_title(title: str) -> str:
    """Lowercase, strip scale markers and format noise, collapse whitespace.

    Used for cross-platform title matching where SKU schemes diverge. Note this
    is a *matching* aid, never a display value.
    """
    text = _TITLE_NOISE.sub(" ", (title or "").lower())
    text = _PUNCT.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


def title_similarity(a: str, b: str) -> float:
    """Token-set Jaccard similarity of two normalised titles, 0.0-1.0.

    Jaccard rather than a sequence ratio because word order varies freely
    between platforms ("Stone Golem, Large" vs "Large Stone Golem") while the
    vocabulary does not.

    This is deliberately *not* trusted on its own anywhere in this repo. On real
    data it produced confident false positives across different creators, which
    is why every caller pairs it with a creator constraint, a REVIEW band, or
    both.
    """
    tokens_a = set(normalise_title(a).split())
    tokens_b = set(normalise_title(b).split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def round_trip_report(before: Product, after: Product) -> list[str]:
    """Fields that changed between an original and a re-parsed Product.

    The acceptance criterion for every converter is that field loss is either
    zero or explicitly documented. This makes it checkable in a test rather than
    a claim in a README.
    """
    lost = []
    for spec in fields(Product):
        original = getattr(before, spec.name)
        result = getattr(after, spec.name)
        if original != result:
            lost.append("%s: %r -> %r" % (spec.name, original, result))
    return lost
