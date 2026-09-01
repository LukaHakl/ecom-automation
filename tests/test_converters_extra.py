"""etsy_to_ebay, image_rows and collection_builder."""

from __future__ import annotations

import csv
import io

import pytest

from converters.collection_builder import (
    build_manual_rows, build_smart_tag_rows, load_products, summary as cb_summary,
)
from converters.etsy_to_ebay import (
    PIC_URL, Template, categorise, convert, convert_price, read_template,
    summary as ebay_summary, write_ebay_csv,
)
from converters.image_rows import (
    EXACT, KEYWORD, NO_IMAGES, OVERRIDE, SKIPPED, build_image_rows,
    load_overrides, match_images, summary as ir_summary,
)
from converters.product import Product


# ===========================================================================
# etsy_to_ebay
# ===========================================================================

EBAY_TEMPLATE = (
    "Info,Version=1.0.0,Template=fx_category_template\n"
    "Info,Action and Category ID are required\n"
    "*Action(SiteID=US|Country=US|Currency=USD|Version=1193),*Category,"
    "*Title,Description,PicURL,*Quantity,*StartPrice,CustomLabel\n"
)


def write_template(tmp_path, text=EBAY_TEMPLATE):
    path = tmp_path / "template.csv"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_the_info_preamble_is_captured(tmp_path):
    template = read_template(write_template(tmp_path))
    assert len(template.info_rows) == 2
    assert template.info_rows[0][0] == "Info"


def test_the_header_is_the_first_non_info_row(tmp_path):
    template = read_template(write_template(tmp_path))
    assert template.has_column(PIC_URL)
    assert len(template.header) == 8


def test_a_template_with_no_header_says_to_download_a_fresh_one(tmp_path):
    path = write_template(tmp_path, "Info,only\nInfo,rows\n")
    with pytest.raises(ValueError, match="Seller Hub"):
        read_template(path)


def test_the_preamble_is_written_back_verbatim(tmp_path):
    """eBay rejects a file whose Info rows have been altered."""
    template = read_template(write_template(tmp_path))
    out = tmp_path / "out.csv"
    write_ebay_csv(convert([], template, 1.08), template, str(out))

    rows = list(csv.reader(out.open(encoding="utf-8")))
    assert rows[0] == ["Info", "Version=1.0.0", "Template=fx_category_template"]
    assert rows[2] == template.header


def test_columns_are_resolved_by_name_not_index(tmp_path):
    """eBay revises the template; a hardcoded index is a silent time bomb."""
    reordered = EBAY_TEMPLATE.replace(
        "*Title,Description,PicURL", "PicURL,*Title,Description")
    template = read_template(write_template(tmp_path, reordered))
    product = Product(title="Golem", sku="A1", price="10", images=["a.jpg"])
    row = convert([product], template, 1.0).rows[0]
    assert row["PicURL"] == "a.jpg" and row["*Title"] == "Golem"


def test_required_star_prefixed_columns_are_matched(tmp_path):
    template = read_template(write_template(tmp_path))
    product = Product(title="Golem", sku="A1", price="10", images=["a.jpg"])
    row = convert([product], template, 1.0).rows[0]
    assert row["*Title"] == "Golem"


def test_the_action_column_is_set_despite_its_parameters(tmp_path):
    template = read_template(write_template(tmp_path))
    product = Product(title="G", sku="A1", images=["a.jpg"])
    row = convert([product], template, 1.0).rows[0]
    action = next(c for c in template.header if c.lstrip("*").startswith("Action"))
    assert row[action] == "Add"


def test_products_with_no_image_are_skipped_and_named(tmp_path):
    """eBay rejects an imageless listing, failing the whole import."""
    template = read_template(write_template(tmp_path))
    products = [Product(title="No pics", sku="A1"),
                Product(title="Has pics", sku="A2", images=["a.jpg"])]
    result = convert(products, template, 1.0)
    assert len(result.rows) == 1
    assert result.skipped_no_image == ["No pics"]


def test_duplicate_skus_are_collapsed():
    """Etsy repeats rows per variation."""
    template = Template(header=["*Title", "CustomLabel", "PicURL"])
    products = [Product(title="A", sku="X1", images=["a.jpg"]),
                Product(title="A again", sku="X1", images=["b.jpg"])]
    result = convert(products, template, 1.0)
    assert len(result.rows) == 1 and len(result.skipped_duplicate) == 1


def test_deduplication_uses_the_first_of_a_comma_separated_sku():
    template = Template(header=["*Title", "CustomLabel", "PicURL"])
    products = [Product(title="A", sku="X1, X2", images=["a.jpg"]),
                Product(title="B", sku="X1, X9", images=["b.jpg"])]
    assert len(convert(products, template, 1.0).rows) == 1


