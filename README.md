# ecom-automation

A toolkit for building and reconciling e-commerce catalogues across Etsy,
Shopify and eBay, where the platforms have no API for what you need and the
import formats fail silently when you get them wrong.

```
$ python -m converters.reconcile --a etsy.csv --b shopify.csv --by sku

rows in: A 14792, B 12104

=== Reconciliation ===
  LINKED      948  (6.4%)
  A_ONLY    13844  (93.6%)

  Fewer than half of all products are linked. If the two catalogues are meant
  to hold the same inventory, they are not. Try --by title before concluding
  that: divergent SKU conventions produce exactly this result on catalogues
  that are in fact identical.
```

That output is the motivating case. Two stores that were supposed to hold
identical inventory shared 948 of 14,792 SKUs, and nobody knew because nothing
had ever compared them.

## The problem

Bulk catalogue work on these platforms is a set of formats that fail quietly:

- Etsy has **no native bulk import for new listings**, so new listings go
  through Vela's CSV — which has a variation row layout with four separate
  rules that each break the import, and a photo column naming difference that
  imports *successfully with zero photos attached*.
- Shopify's bulk editor takes a handle-grouped row model where collection
  membership must be expressed as tags matching Smart Collection rules, not as
  a collections column.
- Neither platform's export can be fed to the other without a translation step,
  and the two stores' SKU conventions have diverged far enough that the obvious
  join returns 6%.

Every rule encoded here was learned by having an import fail, usually without
an error message.

## Approach

**A normalised product model in the middle.** Every reader parses a platform
export into a `Product`; every writer emits `Product`s into a platform format.
No reader ever talks to a writer directly. With N platforms, direct conversion
means N×(N−1) converters each with their own quirks; through a normalised model
it is N readers plus N writers, and a platform changing a column name touches
one file.

**Format rules live in the writer, not the caller.** `write_vela_csv` takes
listings and produces correct row layouts, because every caller trusted to get
the variation rules right has eventually got them wrong.

**Everything reports what it dropped.** Rows in, rows out, rows skipped, and
why. Silent truncation reads as complete coverage, and most of the expensive
failures in this domain are silent.

## What's in it

### `common/vela.py` — Etsy bulk listing

The four variation rules, enforced rather than documented:

1. The first variation is **embedded in the parent row**. A parent plus N
   variation rows produces N+1 variations on Etsy, and the phantom extra is the
   parent's own values.
2. Every child row **repeats both axis label fields**. Blank ones fail.
3. Parent rows have every `Var*` field **explicitly written**. Building rows by
   mutating one shared dict leaks the previous listing's values into the next
   parent — producing a file that looks valid and imports the wrong data.
4. `Var Visibility` is the literal string `On`. Not `show`, not `TRUE`, not `1`.

Plus the photo column trap: Etsy *exports* `IMAGE1`, Vela *imports* `Photo 1`.
Feed Vela the wrong spelling and the import succeeds with no photos. So
`normalise_photo_columns` accepts all three spellings and `check_photo_coverage`
warns loudly below 95%.

### `common/altera.py` — Shopify bulk edit

Handle-grouped rows, `Top Row = TRUE` on the first row of each block only, and
collections done properly: structured tags with `Tags Command = REPLACE`, never
a manual collections column. An earlier version of this pipeline invented
collection names in a manual column and both the mechanism and the names were
wrong.

Also fails the build when a product references an image host Shopify's fetcher
cannot read, rather than producing a file that imports with missing media.

### `common/text_clean.py` — copy rewriting

Source listings sell a *digital file*; these listings sell a physical printed
object. Strips STL/file-format/instant-download language, Patreon blocks, and
all external links — Etsy prohibits linking to outside sales channels, so a
leftover link risks the listing, not just its quality.

Also applies a config-supplied IP denylist. **That list is never committed.**
Some rightsholders in this space pursue takedowns against proxy and lookalike
terms rather than only their own trademarks, so publishing the list would
publish a map of which terms attract enforcement.

Every removal is reported, because these are the edits you get asked to justify.

### `common/progress.py` — resumability

Not optional. These jobs run 10–40 minutes against sites that rate-limit and
routinely die partway through. Checkpoints are atomic, resumes state the skip
count out loud, and `ConsecutiveFailures` halts a job that has started failing
systematically — on one ~1,100-product run a scraper was blocked from roughly
product 380 onward, kept going, and wrote 700 rows of plausible-looking garbage
that only surfaced as missing images much later.

### `converters/` — the model, readers, and four converters

`reconcile.py` compares two catalogues by SKU or by normalised title. Title
matching produces false positives across creators, so anything below the
threshold is reported `REVIEW` rather than `MATCHED`.

