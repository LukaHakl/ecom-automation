"""Vela CSV rules. Every test here corresponds to an import that once failed."""

from __future__ import annotations

import csv

import pytest

from common.vela import (
    COLUMNS, MAX_PHOTOS, PHOTO_COLUMNS, TAXONOMY_ID, VAR_VISIBILITY_ON,
    WHEN_MADE, WHO_MADE, Axis, Listing, Variation, blank_row, build_rows,
    check_photo_coverage, normalise_photo_columns, photo_column_index,
    photo_coverage, write_post_import_todo, write_vela_csv,
)


# ---------------------------------------------------------------------------
# The photo column trap
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("spelling,slot", [
    ("Photo 1", 1), ("IMAGE1", 1), ("Image 1", 1), ("photo_1", 1),
    ("PHOTO 10", 10), ("IMAGE10", 10),
])
def test_every_photo_spelling_is_recognised(spelling, slot):
    assert photo_column_index(spelling) == slot


@pytest.mark.parametrize("name", ["TITLE", "PRICE", "SKU", "Photos", "Image"])
def test_non_photo_columns_are_not_mistaken_for_photos(name):
    assert photo_column_index(name) is None


def test_etsy_export_headers_are_rewritten_to_vela_spelling():
    """The exact failure: Vela imports IMAGE1 headers successfully and
    attaches nothing."""
    header = ["TITLE", "IMAGE1", "IMAGE2", "SKU"]
    assert normalise_photo_columns(header) == ["TITLE", "Photo 1", "Photo 2", "SKU"]


def test_normalisation_leaves_correct_headers_alone():
    header = ["TITLE", "Photo 1", "SKU"]
    assert normalise_photo_columns(header) == header


def test_photo_coverage_counts_only_parent_rows():
    """Child variation rows legitimately have no photos; counting them would
    make a healthy file look broken."""
    rows = [
        {"TITLE": "A", "Photo 1": "a.jpg"},
        {"TITLE": "", "Photo 1": ""},          # child row
        {"TITLE": "B", "Photo 1": ""},
    ]
    with_photo, total, fraction = photo_coverage(rows)
    assert (with_photo, total) == (1, 2)
    assert fraction == 0.5


def test_low_coverage_produces_a_loud_warning():
    rows = [{"TITLE": "A", "Photo 1": ""}, {"TITLE": "B", "Photo 1": ""}]
    message = check_photo_coverage(rows)
    assert message and "0 of 2" in message and "silently" in message


def test_full_coverage_warns_about_nothing():
    assert check_photo_coverage([{"TITLE": "A", "Photo 1": "a.jpg"}]) is None


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def test_made_to_order_defaults_are_applied():
    row = build_rows(Listing(title="Thing"))[0]
    assert row["WHO_MADE"] == WHO_MADE == "i_did"
    assert row["WHEN_MADE"] == WHEN_MADE == "made_to_order"
    assert row["TAXONOMY_ID"] == TAXONOMY_ID == "1239"


def test_shipping_profile_is_left_blank_for_the_shop_default():
    row = build_rows(Listing(title="Thing"))[0]
    assert row["SHIPPING_PROFILE"] == ""
    assert row["PRODUCTION_PARTNER_IDS"] == ""


# ---------------------------------------------------------------------------
# Rule 1: the first variation is embedded in the parent row
# ---------------------------------------------------------------------------

def test_a_listing_with_no_variations_is_one_row():
    rows = build_rows(Listing(title="Plain"))
    assert len(rows) == 1
    assert rows[0]["VARIATION 1 VALUES"] == ""
    assert rows[0]["Var Visibility"] == ""


def test_three_variations_produce_three_rows_not_four():
    """Rule 1. A parent plus N variation rows yields N+1 variations on Etsy."""
    listing = Listing(
        title="Sized", axis_1=Axis("Size", "Size"),
        variations=[Variation("S"), Variation("M"), Variation("L")],
    )
    rows = build_rows(listing)
    assert len(rows) == 3
    assert [r["VARIATION 1 VALUES"] for r in rows] == ["S", "M", "L"]


