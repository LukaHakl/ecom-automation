"""Shop Manager stage 2 (the join) and the config-driven form driver."""

from __future__ import annotations

import csv
import io

import pytest

from shopmanager.form_driver import (
    CLICK, FILL, SELECT_SEARCH, FormSpecError, Step, describe, load_steps,
    required_columns, resolve, tabs_in_order,
)
from shopmanager.price_matcher import (
    LibraryItem, SourceItem, match, read_library, read_source, summary,
    write_outputs,
)


# ===========================================================================
# Stage 2: the join
# ===========================================================================

def lib(**kwargs):
    base = {"creator": "ACME", "creator_value": "c1",
            "object_text": "Stone Golem", "object_value": "o1"}
    base.update(kwargs)
    return LibraryItem(**base)


def test_a_close_title_matches():
    result = match([lib()], [SourceItem(title="Stone Golem", price="10")])
    assert len(result.matched) == 1
    assert result.matched[0].price == "10"


def test_the_internal_option_value_is_carried_through():
    """Stage 3 selects on the value, not the display text -- the text is
    localised and occasionally re-worded, the value is not."""
    result = match([lib()], [SourceItem(title="Stone Golem")])
    assert result.matched[0].object_value == "o1"
    assert result.matched[0].creator_value == "c1"


def test_a_weak_match_falls_below_the_threshold():
    result = match([lib()], [SourceItem(title="Completely Different")])
    assert result.matched == [] and len(result.unmatched_library) == 1


def test_the_threshold_is_adjustable():
    library = [lib(object_text="Stone Golem Large")]
    source = [SourceItem(title="Stone Golem")]
    assert match(library, source, threshold=0.9).matched == []
    assert len(match(library, source, threshold=0.5).matched) == 1


def test_every_match_carries_a_confidence_score():
    result = match([lib()], [SourceItem(title="Stone Golem")])
    assert 0 < result.matched[0].match_score <= 1.0


def test_matching_is_confined_to_the_same_creator():
    """Two studios both selling a 'Stone Golem' is not a coincidence worth
    matching on -- this is the main failure mode of fuzzy title matching here."""
    library = [lib(creator="ACME")]
    source = [SourceItem(title="Stone Golem", creator="OTHER STUDIO")]
    assert match(library, source).matched == []


def test_the_creator_constraint_can_be_turned_off():
    library = [lib(creator="ACME")]
    source = [SourceItem(title="Stone Golem", creator="OTHER STUDIO")]
    assert len(match(library, source, same_creator_only=False).matched) == 1


def test_the_constraint_does_not_apply_when_the_source_has_no_creator_column():
    """Applying it to a source with the column blank would match nothing."""
    result = match([lib(creator="ACME")], [SourceItem(title="Stone Golem")])
    assert len(result.matched) == 1


def test_a_source_listing_is_claimed_only_once():
    library = [lib(object_value="o1"), lib(object_value="o2")]
    result = match(library, [SourceItem(title="Stone Golem")])
    assert len(result.matched) == 1 and len(result.unmatched_library) == 1


def test_unmatched_library_items_are_the_catalogue_gaps():
    result = match([lib(object_text="Owned But Unlisted")], [])
    assert len(result.unmatched_library) == 1


def test_unmatched_source_listings_have_no_owned_asset():
    result = match([], [SourceItem(title="Listed But Unowned")])
    assert len(result.unmatched_source) == 1


def test_the_summary_shows_the_weakest_matches_for_review():
    library = [lib(object_text="Stone Golem %d" % i, object_value="o%d" % i)
               for i in range(3)]
    source = [SourceItem(title="Stone Golem %d" % i) for i in range(3)]
    text = summary(match(library, source))
    assert "Weakest" in text and "eyeball" in text


def test_the_three_output_files_are_written(tmp_path):
    result = match([lib()], [SourceItem(title="Stone Golem", price="10")])
    paths = write_outputs(result, str(tmp_path), stamp="20260101_000000")
    assert set(paths) == {"matched", "unmatched_library", "unmatched_source"}
    rows = list(csv.DictReader(open(paths["matched"], encoding="utf-8")))
    assert rows[0]["object_value"] == "o1" and rows[0]["price"] == "10"


