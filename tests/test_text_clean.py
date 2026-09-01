"""Description and title rewriting for physical made-to-order listings."""

from __future__ import annotations

from common.text_clean import (
    clean_description, clean_title, load_denylist, make_sku, slugify,
    strip_links, strip_terms, tidy_whitespace, translate, CleanResult,
)


# ---------------------------------------------------------------------------
# Digital-only copy
# ---------------------------------------------------------------------------

def test_stl_and_file_format_lines_are_removed():
    text = ("A detailed dragon model.\n"
            "You will receive the STL files for printing.\n"
            "Pre-supported and unsupported versions included.\n"
            "Great centrepiece for any table.")
    result = clean_description(text)
    assert "STL" not in result.text
    assert "supported" not in result.text.lower()
    assert "A detailed dragon model." in result.text
    assert "Great centrepiece" in result.text


def test_instant_download_lines_go():
    result = clean_description("Instant download after purchase.\nKeep this.")
    assert result.text == "Keep this."


def test_commercial_licence_lines_go():
    result = clean_description("Commercial license included.\nA nice model.")
    assert "license" not in result.text.lower()


def test_patreon_blocks_are_removed():
    text = "Lovely model.\nJoin my Patreon for more!\nSupport me on Patreon."
    result = clean_description(text)
    assert "patreon" not in result.text.lower()
    assert "Lovely model." in result.text


def test_every_removal_is_reported():
    """These edits get questioned; the audit trail is the answer."""
    result = clean_description("Nice.\nIncludes STL files.\nhttps://patreon.com/x")
    assert result.removed
    assert any("digital-only line" in entry for entry in result.removed)


# ---------------------------------------------------------------------------
# Links -- an Etsy policy risk, not a style issue
# ---------------------------------------------------------------------------

def test_urls_are_stripped():
    result = CleanResult(text="")
    assert "http" not in strip_links("See https://example.com/x for more", result)


def test_bare_domains_are_stripped():
    result = CleanResult(text="")
    assert "example.com" not in strip_links("Visit example.com today", result)


def test_www_links_are_stripped():
    result = CleanResult(text="")
    assert "www." not in strip_links("Go to www.example.org now", result)


def test_emails_are_stripped():
    result = CleanResult(text="")
    assert "@" not in strip_links("Mail me at a.b@example.com", result)


def test_links_are_reported_individually():
    result = CleanResult(text="")
    strip_links("https://a.example.com and https://b.example.com", result)
    assert len([r for r in result.removed if "external link" in r]) == 2


# ---------------------------------------------------------------------------
# The IP denylist
# ---------------------------------------------------------------------------

def test_denylisted_terms_are_removed_from_descriptions():
    result = clean_description("A Space Marine model, very detailed.",
                               denylist=["Space Marine"])
    assert "Space Marine" not in result.text
    assert "very detailed" in result.text


def test_multiword_terms_tolerate_varied_whitespace():
    result = CleanResult(text="")
    assert "Space" not in strip_terms("A Space   Marine here",
                                      ["Space Marine"], result)


def test_denylist_matching_is_word_bounded():
    """Substring matching is how a denylist destroys otherwise-fine copy:
    stripping 'orc' from 'record' or 'forces'."""
    result = CleanResult(text="")
    text = strip_terms("We record every order for the forces", ["orc"], result)
    assert "record" in text and "forces" in text


def test_denylist_is_case_insensitive():
    result = CleanResult(text="")
    assert "SPACE MARINE" not in strip_terms("A SPACE MARINE", ["space marine"],
                                             result)


def test_each_denylist_removal_is_reported_with_its_count():
    result = CleanResult(text="")
    strip_terms("orc and orc and orc", ["orc"], result)
    assert any("x3" in entry for entry in result.removed)


def test_an_absent_denylist_file_is_not_an_error(tmp_path):
    """The tool must stay usable for anyone who does not have one."""
    assert load_denylist(str(tmp_path / "nope.txt")) == []
    assert load_denylist(None) == []


def test_denylist_file_ignores_comments_and_blanks(tmp_path):
    path = tmp_path / "deny.txt"
    path.write_text("# a comment\nterm one\n\nterm two\n", encoding="utf-8")
    assert load_denylist(str(path)) == ["term one", "term two"]


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------

def test_glossary_replaces_phrases():
    result = CleanResult(text="")
    assert translate("Ein Drache Modell", {"Drache": "Dragon"}, result) \
        == "Ein Dragon Modell"


def test_longer_glossary_phrases_win():
    """Otherwise 'Miniatur' half-translates 'Miniatur Set'."""
    result = CleanResult(text="")
    glossary = {"Miniatur": "Miniature", "Miniatur Set": "Miniature Set"}
    assert translate("Ein Miniatur Set", glossary, result) == "Ein Miniature Set"


def test_translation_is_reported():
    result = CleanResult(text="")
    translate("Drache", {"Drache": "Dragon"}, result)
    assert any("translated" in entry for entry in result.removed)


def test_translation_runs_before_the_denylist():
    """A denylisted term written in the source language must still be caught."""
    result = clean_description("Ein Drache hier", denylist=["Dragon"],
                               glossary={"Drache": "Dragon"})
    assert "Dragon" not in result.text


# ---------------------------------------------------------------------------
# Whitespace
# ---------------------------------------------------------------------------

def test_double_spaces_from_removals_are_collapsed():
    assert tidy_whitespace("a  b   c") == "a b c"


def test_runs_of_blank_lines_are_collapsed():
    assert tidy_whitespace("a\n\n\n\n\nb") == "a\n\nb"


def test_leading_and_trailing_whitespace_goes():
    assert tidy_whitespace("\n  hello  \n\n") == "hello"


# ---------------------------------------------------------------------------
# Titles and SKUs
# ---------------------------------------------------------------------------

def test_marketplace_boilerplate_is_stripped_from_titles():
    assert clean_title("3D Printable Dragon Model").text == "Dragon Model"


def test_title_stripping_is_case_insensitive():
    assert clean_title("3d print Dragon").text == "Dragon"


def test_double_spaces_left_by_stripping_are_collapsed():
    assert "  " not in clean_title("Big 3D Print Dragon").text


def test_title_strip_list_is_configurable():
    assert clean_title("Custom Dragon", strip=["Custom"]).text == "Dragon"


def test_denylisted_terms_are_stripped_from_titles_too():
    assert "Marine" not in clean_title("Space Marine Dragon",
                                       denylist=["Space Marine"]).text


def test_slugify_lowercases_and_hyphenates():
    assert slugify("Big Stone Golem!") == "big-stone-golem"


def test_slugify_collapses_separator_runs():
    assert slugify("a -- b__c") == "a-b-c"


def test_sku_is_uppercase_creator_and_lowercase_slug():
    assert make_sku("acme studio", "Stone Golem") == "ACME-STUDIO-stone-golem"


def test_sku_uses_the_translated_title():
    """A SKU from the German title while the listing shows English is a
    mismatch that surfaces much later, in whichever system joins on SKU."""
    result = CleanResult(text="")
    english = translate("Drache", {"Drache": "Dragon"}, result)
    assert make_sku("ACME", english) == "ACME-dragon"