def test_the_parent_row_carries_the_first_variation_and_the_listing_data():
    listing = Listing(
        title="Sized", sku="SKU1", axis_1=Axis("Size", "Size"),
        variations=[Variation("S", price="10.00"), Variation("M")],
    )
    parent = build_rows(listing)[0]
    assert parent["TITLE"] == "Sized" and parent["SKU"] == "SKU1"
    assert parent["VARIATION 1 VALUES"] == "S"
    assert parent["Var Price"] == "10.00"


def test_child_rows_carry_no_listing_level_data():
    listing = Listing(title="Sized", sku="SKU1", axis_1=Axis("Size", "Size"),
                      variations=[Variation("S"), Variation("M")])
    child = build_rows(listing)[1]
    assert child["TITLE"] == "" and child["SKU"] == "" and child["PRICE"] == ""


# ---------------------------------------------------------------------------
# Rule 2: child rows repeat both axis label fields
# ---------------------------------------------------------------------------

def test_single_axis_labels_repeat_on_every_row():
    listing = Listing(title="Sized", axis_1=Axis("Size", "Size"),
                      variations=[Variation("S"), Variation("M"), Variation("L")])
    for row in build_rows(listing):
        assert row["VARIATION 1 TYPE"] == "Size"
        assert row["VARIATION 1 NAME"] == "Size"


def test_both_axis_labels_repeat_on_every_row():
    """Rule 2. Leaving them blank on child rows fails the import."""
    listing = Listing(
        title="Two", axis_1=Axis("Size", "Size"), axis_2=Axis("Colour", "Edge colour"),
        variations=[Variation("S", "Red"), Variation("S", "Blue"),
                    Variation("M", "Red")],
    )
    rows = build_rows(listing)
    assert len(rows) == 3
    for row in rows:
        assert row["VARIATION 1 TYPE"] == "Size"
        assert row["VARIATION 1 NAME"] == "Size"
        assert row["VARIATION 2 TYPE"] == "Colour"
        assert row["VARIATION 2 NAME"] == "Edge colour"


def test_two_axis_values_land_in_their_own_columns():
    listing = Listing(title="Two", axis_1=Axis("Size", "Size"),
                      axis_2=Axis("Colour", "Edge colour"),
                      variations=[Variation("S", "Red"), Variation("M", "Blue")])
    rows = build_rows(listing)
    assert [(r["VARIATION 1 VALUES"], r["VARIATION 2 VALUES"]) for r in rows] \
        == [("S", "Red"), ("M", "Blue")]


def test_a_single_axis_listing_leaves_axis_2_entirely_blank():
    listing = Listing(title="One", axis_1=Axis("Size", "Size"),
                      variations=[Variation("S"), Variation("M")])
    for row in build_rows(listing):
        assert row["VARIATION 2 TYPE"] == ""
        assert row["VARIATION 2 NAME"] == ""
        assert row["VARIATION 2 VALUES"] == ""


# ---------------------------------------------------------------------------
# Rule 3: no stale field leakage between listings
# ---------------------------------------------------------------------------

def test_a_plain_listing_after_a_variated_one_has_no_leaked_variation_data():
    """Rule 3, the bug this rule exists for.

    Building rows by mutating one shared dict lets the previous listing's
    variation values survive into the next listing's parent row. The file looks
    valid and imports the wrong data, which is the worst possible combination.
    """
    variated = Listing(title="Variated", axis_1=Axis("Size", "Size"),
                       axis_2=Axis("Colour", "Colour"),
                       variations=[Variation("S", "Red"), Variation("M", "Blue")])
    plain = Listing(title="Plain")

    rows = build_rows(variated) + build_rows(plain)
    last = rows[-1]

    assert last["TITLE"] == "Plain"
    for column in ("VARIATION 1 TYPE", "VARIATION 1 NAME", "VARIATION 1 VALUES",
                   "VARIATION 2 TYPE", "VARIATION 2 NAME", "VARIATION 2 VALUES",
                   "Var Visibility", "Var Price", "Var Quantity", "Var SKU"):
        assert last[column] == "", "%s leaked: %r" % (column, last[column])


def test_photos_do_not_leak_between_listings():
    with_photos = Listing(title="A", photos=["a.jpg", "b.jpg"])
    without = Listing(title="B")
    rows = build_rows(with_photos) + build_rows(without)
    assert all(rows[-1][column] == "" for column in PHOTO_COLUMNS)


