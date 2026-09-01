"""Altera/Shopify row model, tags-not-collections, and the blocked-host guard."""

from __future__ import annotations

import csv

import pytest

from common.altera import (
    COLUMNS, MERGE, REPLACE, BlockedImageHost, Product, Variant, blocked_hosts,
    build_rows, check_image_hosts, render_description, resolve_collections,
    video_note, write_altera_csv,
)


# ---------------------------------------------------------------------------
# Row model
# ---------------------------------------------------------------------------

def test_a_simple_product_is_one_row():
    rows = build_rows(Product(handle="dragon", title="Dragon"))
    assert len(rows) == 1
    assert rows[0]["Handle"] == "dragon"
    assert rows[0]["Command"] == MERGE


def test_top_row_is_true_on_the_first_row_only():
    product = Product(handle="d", title="D", images=["a.jpg", "b.jpg", "c.jpg"])
    rows = build_rows(product)
    assert rows[0]["Top Row"] == "TRUE"
    assert all(row["Top Row"] == "" for row in rows[1:])


def test_image_positions_are_one_indexed_and_sequential():
    product = Product(handle="d", title="D", images=["a.jpg", "b.jpg", "c.jpg"])
    rows = build_rows(product)
    assert [r["Image Position"] for r in rows] == ["1", "2", "3"]
    assert all(r["Image Command"] == MERGE for r in rows)


def test_the_first_image_rides_on_the_first_row():
    rows = build_rows(Product(handle="d", title="D", images=["a.jpg"]))
    assert len(rows) == 1 and rows[0]["Image Src"] == "a.jpg"


def test_extra_variants_get_their_own_rows():
    product = Product(handle="d", title="D", variants=[
        Variant(sku="S1", price="10", option_name="Size", option_value="S"),
        Variant(sku="S2", price="12", option_name="Size", option_value="M"),
    ])
    rows = build_rows(product)
    assert len(rows) == 2
    assert rows[0]["Variant SKU"] == "S1" and rows[0]["Title"] == "D"
    assert rows[1]["Variant SKU"] == "S2" and rows[1]["Title"] == ""


def test_every_row_in_a_block_carries_the_handle():
    product = Product(handle="d", title="D", images=["a.jpg", "b.jpg"],
                      variants=[Variant(sku="S1"), Variant(sku="S2")])
    assert all(row["Handle"] == "d" for row in build_rows(product))


# ---------------------------------------------------------------------------
# Tags, not a manual collections column
# ---------------------------------------------------------------------------

def test_tags_command_is_replace():
    """Collection membership follows from tags matching the store's Smart
    Collection rules -- an earlier version wrote invented names into a manual
    column and both the mechanism and the names were wrong."""
    rows = build_rows(Product(handle="d", title="D", tags=["Infantry", "Sci-Fi"]))
    assert rows[0]["Tags"] == "Infantry, Sci-Fi"
    assert rows[0]["Tags Command"] == REPLACE


def test_collections_resolve_when_every_required_tag_is_present():
    mapping = [{"tags": ["Infantry", "Imperial Guard"],
                "resolves_to": "Sci-Fi Imperial Infantry"}]
    assert resolve_collections(["Infantry", "Imperial Guard", "Extra"], mapping) \
        == ["Sci-Fi Imperial Infantry"]


def test_a_partial_tag_match_resolves_to_nothing():
    """Smart Collection rules are conjunctive."""
    mapping = [{"tags": ["Infantry", "Imperial Guard"], "resolves_to": "X"}]
    assert resolve_collections(["Infantry"], mapping) == []


def test_collection_matching_ignores_case_and_padding():
    mapping = [{"tags": [" infantry "], "resolves_to": "X"}]
    assert resolve_collections(["Infantry"], mapping) == ["X"]


def test_several_rules_can_resolve_at_once():
    mapping = [{"tags": ["A"], "resolves_to": "One"},
               {"tags": ["B"], "resolves_to": "Two"}]
    assert resolve_collections(["A", "B"], mapping) == ["One", "Two"]


# ---------------------------------------------------------------------------
# Description template
# ---------------------------------------------------------------------------

def test_description_follows_the_template_literally():
    text = render_description("Stone Golem", "ACME", "32mm", "Resin",
                              "Fantasy", "A heavy hitter for any table.")
    assert text.startswith("Stone Golem — by ACME")
    assert "Creator: ACME" in text
    assert "Scale: 32mm" in text
    assert text.rstrip().endswith("A heavy hitter for any table.")


def test_description_has_no_marketing_filler():
    """Generated copy pulls hard toward boilerplate. The template is literal."""
    text = render_description("T", "C", "S", "M", "Cat", "Lead.")
    lowered = text.lower()
    for filler in ("why choose", "features", "tariff", "free shipping",
                   "satisfaction"):
        assert filler not in lowered


# ---------------------------------------------------------------------------
# Vendor is not Creator
# ---------------------------------------------------------------------------

def test_vendor_is_the_shop_not_the_creator():
    product = Product(handle="d", title="D", vendor="My Shop",
                      body_html=render_description("D", "ACME", "32mm",
                                                   "Resin", "Fantasy", "Lead."))
    row = build_rows(product)[0]
    assert row["Vendor"] == "My Shop"
    assert "Creator: ACME" in row["Body HTML"]
    assert "ACME" != row["Vendor"]


# ---------------------------------------------------------------------------
# Blocked image hosts
# ---------------------------------------------------------------------------

def test_a_blocked_host_is_detected():
    assert blocked_hosts(["https://cults3d.com/x.jpg"]) == ["https://cults3d.com/x.jpg"]


def test_a_working_host_is_not_flagged():
    assert blocked_hosts(["https://i.etsystatic.com/x.jpg"]) == []


def test_a_blocked_host_fails_the_build_loudly():
    """A warning in a long log is not seen; an import that silently drops
    media is found weeks later by a customer."""
    products = [Product(handle="d", title="D", images=["https://cults3d.com/x.jpg"])]
    with pytest.raises(BlockedImageHost, match="rehost"):
        check_image_hosts(products)


def test_the_error_names_the_first_offender():
    products = [Product(handle="ok", title="A", images=["https://i.etsystatic.com/a.jpg"]),
                Product(handle="bad", title="B", images=["https://cults3d.com/b.jpg"])]
    with pytest.raises(BlockedImageHost, match="bad"):
        check_image_hosts(products)


def test_clean_products_pass_the_check():
    check_image_hosts([Product(handle="d", title="D",
                               images=["https://i.etsystatic.com/a.jpg"])])


def test_the_blocked_list_is_overridable():
    with pytest.raises(BlockedImageHost):
        check_image_hosts([Product(handle="d", images=["https://other.net/a.jpg"])],
                          blocked=["other.net"])


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def test_written_csv_has_the_documented_header(tmp_path):
    path = tmp_path / "out.csv"
    write_altera_csv([Product(handle="d", title="D")], str(path))
    assert next(csv.reader(path.open(encoding="utf-8"))) == COLUMNS


def test_writing_refuses_a_blocked_host_by_default(tmp_path):
    products = [Product(handle="d", images=["https://cults3d.com/x.jpg"])]
    with pytest.raises(BlockedImageHost):
        write_altera_csv(products, str(tmp_path / "out.csv"))


def test_video_columns_are_reported_as_unhandled():
    assert "Video" in video_note([Product(handle="d")])