`etsy_to_ebay.py` maps an Etsy export onto eBay's bulk template. The template's
leading `Info` rows are copied through verbatim — eBay rejects a file without
them — and the header is read from *your* downloaded template rather than
hardcoded, because eBay revises the column set and a hardcoded 93-column list
is a time bomb. Columns resolve by name, never by index.

`image_rows.py` attaches images to products across two exports, and is
deliberately strict. A permissive earlier version matched 649 of 586 products —
more matches than products — of which ~160 were wrong. The strict version
matched 489 and skipped 97, which was the better outcome: a skipped product
keeps its existing images and nobody notices; a wrong image is a product page
showing someone else's product. Match order is exact title → override table →
strict keyword, with **no fallback to loose fuzzy matching**. Every skipped
title is printed in full.

`collection_builder.py` assigns products to a collection via smart tags
(preferred) or manual membership, validating out the junk rows real bestseller
exports contain.

### `builders/` — catalogue construction

`vela_builder.py` handles photo slot ordering as a merchandising decision and
combination trimming declared in the input rather than hand-edited into the CSV.
`build_listings.py` is the entry point: products YAML in, Vela CSV out, with
`--only SKU1,SKU2` because the discipline here is to build two listings and
import them before running the full set.

`video_map.py` assigns videos to listings by photo-to-video timestamp
proximity. Session-level guessing — "these were shot the same day, so distribute
them across that day's listings" — produces wrong assignments and is explicitly
rejected: a day holds many listings, and nearness *within* the day is the whole
signal. Genuinely ambiguous videos go to a review file with their candidates
rather than being assigned.

### `scrapers/` — creator storefronts

One parameterised scraper with a small adapter per site, not one script per
creator — the previous incarnation had five copies of the same bugs. Three
interchangeable URL-acquisition modes (live scroll, saved HTML, open tabs), and
`parse_product` takes **HTML text rather than a driver**, which is what makes
the parsing testable against fixtures with no browser.

### `shopmanager/` — the three-stage bulk lister

Kept as three scripts on purpose. Stage 1 is slow and occasionally needs
rerunning; stage 2 is instant and gets rerun constantly while tuning the
threshold; stage 3 is the only one that writes to a live account. Fusing them
would mean re-scraping the library every time a number changes.

Stage 3 drives the form from a `tab → field → selector → value` description in
config rather than hardcoded selectors — the DOM here changes, and the same
shape recurs on every other marketplace admin. It **saves as draft by default**;
publishing needs an explicit `--publish`.

## Usage

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml     # then edit it
python -m pytest                       # 285 tests
```

Reconcile two catalogues:

```bash
python -m converters.reconcile --a etsy.csv --b shopify.xlsx --by sku
python -m converters.reconcile --a etsy.csv --b shopify.xlsx --by title --threshold 0.9
```

## Notes and limitations

**Test coverage is honest about where it stops.** 285 tests cover every pure
function: both CSV schemas and their row layouts, the variation rules including
the stale-leak case, photo column normalisation, text cleaning, the denylist,
translation, checkpointing, the failure halt, title normalisation, similarity,
reconciliation in both modes, eBay template parsing, strict image matching,
form-plan resolution, adapter parsing against fixture markup, and video
timestamp mapping.

**The browser automation is untested and marked as such.** `common/browser.py`,
`scrapers/creator_store.py`'s acquisition loop, `shopmanager/library_scraper.py`
and `shopmanager/bulk_creator.py`'s execution path all need a live Chrome, so
they are excluded from the suite by design — which is
exactly why it is as thin as it is. Its whole job is to hand back a driver so
the logic wrapped around it, where the bugs live, can be tested without one.
Selenium is deliberately not in `requirements.txt`; install it only if you run
those parts.

**Provenance, stated precisely.** These tools ran in production — the scrapers
against creator catalogues of 1,000+ products, the listing builder against a
208-listing Etsy catalogue with two variation axes. That original source was
lost, and this repository is a rebuild from the specifications those runs
produced. So the *approach* is proven and every rule here was paid for by a real
failure; the *rebuilt code* is verified against fixtures and the pure logic is
covered by 285 tests, but it has not itself been re-run against a live account.

Practically: trust the rules, treat a first run as a first run. Use the dry-run
flags, start with two listings, confirm the import before building 200. That is
the discipline the original runs taught, and it applies to any rebuild.

**The photo slot layout has one deliberate deviation from the original spec.**
The written layout described "slots 7–9: original photos 3–6" — three slots for
four photos. Three wins, because ten is Etsy's hard ceiling and the alternative
silently pushes the cover shot off the end. The sixth original is counted as
dropped and reported.

**Title similarity is never trusted alone.** It is Jaccard over normalised
tokens, and on real data it produced confident false positives across different
creators. Every caller pairs it with a creator constraint, a REVIEW band, or
both.

## Licence

MIT — see [LICENSE](LICENSE).
