"""Product model, readers, and reconciliation."""

from __future__ import annotations

import csv

import pytest

from converters.product import (
    Product, Variant, all_skus, first_sku, normalise_title, round_trip_report,
    title_similarity,
)
from converters.readers import (
    detect_format, photo_fields, read_altera_rows, read_catalogue, read_vela_rows,
)
from converters.reconcile import (
    A_ONLY, B_ONLY, LINKED, REVIEW, reconcile_by_sku, reconcile_by_title,
    summarise, write_csv,
)


# ---------------------------------------------------------------------------
# SKU handling
# ---------------------------------------------------------------------------

def test_the_first_sku_is_taken_from_a_comma_separated_field():
    """Etsy repeats rows per variation and its SKU field holds a list."""
    assert first_sku("AC-001, AC-002, AC-003") == "AC-001"


def test_a_single_sku_passes_through():
    assert first_sku("AC-001") == "AC-001"


def test_an_empty_sku_field_is_empty_not_an_error():
    assert first_sku("") == "" and first_sku(None) == ""


def test_all_skus_splits_and_strips():
    assert all_skus(" A , B ,, C ") == ["A", "B", "C"]


def test_primary_sku_uses_the_first():
    assert Product(sku="X-1, X-2").primary_sku == "X-1"


# ---------------------------------------------------------------------------
# Title normalisation
# ---------------------------------------------------------------------------

def test_scale_markers_are_stripped():
    assert "32" not in normalise_title("Stone Golem 32mm")
    assert "56" not in normalise_title("Tank 1:56")


def test_format_noise_is_stripped():
    assert normalise_title("Stone Golem STL pre-supported") == "stone golem"


def test_generic_nouns_are_stripped():
    assert normalise_title("Stone Golem Miniature") == "stone golem"


def test_punctuation_and_case_are_normalised():
    assert normalise_title("Stone-Golem, Large!") == "stone golem large"


def test_identical_titles_score_one():
    assert title_similarity("Stone Golem", "Stone Golem") == 1.0


def test_word_order_does_not_matter():
    """Jaccard rather than a sequence ratio: order varies between platforms,
    vocabulary does not."""
    assert title_similarity("Stone Golem, Large", "Large Stone Golem") == 1.0


def test_unrelated_titles_score_low():
    assert title_similarity("Stone Golem", "Frost Wyrm") < 0.3


def test_an_empty_title_scores_zero_rather_than_crashing():
    assert title_similarity("", "Stone Golem") == 0.0


# ---------------------------------------------------------------------------
# Round-trip reporting
# ---------------------------------------------------------------------------

def test_an_unchanged_product_reports_no_loss():
    product = Product(title="A", sku="S", images=["a.jpg"])
    assert round_trip_report(product, Product(title="A", sku="S",
                                              images=["a.jpg"])) == []


def test_a_dropped_field_is_named():
    before = Product(title="A", material="Resin")
    lost = round_trip_report(before, Product(title="A"))
    assert len(lost) == 1 and "material" in lost[0]


# ---------------------------------------------------------------------------
# Format detection and reading
# ---------------------------------------------------------------------------

def test_altera_headers_are_detected():
    assert detect_format(["Handle", "Title", "Image Src"]) == "altera"


def test_vela_headers_are_detected():
    assert detect_format(["TITLE", "SKU", "Photo 1"]) == "vela"


def test_an_unknown_header_is_not_guessed_at():
    assert detect_format(["foo", "bar"]) == "unknown"


def test_photo_fields_come_back_in_slot_order():
    assert photo_fields(["TITLE", "IMAGE10", "IMAGE2", "IMAGE1"]) \
        == ["IMAGE1", "IMAGE2", "IMAGE10"]


def test_vela_variation_rows_collapse_into_their_parent():
    products = read_vela_rows([
        {"TITLE": "Dragon", "SKU": "S1", "Photo 1": "a.jpg"},
        {"TITLE": "", "Photo 1": "b.jpg"},
        {"TITLE": "Golem", "SKU": "S2", "Photo 1": "c.jpg"},
    ], ["TITLE", "SKU", "Photo 1"])
    assert len(products) == 2
    assert products[0].images == ["a.jpg", "b.jpg"]


def test_altera_blocks_collect_images_and_variants():
    products = read_altera_rows([
        {"Handle": "d", "Title": "Dragon", "Image Src": "a.jpg", "Variant SKU": "S1"},
        {"Handle": "d", "Image Src": "b.jpg"},
    ])
    assert len(products) == 1
    assert products[0].images == ["a.jpg", "b.jpg"]
    assert products[0].sku == "S1"