def test_the_library_reader_skips_rows_with_no_object():
    handle = io.StringIO("creator,creator_value,object_text,object_value\n"
                         "ACME,c1,Stone Golem,o1\n"
                         "ACME,c1,,o2\n")
    assert len(read_library(handle)) == 1


def test_the_source_reader_accepts_either_header_case():
    handle = io.StringIO("TITLE,PRICE,SKU\nStone Golem,10.00,S1\n")
    items = read_source(handle)
    assert items[0].title == "Stone Golem" and items[0].price == "10.00"


# ===========================================================================
# The config-driven form driver
# ===========================================================================

SPEC = {"form": {"steps": [
    {"tab": "Info", "field": "creator", "selector": "#creator",
     "action": "select_search", "value": "{creator_value}", "required": True},
    {"tab": "Info", "field": "title", "selector": "input[name=title]",
     "value": "{title}"},
    {"tab": "Pricing", "field": "price", "selector": "input[name=price]",
     "value": "{price}"},
]}}

ROW = {"creator_value": "c1", "title": "Stone Golem", "price": "24.00"}


def test_steps_load_from_config():
    steps = load_steps(SPEC)
    assert len(steps) == 3
    assert steps[0].action == SELECT_SEARCH and steps[0].required


def test_the_default_action_is_fill():
    assert load_steps(SPEC)[1].action == FILL


def test_a_config_with_no_steps_explains_the_design():
    with pytest.raises(FormSpecError, match="form.steps"):
        load_steps({})


def test_a_step_missing_a_selector_is_rejected():
    with pytest.raises(FormSpecError, match="selector"):
        load_steps({"form": {"steps": [{"tab": "T", "field": "f"}]}})


def test_an_unknown_action_is_rejected():
    spec = {"form": {"steps": [{"tab": "T", "field": "f", "selector": "s",
                                "action": "teleport"}]}}
    with pytest.raises(FormSpecError, match="teleport"):
        load_steps(spec)


def test_required_columns_are_discoverable_up_front():
    """So a CSV can be checked before a run touches the account."""
    assert required_columns(load_steps(SPEC)) == {"creator_value", "title", "price"}


def test_values_are_substituted_from_the_row():
    resolved = resolve(load_steps(SPEC), ROW)
    assert resolved[0].value == "c1"
    assert resolved[1].value == "Stone Golem"


def test_a_row_missing_a_referenced_column_is_an_error():
    with pytest.raises(FormSpecError, match="missing column"):
        resolve(load_steps(SPEC), {"creator_value": "c1"})


def test_an_empty_optional_field_is_dropped():
    row = dict(ROW, price="")
    fields = [s.field for s in resolve(load_steps(SPEC), row)]
    assert "price" not in fields


def test_an_empty_required_field_raises_rather_than_half_filling():
    """A half-filled draft looks like a successful run."""
    row = dict(ROW, creator_value="")
    with pytest.raises(FormSpecError, match="required field"):
        resolve(load_steps(SPEC), row)


def test_a_click_step_survives_having_no_value():
    spec = {"form": {"steps": [{"tab": "T", "field": "save", "selector": "#s",
                                "action": "click"}]}}
    assert resolve(load_steps(spec), {})[0].action == CLICK


def test_tabs_come_back_in_first_appearance_order():
    """Each tab switch is a page transition and a wait, so grouping matters."""
    assert tabs_in_order(resolve(load_steps(SPEC), ROW)) == ["Info", "Pricing"]


def test_the_dry_run_description_names_every_field_and_value():
    text = describe(resolve(load_steps(SPEC), ROW))
    assert "[tab] Info" in text and "[tab] Pricing" in text
    assert "Stone Golem" in text and "24.00" in text


# ===========================================================================
# Stage 1's option-count settling
# ===========================================================================

def test_the_option_count_must_hold_steady_before_it_is_accepted():
    """A fixed sleep either wastes time or truncates the list; this waits for
    the count to stop changing."""
    from shopmanager.library_scraper import wait_for_options

    counts = iter([0, 3, 7, 12, 12, 12, 12, 12, 12, 12, 12, 12])
    assert wait_for_options(lambda: next(counts, 12), timeout=10) == 12


def test_waiting_gives_up_at_the_timeout_rather_than_hanging():
    from shopmanager.library_scraper import wait_for_options

    counter = iter(range(1, 10_000))
    assert wait_for_options(lambda: next(counter), timeout=1.5) >= 0
