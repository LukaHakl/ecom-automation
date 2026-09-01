"""Vela listing builder: photo slot ordering, combination trimming, pricing."""

from __future__ import annotations

from builders.vela_builder import (
    DEFAULT_PHOTO_LAYOUT, MAX_PHOTOS, VARIATION_CEILING, ProductSpec,
    build_listing, combinations, final_price, format_summary, order_photos,
)


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

def test_the_multiplier_is_applied_and_rounded():
    assert final_price(15.00, 1.60) == "24.00"


def test_pricing_rounds_to_two_places():
    assert final_price(9.99, 1.60) == "15.98"


def test_a_multiplier_of_one_changes_nothing():
    assert final_price(12.50, 1.0) == "12.50"


def test_both_prices_appear_in_the_summary_so_the_arithmetic_is_auditable():
    spec = ProductSpec(sku="S", title="T", base_price=15.0)
    _listing, summary = build_listing(spec, multiplier=1.6)
    assert summary["base_price"] == "15.00" and summary["final_price"] == "24.00"


# ---------------------------------------------------------------------------
# Photo slot ordering
# ---------------------------------------------------------------------------

def test_the_full_layout_fills_slots_in_merchandising_order():
    groups = {
        "ai_scene": ["scene.jpg"],
        "originals": ["o1.jpg", "o2.jpg", "o3.jpg", "o4.jpg", "o5.jpg", "o6.jpg"],
        "infographics": ["i1.jpg", "i2.jpg", "i3.jpg"],
        "cover": ["cover.jpg"],
    }
    urls, dropped = order_photos(groups)
    assert urls == ["scene.jpg", "o1.jpg", "o2.jpg",
                    "i1.jpg", "i2.jpg", "i3.jpg",
                    "o3.jpg", "o4.jpg", "o5.jpg", "cover.jpg"]
    assert len(urls) == MAX_PHOTOS
    assert dropped == 1       # o6: the layout has three slots, not four


def test_a_listing_without_infographics_collapses_the_gap():
    """Etsy renders an empty slot as a missing image, not a skipped one."""
    groups = {"ai_scene": ["scene.jpg"],
              "originals": ["o1.jpg", "o2.jpg", "o3.jpg"],
              "cover": ["cover.jpg"]}
    urls, dropped = order_photos(groups)
    assert urls == ["scene.jpg", "o1.jpg", "o2.jpg", "o3.jpg", "cover.jpg"]
    assert "" not in urls and dropped == 0


def test_originals_resume_where_the_earlier_slot_left_off():
    """The layout takes originals twice; the second block must not repeat the
    first block's photos."""
    groups = {"originals": ["o1.jpg", "o2.jpg", "o3.jpg", "o4.jpg"]}
    urls, _ = order_photos(groups)
    assert urls == ["o1.jpg", "o2.jpg", "o3.jpg", "o4.jpg"]
    assert len(urls) == len(set(urls))


def test_a_missing_group_contributes_nothing_and_does_not_crash():
    assert order_photos({})[0] == []


def test_a_single_string_is_accepted_where_a_list_is_expected():
    urls, _ = order_photos({"ai_scene": "scene.jpg"})
    assert urls == ["scene.jpg"]


def test_photos_the_layout_has_no_room_for_are_counted_as_dropped():
    """The layout allots originals 2 + 3 slots, so 20 supplied means 15 lost --
    and a photo handed to the builder and silently discarded is exactly the
    failure this repo exists to surface."""
    groups = {"originals": ["o%d.jpg" % i for i in range(20)]}
    urls, dropped = order_photos(groups)
    assert len(urls) == 5
    assert dropped == 15


def test_dropped_photos_are_counted_when_more_are_supplied_than_fit():
    groups = {"ai_scene": ["a.jpg"] * 3, "originals": ["o.jpg"] * 6,
              "infographics": ["i.jpg"] * 3, "cover": ["c.jpg"] * 3}
    urls, dropped = order_photos(groups)
    assert len(urls) == MAX_PHOTOS and dropped == 5


def test_the_layout_is_configurable():
    layout = [("cover", 1), ("ai_scene", 1)]
    urls, _ = order_photos({"ai_scene": ["s.jpg"], "cover": ["c.jpg"]}, layout)
    assert urls == ["c.jpg", "s.jpg"]


def test_the_default_layout_totals_ten_slots():
    assert sum(slots for _, slots in DEFAULT_PHOTO_LAYOUT) == MAX_PHOTOS


