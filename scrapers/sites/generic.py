"""Two site adapters, configured by pattern rather than by bespoke code.

Both marketplaces expose the same three things -- a title, a description, and a
gallery -- behind different markup. Rather than write two hand-rolled parsers,
each adapter declares its selectors and the shared implementation does the
work, so a fix to the parsing benefits both.

Parsing uses regular expressions over the saved HTML rather than a DOM library.
That is a deliberate trade: these pages are large, the fields wanted are few,
and the alternative pulls in a parser dependency for three extractions. It also
means ``parse_product`` works on a string, which is what makes it testable
against fixture files.
"""

from __future__ import annotations

import html as html_module
import re

from .base import ScrapedProduct, SiteAdapter


def _text(pattern: re.Pattern, html: str, group: int = 1) -> str:
    match = pattern.search(html or "")
    if not match:
        return ""
    return html_module.unescape(re.sub(r"<[^>]+>", " ", match.group(group))).strip()


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


class PatternAdapter(SiteAdapter):
    """A site adapter driven entirely by declared patterns."""

    title_patterns: list[re.Pattern] = []
    description_patterns: list[re.Pattern] = []
    image_patterns: list[re.Pattern] = []
    #: Substrings that mark a URL as chrome rather than product photography.
    image_denylist: tuple[str, ...] = ("avatar", "logo", "sprite", "icon",
                                       "placeholder", "profile")
    max_images = 10

    def parse_product(self, html: str, url: str = "") -> ScrapedProduct:
        product = ScrapedProduct(url=url, product_id=self.product_id(url))

        for pattern in self.title_patterns:
            title = _collapse(_text(pattern, html))
            if title:
                product.title = title
                break

        for pattern in self.description_patterns:
            description = _text(pattern, html)
            if description:
                product.description = _collapse(description)
                break

        seen = set()
        for pattern in self.image_patterns:
            for match in pattern.finditer(html or ""):
                candidate = html_module.unescape(match.group(1)).strip()
                lowered = candidate.lower()
                if any(bad in lowered for bad in self.image_denylist):
                    continue
                if candidate and candidate not in seen:
                    seen.add(candidate)
                    product.images.append(candidate)
                if len(product.images) >= self.max_images:
                    return product
        return product


class MyMiniFactoryAdapter(PatternAdapter):
    name = "myminifactory"
    #: Object URLs end in a slug plus a five-or-more digit ID, which is the
    #: stable key. The saved-HTML acquisition mode regexes for exactly this.
    product_url_pattern = re.compile(r"https?://[^\"'\s]*?/object/[^\"'\s]*?-(\d{5,})")
    title_patterns = [
        re.compile(r'<meta\s+property="og:title"\s+content="([^"]+)"', re.I),
        re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S),
    ]
    description_patterns = [
        re.compile(r'<meta\s+property="og:description"\s+content="([^"]+)"', re.I),
        re.compile(r'<div[^>]+class="[^"]*object-description[^"]*"[^>]*>(.*?)</div>',
                   re.I | re.S),
    ]
    image_patterns = [
        re.compile(r'<meta\s+property="og:image"\s+content="([^"]+)"', re.I),
        re.compile(r'<img[^>]+src="([^"]+images\.myminifactory[^"]+)"', re.I),
    ]


class Cults3DAdapter(PatternAdapter):
    name = "cults3d"
    product_url_pattern = re.compile(r"https?://[^\"'\s]*?/en/3d-model/[^\"'\s/]+/([\w-]+)")
    title_patterns = [
        re.compile(r'<meta\s+property="og:title"\s+content="([^"]+)"', re.I),
        re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S),
    ]
    description_patterns = [
        re.compile(r'<meta\s+property="og:description"\s+content="([^"]+)"', re.I),
    ]
    image_patterns = [
        re.compile(r'<meta\s+property="og:image"\s+content="([^"]+)"', re.I),
        re.compile(r'<img[^>]+src="([^"]+cults3d[^"]+)"', re.I),
    ]


ADAPTERS = {
    MyMiniFactoryAdapter.name: MyMiniFactoryAdapter,
    Cults3DAdapter.name: Cults3DAdapter,
}


def get_adapter(name: str) -> SiteAdapter:
    try:
        return ADAPTERS[name]()
    except KeyError:
        raise SystemExit("Unknown site %r. Available: %s"
                         % (name, ", ".join(sorted(ADAPTERS))))
