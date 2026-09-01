"""Rewrite marketplace copy for physical made-to-order prints.

Source listings are written to sell a *digital file*: STLs, pre-supported
meshes, instant download, commercial licences, Patreon tiers. A listing selling
a physical printed object cannot say any of that, and several categories of
text are outright policy or legal risks rather than merely wrong:

- **External links.** Etsy prohibits linking to outside sales channels. A
  leftover Patreon or MyMiniFactory link risks the listing, not just its
  quality.
- **IP terms.** Some rightsholders in this space pursue takedowns aggressively,
  including against proxy and lookalike terms rather than only their own
  trademarks. The denylist is therefore **config-supplied and stays out of this
  repo** -- shipping it would publish a map of which terms attract enforcement.
  :func:`load_denylist` reads it from a file the user provides;
  ``config.example.yaml`` ships with an empty list and a comment.

Every removal is reported rather than done silently, because these edits are
the ones you will be asked to justify. :func:`clean_description` returns both
the cleaned text and the list of what it took out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Phrases that only make sense when selling a digital file. Matched
#: case-insensitively as whole phrases; a line containing one is dropped.
DIGITAL_PHRASES = [
    "stl", "stl file", "stl files", "3mf", "obj file", "zip file",
    "pre-supported", "presupported", "unsupported", "supported version",
    "instant download", "digital download", "digital file", "digital product",
    "printable file", "print at home", "you will receive the files",
    "commercial licence", "commercial license", "personal use only",
    "merchant licence", "merchant license", "file format", "mesh",
    "slicer", "chitubox", "lychee",
]

#: Whole blocks that disappear along with the line they head.
PROMO_HEADINGS = ["patreon", "my patreon", "join my patreon", "support me on",
                  "tribes", "kickstarter", "gumroad", "cults3d", "myminifactory"]

_URL = re.compile(r"""(?xi)
    \b(?:https?://|www\.)\S+          # explicit URLs
  | \b[\w.-]+\.(?:com|net|org|io|co|shop|store|de|uk)\b(?:/\S*)?
""")

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")

#: Collapses the double spaces left behind by removing a word mid-sentence.
_MULTISPACE = re.compile(r"[ \t]{2,}")
_MULTINEWLINE = re.compile(r"\n{3,}")


@dataclass
class CleanResult:
    """Cleaned text plus an audit trail of what was removed and why."""

    text: str
    removed: list[str] = field(default_factory=list)

    def note(self, reason: str, detail: str) -> None:
        self.removed.append("%s: %s" % (reason, detail.strip()[:120]))


def load_denylist(path: str | None) -> list[str]:
    """Read the IP denylist from a config-supplied file.

    One term per line, ``#`` for comments, blank lines ignored. Returns an empty
    list when `path` is None or the file is absent -- an absent denylist must
    never be an error, or the tool becomes unusable for anyone who does not have
    one.
    """
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            return [line.strip() for line in handle
                    if line.strip() and not line.startswith("#")]
    except FileNotFoundError:
        return []


def _phrase_pattern(term: str) -> re.Pattern:
    r"""Whole-word-ish match for a possibly multi-word term.

    ``\b`` on both ends, with internal whitespace allowed to vary, so
    "Space Marine" also matches "space  marine" and "Space\nMarine". Deliberately
    not a substring match: stripping "orc" from "record" is how a denylist
    destroys otherwise-fine copy.
    """
    escaped = r"\s+".join(re.escape(part) for part in term.split())
    return re.compile(r"\b%s\b" % escaped, re.I)


def strip_terms(text: str, terms: list[str], result: CleanResult,
                reason: str = "denylisted term") -> str:
    """Remove every denylisted term, recording each removal."""
    for term in terms:
        pattern = _phrase_pattern(term)
        if pattern.search(text):
            count = len(pattern.findall(text))
            text = pattern.sub("", text)
            result.note(reason, "%s (x%d)" % (term, count))
    return text


def strip_links(text: str, result: CleanResult) -> str:
    """Remove email addresses and URLs.

    Emails first, deliberately. The bare-domain half of the URL pattern matches
    the domain *inside* an address, so running URLs first turns
    ``a.b@example.com`` into a dangling ``a.b@`` that the email pattern then no
    longer recognises -- leaving a fragment of a real contact address in live
    copy.
    """
    for pattern, reason in ((_EMAIL, "email address"), (_URL, "external link")):
        for found in pattern.findall(text):
            result.note(reason, found if isinstance(found, str) else str(found))
        text = pattern.sub("", text)
    return text


def _is_digital_line(line: str) -> str | None:
    """The phrase that makes this line digital-only, if any."""
    lowered = line.lower()
    for phrase in DIGITAL_PHRASES:
        if _phrase_pattern(phrase).search(lowered):
            return phrase
    for heading in PROMO_HEADINGS:
        if heading in lowered:
            return heading
    return None


def tidy_whitespace(text: str) -> str:
    """Collapse the gaps left by removing words and lines."""
    lines = [_MULTISPACE.sub(" ", line).strip() for line in text.splitlines()]
    return _MULTINEWLINE.sub("\n\n", "\n".join(lines)).strip()


def clean_description(text: str, denylist: list[str] | None = None,
                      glossary: dict[str, str] | None = None) -> CleanResult:
    """Rewrite a source description for a physical made-to-order listing.

    Order matters. Translation runs first so that denylist terms written in the
    source language are still caught; links are stripped before line removal so
    a bare URL on its own line does not survive as an empty line.
    """
    result = CleanResult(text="")

    if glossary:
        text = translate(text, glossary, result)

    text = strip_links(text, result)

    kept = []
    for line in text.splitlines():
        phrase = _is_digital_line(line)
        if phrase and line.strip():
            result.note("digital-only line", "%r (matched %r)" % (line, phrase))
            continue
        kept.append(line)
    text = "\n".join(kept)

    text = strip_terms(text, denylist or [], result)
    result.text = tidy_whitespace(text)
    return result


def translate(text: str, glossary: dict[str, str], result: CleanResult) -> str:
    """Phrase-level replacement from a config-supplied glossary.

    Deliberately not machine translation. Some creators publish in German, and
    a deterministic, reviewable substitution table is worth more than a fluent
    but unpredictable rendering -- the output of this goes straight onto a live
    listing, and a diff you can read is the only way to approve it.

    Longest phrases first, so a glossary containing both "Miniatur" and
    "Miniatur Set" does not half-translate the longer one.
    """
    for source in sorted(glossary, key=len, reverse=True):
        pattern = _phrase_pattern(source)
        if pattern.search(text):
            text = pattern.sub(glossary[source], text)
            result.note("translated", "%s -> %s" % (source, glossary[source]))
    return text


#: Marketplace boilerplate stripped from titles. Configurable because it varies
#: by site.
TITLE_STRIP = ["3D Printable", "3D Print", "3D-Printable", "STL"]


def clean_title(title: str, strip: list[str] | None = None,
                denylist: list[str] | None = None) -> CleanResult:
    """Strip marketplace boilerplate and denylisted terms from a title."""
    result = CleanResult(text="")
    text = title
    for term in (strip if strip is not None else TITLE_STRIP):
        pattern = _phrase_pattern(term)
        if pattern.search(text):
            text = pattern.sub("", text)
            result.note("title boilerplate", term)
    text = strip_terms(text, denylist or [], result)
    # Collapse the double spaces and stray punctuation left behind.
    text = _MULTISPACE.sub(" ", text).strip(" -–—|,")
    result.text = text.strip()
    return result


def slugify(text: str) -> str:
    """Lowercase hyphenated slug, for SKU generation."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_-]+", "-", slug).strip("-")


def make_sku(creator: str, title: str) -> str:
    """``{CREATOR}-{slug}``: uppercase creator prefix, lowercase slug.

    Callers must pass the *translated* title where translation applies. A SKU
    derived from the original German title while the listing shows English is a
    mismatch that surfaces much later, in whichever system joins on SKU.
    """
    return "%s-%s" % (slugify(creator).upper(), slugify(title))