# ---------------------------------------------------------------------------
# Variation combinations
# ---------------------------------------------------------------------------

def test_two_axes_multiply():
    spec = ProductSpec(sku="S", title="T",
                       axis_1={"values": ["S", "M"]},
                       axis_2={"values": ["Red", "Blue"]})
    assert len(combinations(spec)) == 4


def test_a_single_axis_yields_one_value_per_combination():
    spec = ProductSpec(sku="S", title="T", axis_1={"values": ["S", "M", "L"]})
    assert combinations(spec) == [("S", ""), ("M", ""), ("L", "")]


def test_no_axis_yields_nothing():
    assert combinations(ProductSpec(sku="S", title="T")) == []


def test_excluded_pairs_are_removed():
    """Trimming is declared in the input; a hand-edited CSV cannot be rebuilt
    and every rebuild silently reintroduces what was trimmed."""
    spec = ProductSpec(sku="S", title="T",
                       axis_1={"values": ["S", "XL"]},
                       axis_2={"values": ["Red", "Gold"]},
                       exclude=[["XL", "Gold"]])
    pairs = combinations(spec)
    assert ("XL", "Gold") not in pairs and len(pairs) == 3


def test_an_exclusion_removes_only_that_pair_not_every_value():
    spec = ProductSpec(sku="S", title="T",
                       axis_1={"values": ["S", "XL"]},
                       axis_2={"values": ["Red", "Gold"]},
                       exclude=[["XL", "Gold"]])
    pairs = combinations(spec)
    assert ("XL", "Red") in pairs and ("S", "Gold") in pairs


# ---------------------------------------------------------------------------
# build_listing
# ---------------------------------------------------------------------------

def test_a_listing_with_no_variations_builds_cleanly():
    spec = ProductSpec(sku="S", title="T", base_price=10.0,
                       photos={"ai_scene": ["a.jpg"]})
    listing, summary = build_listing(spec, 1.6)
    assert listing.variations == [] and listing.axis_1 is None
    assert summary["variations"] == 0 and summary["photos"] == 1


def test_axes_are_carried_onto_the_listing():
    spec = ProductSpec(sku="S", title="T",
                       axis_1={"type": "Size", "name": "Size",
                               "values": ["S", "M"]},
                       axis_2={"type": "Colour", "name": "Edge colour",
                               "values": ["Red"]})
    listing, _ = build_listing(spec, 1.6)
    assert listing.axis_1.type == "Size"
    assert listing.axis_2.name == "Edge colour"
    assert len(listing.variations) == 2


def test_every_variation_carries_a_unique_sku():
    spec = ProductSpec(sku="BASE", title="T", axis_1={"values": ["S", "M", "L"]})
    listing, _ = build_listing(spec, 1.6)
    skus = [v.sku for v in listing.variations]
    assert len(set(skus)) == len(skus)
    assert all(sku.startswith("BASE-") for sku in skus)


def test_a_video_is_reported_in_the_summary():
    spec = ProductSpec(sku="S", title="T", video="clip.mp4")
    _listing, summary = build_listing(spec, 1.6)
    assert summary["video_mapped"] is True


def test_profiles_reach_the_listing_for_the_post_import_todo():
    spec = ProductSpec(sku="S", title="T", profiles=["Name to write"])
    listing, _ = build_listing(spec, 1.6)
    assert listing.profiles == ["Name to write"]


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def test_the_summary_warns_above_the_variation_ceiling():
    spec = ProductSpec(sku="S", title="T",
                       axis_1={"values": ["v%d" % i for i in range(10)]},
                       axis_2={"values": ["c%d" % i for i in range(9)]})
    _listing, summary = build_listing(spec, 1.6)
    assert summary["variations"] == 90 and summary["over_ceiling"]
    assert "exceeds the ceiling" in format_summary([summary])


def test_the_summary_lists_one_row_per_listing():
    summaries = [build_listing(ProductSpec(sku="S%d" % i, title="T"), 1.6)[1]
                 for i in range(3)]
    text = format_summary(summaries)
    assert "3 listings" in text
    for i in range(3):
        assert "S%d" % i in text


def test_the_summary_warns_about_truncated_photos():
    spec = ProductSpec(sku="S", title="T",
                       photos={"ai_scene": ["a.jpg"] * 3,
                               "originals": ["o.jpg"] * 6,
                               "infographics": ["i.jpg"] * 3,
                               "cover": ["c.jpg"] * 3})
    _listing, summary = build_listing(spec, 1.6)
    assert "not placed" in format_summary([summary])
