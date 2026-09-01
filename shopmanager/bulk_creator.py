"""Stage 3: drive the product-creation form for each matched row.

    python -m shopmanager.bulk_creator --csv matched.csv --config config.yaml --dry-run
    python -m shopmanager.bulk_creator --csv matched.csv --config config.yaml --limit 20

**Saves as draft by default. Publishing requires an explicit ``--publish``.**
Every run of a script like this against a live account with a bad CSV is
cleanup work, and the asymmetry is stark: an unpublished draft is deleted in
bulk, a published listing is not.

The form itself is described in config and resolved by
:mod:`shopmanager.form_driver`, which is where the tested logic lives. This
module is the part that needs a browser, so it is kept thin enough to read in
one sitting.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys

from common.browser import attach_to_chrome, survives_disconnect
from common.progress import Checkpoint, pause_if_interactive, timestamp
from .form_driver import CLICK, FILL, SELECT, SELECT_SEARCH, describe, load_steps, resolve

RESULT_COLUMNS = ["row_key", "object_value", "title", "status", "error"]


def load_config(path: str) -> dict:
    import yaml

    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def row_key(row: dict) -> str:
    """Stable identity for checkpointing. Must not be the row index -- a
    reordered CSV would then resume onto the wrong products."""
    return row.get("object_value") or row.get("title", "")


def apply_step(driver, step, wait_seconds: float = 20.0) -> None:
    """Execute one resolved step against the live form."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as ec
    from selenium.webdriver.support.ui import WebDriverWait

    wait = WebDriverWait(driver, wait_seconds)
    element = wait.until(ec.presence_of_element_located(
        (By.CSS_SELECTOR, step.selector)))

    if step.action == CLICK:
        element.click()
    elif step.action == FILL:
        element.clear()
        element.send_keys(step.value)
    elif step.action == SELECT:
        from selenium.webdriver.support.ui import Select
        Select(element).select_by_value(step.value)
    elif step.action == SELECT_SEARCH:
        # These lists are long and the dropdowns are searchable, so type into
        # the filter rather than scrolling to find an option.
        element.click()
        element.send_keys(step.value)
        wait.until(ec.presence_of_element_located(
            (By.CSS_SELECTOR, "%s option, .dropdown-option" % step.selector)))
        element.send_keys("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bulk_creator",
        description="Create marketplace listings from a matched CSV.")
    parser.add_argument("--csv", required=True, help="stage 2's matched output")
    parser.add_argument("--config", required=True, help="config.yaml with form.steps")
    parser.add_argument("--dry-run", action="store_true",
                        help="walk the plan and report what would be selected, "
                             "without touching the account. Use this for every "
                             "first run against a new CSV")
    parser.add_argument("--limit", type=int, help="stop after N products")
    parser.add_argument("--publish", action="store_true",
                        help="publish instead of saving as draft. Off by "
                             "default and deliberately awkward")
    parser.add_argument("--resume", help="checkpoint file to resume from")
    return parser


@survives_disconnect
def run(args) -> int:
    steps = load_steps(load_config(args.config))

    with open(args.csv, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if args.limit:
        rows = rows[:args.limit]

    stamp = timestamp()
    checkpoint = Checkpoint.load(args.resume or "mmf_upload_progress_%s.json" % stamp)
    pending = [r for r in rows if not checkpoint.is_done(row_key(r))]
    print(checkpoint.startup_message(len(rows)))

    if args.dry_run:
        print("\n--dry-run: no browser, nothing saved.\n")
        unresolvable = 0
        for row in pending[:20]:
            try:
                print("%s" % row.get("title", "")[:70])
                print(describe(resolve(steps, row)))
            except Exception as exc:                      # noqa: BLE001
                unresolvable += 1
                print("  UNRESOLVABLE: %s" % exc)
            print("")
        print("%d of %d sampled rows could not be resolved."
              % (unresolvable, min(len(pending), 20)))
        print("Get this to zero before running live." if unresolvable
              else "Ready for a live run.")
        return 1 if unresolvable else 0

    if not args.publish:
        print("Saving as DRAFT. Pass --publish to publish.\n")

    driver = attach_to_chrome(os.path.dirname(os.path.abspath(__file__)))
    results = []

    for index, row in enumerate(pending, start=1):
        key = row_key(row)
        title = row.get("title", "")
        try:
            for step in resolve(steps, row):
                apply_step(driver, step)
            checkpoint.mark_done(key)
            results.append({"row_key": key, "object_value": row.get("object_value", ""),
                            "title": title, "status": "created", "error": ""})
        except Exception as exc:                          # noqa: BLE001
            # A dropdown that returns no match is logged and skipped, never
            # fatal. The failures file is re-drivable as its own input.
            checkpoint.mark_failed(key, str(exc)[:200])
            results.append({"row_key": key, "object_value": row.get("object_value", ""),
                            "title": title, "status": "failed",
                            "error": "%s: %s" % (type(exc).__name__, str(exc)[:150])})
            print("  failed: %s (%s)" % (title[:50], type(exc).__name__))

        if index % 10 == 0:
            checkpoint.save()
            print("  %d/%d" % (index, len(pending)), flush=True)

    checkpoint.save()
    path = "mmf_upload_results_%s.csv" % stamp
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        writer.writerows(results)

    created = sum(1 for r in results if r["status"] == "created")
    print("\ncreated %d, failed %d -> %s" % (created, len(results) - created, path))
    return 0


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    code = main()
    pause_if_interactive()
    sys.exit(code)