def test_currency_conversion_applies_the_supplied_rate():
    assert convert_price("10.00", 1.08) == "10.80"


def test_conversion_handles_a_comma_decimal_separator():
    assert convert_price("10,50", 1.0) == "10.50"


def test_an_unparseable_price_becomes_empty_not_a_crash():
    assert convert_price("free", 1.08) == "" and convert_price(None, 1.08) == ""


def test_the_rate_and_its_date_are_recorded_in_the_summary():
    """So a file can be traced back to the assumption that priced it."""
    text = ebay_summary(convert([], Template(header=["*Title"]), 1.08),
                        0, 1.08, "2026-08-01")
    assert "1.0800" in text and "2026-08-01" in text


@pytest.mark.parametrize("title,expected", [
    ("WW2 Panzer IV", "Model Vehicle"),
    ("Sherman Tank Platoon", "Model Vehicle"),
    ("Space Marine Squad", "Sci-Fi Miniature"),
    ("Cyber Mech Walker", "Sci-Fi Miniature"),
    ("Stone Golem", "Fantasy Miniature"),
    ("", "Fantasy Miniature"),
])
def test_keyword_categorisation(title, expected):
    assert categorise(title) == expected


def test_the_first_matching_rule_wins():
    rules = [{"keywords": ["tank"], "category": "First"},
             {"keywords": ["tank"], "category": "Second"}]
    assert categorise("A tank", rules) == "First"


def test_per_category_counts_are_reported():
    """Keyword categorisation misclassifies quietly; the count is where it shows."""
    template = Template(header=["*Title", "CustomLabel", "PicURL", "Category"])
    products = [Product(title="WW2 Tank", sku="A", images=["a.jpg"]),
                Product(title="Stone Golem", sku="B", images=["b.jpg"])]
    result = convert(products, template, 1.0)
    assert result.category_counts == {"Model Vehicle": 1, "Fantasy Miniature": 1}
    assert "Model Vehicle" in ebay_summary(result, 2, 1.0, "x")


def test_limit_stops_after_n_rows():
    template = Template(header=["*Title", "CustomLabel", "PicURL"])
    products = [Product(title="P%d" % i, sku="S%d" % i, images=["a.jpg"])
                for i in range(10)]
    assert len(convert(products, template, 1.0, limit=3).rows) == 3


def test_titles_are_capped_at_ebays_eighty_characters():
    template = Template(header=["*Title", "CustomLabel", "PicURL"])
    product = Product(title="x" * 200, sku="A", images=["a.jpg"])
    assert len(convert([product], template, 1.0).rows[0]["*Title"]) == 80


# ===========================================================================
# image_rows -- strict by design
# ===========================================================================

def test_an_exact_normalised_title_matches():
    products = [Product(title="Stone Golem 32mm", sku="H1")]
    sources = [Product(title="stone golem", images=["a.jpg"])]
    result = match_images(products, sources)[0]
    assert result.method == EXACT and result.images == ["a.jpg"]


def test_a_weak_similarity_is_skipped_not_matched():
    """The permissive version matched 649 of 586 products, ~160 wrongly."""
    products = [Product(title="Stone Golem", sku="H1")]
    sources = [Product(title="Frost Wyrm", images=["a.jpg"])]
    result = match_images(products, sources)[0]
    assert result.method == SKIPPED and result.images == []


def test_a_source_is_only_claimed_once():
    """More matches than products is the signature of the permissive bug."""
    products = [Product(title="Stone Golem", sku="H1"),
                Product(title="Stone Golem", sku="H2")]
    sources = [Product(title="Stone Golem", images=["a.jpg"])]
    results = match_images(products, sources)
    assert sum(1 for r in results if r.images) == 1


def test_an_override_matches_what_the_fuzzy_step_would_not():
    products = [Product(title="Product A", sku="H1")]
    sources = [Product(title="Totally Different Name", images=["a.jpg"])]
    overrides = {"H1": "Totally Different Name"}
    result = match_images(products, sources, overrides)[0]
    assert result.method == OVERRIDE and result.images == ["a.jpg"]


def test_none_marks_a_product_as_deliberately_imageless():
    """Otherwise the skip list fills with known-fine entries and stops
    being read."""
    products = [Product(title="Stone Golem", sku="H1")]
    sources = [Product(title="Stone Golem", images=["a.jpg"])]
    result = match_images(products, sources, {"H1": NO_IMAGES})[0]
    assert result.method == SKIPPED and result.images == []


def test_a_high_scoring_keyword_match_is_allowed():
    products = [Product(title="Stone Golem Large", sku="H1")]
    sources = [Product(title="Large Stone Golem", images=["a.jpg"])]
    result = match_images(products, sources)[0]
    assert result.method == KEYWORD and result.score >= 0.9


