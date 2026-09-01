"""The interface every site adapter implements.

The previous incarnation of this scraper had a separate script per creator,
which meant five copies of the same bugs and five places to fix each one. There
is now **one** scraper and a small adapter per site, holding only what genuinely
differs: selectors, the product-URL pattern, and how pagination works.

Adapters are split so that ``parse_product`` takes **HTML text, not a driver**.
That is the whole reason this repo can test scraping logic at all: the parsing
runs against saved fixture HTML with no browser and no network, and only URL
acquisition needs a live page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ScrapedProduct:
    """What a product page yields. Validated before it is ever written."""

    url: str = ""
    product_id: str = ""
    title: str = ""
    description: str = ""
    images: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def is_valid(self) -> bool:
        """A product with no title or no images is a failure, not a row.

        This is the check that catches a silent block. When Cloudflare starts
        returning the site's generic landing page instead of the product, the
        response is a perfectly good HTTP 200 full of HTML -- it just has no
        product in it. Writing those rows produced 700 plausible-looking
        entries that only revealed themselves as missing images much later.
        """
        return bool(self.title.strip()) and bool(self.images)

    def failure_reason(self) -> str:
        if not self.title.strip():
            return "no title"
        if not self.images:
            return "no images"
        return ""


class SiteAdapter:
    """Base class. Subclasses supply the selectors and the URL pattern."""

    name = "base"
    #: Matches a product URL on this site; group 1 is the stable ID.
    product_url_pattern: re.Pattern = re.compile(r"$^")

    def product_urls_from_html(self, html: str, base_url: str = "") -> list[str]:
        """Every product URL in a page of markup, deduplicated, order kept.

        Used by both the live-scroll mode and the saved-HTML mode, so the two
        acquisition paths cannot drift apart in what they consider a product.
        """
        found, seen = [], set()
        for match in self.product_url_pattern.finditer(html or ""):
            url = match.group(0)
            if url.startswith("/") and base_url:
                url = base_url.rstrip("/") + url
            if url not in seen:
                seen.add(url)
                found.append(url)
        return found

    def product_id(self, url: str) -> str:
        """The stable numeric ID from a product URL, for deduplication."""
        match = self.product_url_pattern.search(url or "")
        return match.group(1) if match and match.lastindex else ""

    def parse_product(self, html: str, url: str = "") -> ScrapedProduct:
        raise NotImplementedError


def diagnose_empty(html: str, limit: int = 40) -> str:
    """Every anchor href on the page, for when zero products were found.

    Not a nicety. When a selector breaks, the difference between this output and
    a bare "0 products found" is a ten-minute fix versus an hour of guessing.
    """
    hrefs = re.findall(r'href="([^"]+)"', html or "")
    unique = list(dict.fromkeys(hrefs))[:limit]
    if not unique:
        return ("No anchors at all on this page. That usually means the content "
                "is rendered after load and the HTML was captured too early, or "
                "the request was served a block page.")
    return ("Found no product URLs. Every anchor on the page, so the pattern "
            "can be fixed:\n" + "\n".join("  %s" % href for href in unique))