def test_blank_row_has_every_column_and_all_empty():
    row = blank_row()
    assert set(row) == set(COLUMNS)
    assert set(row.values()) == {""}


def test_each_call_returns_an_independent_dict():
    """The mechanical guarantee behind rule 3."""
    first, second = blank_row(), blank_row()
    first["TITLE"] = "mutated"
    assert second["TITLE"] == ""


# ---------------------------------------------------------------------------
# Rule 4: Var Visibility is the string "On"
# ---------------------------------------------------------------------------

def test_var_visibility_is_the_literal_string_on():
    """Not 'show', not 'TRUE', not '1'."""
    listing = Listing(title="V", axis_1=Axis("Size", "Size"),
                      variations=[Variation("S"), Variation("M")])
    for row in build_rows(listing):
        assert row["Var Visibility"] == "On" == VAR_VISIBILITY_ON


def test_a_listing_without_variations_has_no_visibility_value():
    assert build_rows(Listing(title="Plain"))[0]["Var Visibility"] == ""


# ---------------------------------------------------------------------------
# Photos and misc
# ---------------------------------------------------------------------------

def test_photos_fill_slots_in_order():
    listing = Listing(title="P", photos=["a.jpg", "b.jpg", "c.jpg"])
    row = build_rows(listing)[0]
    assert row["Photo 1"] == "a.jpg"
    assert row["Photo 3"] == "c.jpg"
    assert row["Photo 4"] == ""


def test_photos_beyond_ten_are_dropped_not_overflowed():
    listing = Listing(title="P", photos=["%d.jpg" % i for i in range(1, 15)])
    row = build_rows(listing)[0]
    assert row["Photo 10"] == "10.jpg"
    assert len([c for c in PHOTO_COLUMNS if row[c]]) == MAX_PHOTOS


def test_tags_and_materials_are_comma_joined():
    listing = Listing(title="T", tags=["a", "b"], materials=["resin", "pla"])
    row = build_rows(listing)[0]
    assert row["TAGS"] == "a,b" and row["MATERIALS"] == "resin,pla"


def test_variations_without_an_axis_is_an_error_not_a_silent_bad_file():
    listing = Listing(title="Broken", variations=[Variation("S")])
    with pytest.raises(ValueError, match="axis_1"):
        build_rows(listing)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def test_written_csv_has_the_documented_header(tmp_path):
    path = tmp_path / "out.csv"
    write_vela_csv([Listing(title="A")], str(path))
    header = next(csv.reader(path.open(encoding="utf-8")))
    assert header == COLUMNS
    assert "Photo 1" in header and "IMAGE1" not in header


def test_written_csv_round_trips_the_variation_layout(tmp_path):
    path = tmp_path / "out.csv"
    listing = Listing(title="V", axis_1=Axis("Size", "Size"),
                      axis_2=Axis("Colour", "Colour"),
                      variations=[Variation("S", "Red"), Variation("M", "Blue")])
    write_vela_csv([listing], str(path))

    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert len(rows) == 2
    assert rows[0]["TITLE"] == "V" and rows[1]["TITLE"] == ""
    assert all(r["VARIATION 2 NAME"] == "Colour" for r in rows)


# ---------------------------------------------------------------------------
# Post-import TODO
# ---------------------------------------------------------------------------

def test_todo_groups_listings_by_profile(tmp_path):
    """Grouped by profile because the workflow is to search for a shared
    keyword and bulk-apply, so the useful unit is the profile."""
    path = tmp_path / "todo.txt"
    listings = [
        Listing(title="A", profiles=["Name to write"]),
        Listing(title="B", profiles=["Name to write", "Font colour"]),
    ]
    grouped = write_post_import_todo(listings, str(path))

    assert grouped["Name to write"] == ["A", "B"]
    assert grouped["Font colour"] == ["B"]
    text = path.read_text(encoding="utf-8")
    assert "Name to write  (2 listings)" in text


def test_todo_says_so_when_nothing_is_needed(tmp_path):
    path = tmp_path / "todo.txt"
    assert write_post_import_todo([Listing(title="A")], str(path)) == {}
    assert "No Vela Profiles needed" in path.read_text(encoding="utf-8")