def test_lowering_the_threshold_is_what_broke_the_old_version():
    products = [Product(title="Stone Golem Large Edition", sku="H1")]
    sources = [Product(title="Stone Wyrm", images=["a.jpg"])]
    assert match_images(products, sources)[0].method == SKIPPED
    assert match_images(products, sources, threshold=0.1)[0].method == KEYWORD


def test_image_rows_set_top_row_on_the_first_only():
    results = match_images([Product(title="A", sku="H1")],
                           [Product(title="A", images=["a.jpg", "b.jpg", "c.jpg"])])
    rows = build_image_rows(results)
    assert [r["Image Position"] for r in rows] == ["1", "2", "3"]
    assert [r["Top Row"] for r in rows] == ["TRUE", "", ""]


def test_skipped_products_produce_no_rows():
    results = match_images([Product(title="A", sku="H1")],
                           [Product(title="Z", images=["a.jpg"])])
    assert build_image_rows(results) == []


def test_every_skipped_title_is_printed_not_a_sample():
    """The skip list is the deliverable; a truncated one is a shorter
    to-do list than the truth."""
    products = [Product(title="Unmatchable %d" % i, sku="H%d" % i)
                for i in range(30)]
    text = ir_summary(match_images(products, []))
    for i in range(30):
        assert "Unmatchable %d" % i in text


def test_overrides_load_from_a_two_column_csv(tmp_path):
    path = tmp_path / "o.csv"
    path.write_text("# comment\nH1,Some Title\nH2,__none__\n", encoding="utf-8")
    assert load_overrides(str(path)) == {"H1": "Some Title", "H2": NO_IMAGES}


def test_a_missing_overrides_file_is_not_an_error():
    assert load_overrides(None) == {} and load_overrides("nope.csv") == {}


# ===========================================================================
# collection_builder
# ===========================================================================

def csv_handle(text):
    return io.StringIO(text)


def test_rows_with_no_title_are_skipped_and_reported():
    """A bare quantity with no title was observed in a real top-500 export."""
    load = load_products(csv_handle("Title,Quantity\nStone Golem,5\n,12\n"))
    assert len(load.rows) == 1
    assert len(load.skipped) == 1 and load.skipped[0][0] == 3


def test_the_skip_reason_shows_what_the_junk_row_carried():
    load = load_products(csv_handle("Title,Quantity\n,12\n"))
    assert "12" in load.skipped[0][1]


def test_smart_tag_mode_sets_tags_command_replace():
    rows = build_smart_tag_rows([load_products(
        csv_handle("Title\nStone Golem\n")).rows[0]], "Bestseller")
    assert rows[0]["Tags Command"] == "REPLACE"
    assert "Bestseller" in rows[0]["Tags"]


def test_existing_tags_survive_a_replace():
    """REPLACE writes the full set; dropping other tags would drop the product
    out of every other Smart Collection it belonged to."""
    load = load_products(csv_handle("Title,Tags\nGolem,\"Fantasy, Infantry\"\n"))
    tags = build_smart_tag_rows(load.rows, "Bestseller")[0]["Tags"]
    assert "Fantasy" in tags and "Infantry" in tags and "Bestseller" in tags


def test_an_already_present_tag_is_not_duplicated():
    load = load_products(csv_handle("Title,Tags\nGolem,Bestseller\n"))
    assert build_smart_tag_rows(load.rows, "Bestseller")[0]["Tags"] == "Bestseller"


def test_a_handle_is_derived_when_the_export_has_none():
    load = load_products(csv_handle("Title\nStone Golem!\n"))
    assert build_smart_tag_rows(load.rows, "T")[0]["Handle"] == "stone-golem"


def test_manual_mode_writes_membership_directly():
    load = load_products(csv_handle("Title\nStone Golem\n"))
    row = build_manual_rows(load.rows, "Bestsellers")[0]
    assert row["Collection"] == "Bestsellers" and row["Title"] == "Stone Golem"


def test_the_summary_reports_in_out_and_skipped():
    load = load_products(csv_handle("Title,Quantity\nA,5\n,12\n"))
    text = cb_summary(load, build_smart_tag_rows(load.rows, "T"), "smart-tags", "T")
    assert "rows in:      2" in text
    assert "rows out:     1" in text
    assert "rows skipped: 1" in text


def test_a_wholly_blank_line_is_not_counted_as_a_skipped_row():
    """csv.DictReader drops blank lines before they reach us, so they are not
    junk to report -- only rows that exist but carry no title are."""
    load = load_products(csv_handle("Title\nA\n\n"))
    assert len(load.rows) == 1 and load.skipped == []