def test_a_bom_prefixed_export_still_parses(tmp_path):
    path = tmp_path / "bom.csv"
    path.write_bytes("TITLE,SKU,Photo 1\nDragon,S1,a.jpg\n".encode("utf-8-sig"))
    assert read_catalogue(str(path))[0].title == "Dragon"


def test_an_unidentifiable_file_says_what_it_saw(tmp_path):
    path = tmp_path / "x.csv"
    path.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Header was"):
        read_catalogue(str(path))


# ---------------------------------------------------------------------------
# Reconciliation by SKU
# ---------------------------------------------------------------------------

def test_shared_skus_are_linked():
    a = [Product(title="A", sku="X1"), Product(title="B", sku="X2")]
    b = [Product(title="A2", sku="X1")]
    rows = reconcile_by_sku(a, b)
    assert [r.status for r in rows] == [LINKED, A_ONLY]


def test_b_only_skus_are_reported():
    rows = reconcile_by_sku([], [Product(title="B", sku="X9")])
    assert rows[0].status == B_ONLY and rows[0].key == "X9"


def test_comma_separated_skus_all_count_as_join_keys():
    """Etsy's variation rows scatter a product's SKUs across cells."""
    a = [Product(title="A", sku="X1, X2")]
    b = [Product(title="B", sku="X2")]
    assert [r.status for r in reconcile_by_sku(a, b)] == [LINKED, A_ONLY]


def test_whitespace_in_sku_fields_does_not_break_the_join():
    a = [Product(title="A", sku="  X1  ")]
    b = [Product(title="B", sku="X1")]
    assert reconcile_by_sku(a, b)[0].status == LINKED


# ---------------------------------------------------------------------------
# Reconciliation by title
# ---------------------------------------------------------------------------

def test_exact_normalised_titles_link():
    a = [Product(title="Stone Golem 32mm", sku="A1")]
    b = [Product(title="stone golem", sku="B1")]
    assert reconcile_by_title(a, b)[0].status == LINKED


def test_a_weak_title_match_is_flagged_for_review_not_asserted():
    """Title matching produces false positives across creators. A REVIEW row
    costs thirty seconds; a wrong link costs a mis-synced inventory."""
    a = [Product(title="Stone Golem Large", sku="A1")]
    b = [Product(title="Stone Wyrm Small", sku="B1")]
    row = reconcile_by_title(a, b)[0]
    assert row.status == REVIEW and 0 < row.score < 0.85


def test_a_target_can_only_be_claimed_once():
    """Without this, a generic title links to everything resembling it."""
    a = [Product(title="Terrain Set", sku="A1"), Product(title="Terrain Set", sku="A2")]
    b = [Product(title="Terrain Set", sku="B1")]
    rows = reconcile_by_title(a, b)
    assert sum(1 for r in rows if r.status == LINKED) == 1


def test_unmatched_target_products_are_reported_as_b_only():
    a = [Product(title="Stone Golem", sku="A1")]
    b = [Product(title="Stone Golem", sku="B1"), Product(title="Frost Wyrm", sku="B2")]
    assert sum(1 for r in reconcile_by_title(a, b) if r.status == B_ONLY) == 1


def test_the_review_threshold_is_configurable():
    a = [Product(title="Stone Golem Large", sku="A1")]
    b = [Product(title="Stone Golem Small", sku="B1")]
    assert reconcile_by_title(a, b, threshold=0.99)[0].status == REVIEW
    assert reconcile_by_title(a, b, threshold=0.1)[0].status == LINKED


# ---------------------------------------------------------------------------
# Summary and output
# ---------------------------------------------------------------------------

def test_the_summary_reports_counts_and_percentages():
    a = [Product(title="A", sku="X1"), Product(title="B", sku="X2")]
    b = [Product(title="A", sku="X1")]
    text = summarise(reconcile_by_sku(a, b))
    assert "LINKED" in text and "A_ONLY" in text and "%" in text


def test_a_low_link_rate_prompts_trying_title_mode():
    """The real case: 6.4% linked by SKU, because the conventions diverged --
    technically true and completely useless."""
    a = [Product(title="A%d" % i, sku="X%d" % i) for i in range(10)]
    b = [Product(title="B%d" % i, sku="Y%d" % i) for i in range(10)]
    assert "--by title" in summarise(reconcile_by_sku(a, b))


def test_the_csv_carries_a_status_per_row(tmp_path):
    path = tmp_path / "out.csv"
    a = [Product(title="A", sku="X1")]
    write_csv(reconcile_by_sku(a, a), str(path))
    rows = list(csv.reader(path.open(encoding="utf-8")))
    assert rows[0] == ["status", "key", "a_title", "b_title", "score"]
    assert rows[1][0] == LINKED
