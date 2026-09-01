"""Stage 1: read the Creator x 3D-object pairs the account owns.

The listing form binds a listing to a licensed asset through two dependent
dropdowns -- pick a Creator, then pick one of that creator's objects. There is
no public API for this surface, so the owned library is read by enumerating the
dropdown options in the creation form itself.

Both the display text and the **internal option value** are captured. Stage 3
selects on the value: display text is localised and occasionally re-worded, and
matching on it alone is fragile.

Expect 5-10 minutes on a large library. Checkpointed per creator, because the
object list for a single creator can take up to 30 seconds to populate and
losing an hour to a disconnect on the last one is avoidable.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time

from common.browser import attach_to_chrome, survives_disconnect
from common.progress import Checkpoint, pause_if_interactive, timestamp

COLUMNS = ["creator", "creator_value", "object_text", "object_value"]

#: The UI warns the object list can take this long to populate.
OBJECT_LIST_TIMEOUT = 45
#: Poll interval while waiting for the option count to stabilise.
POLL = 0.5
#: The count must hold steady this long before it counts as settled. A fixed
#: sleep is what produces half-read lists: it either wastes time or truncates.
SETTLE_FOR = 2.0


def wait_for_options(get_count, timeout: float = OBJECT_LIST_TIMEOUT) -> int:
    """Wait until the option count stops changing, not for a fixed duration.

    Pure apart from `get_count`, so the settling logic is exercised in tests
    with a plain callable.
    """
    deadline = time.monotonic() + timeout
    last, stable_since = -1, None

    while time.monotonic() < deadline:
        count = get_count()
        if count == last and count > 0:
            if stable_since is None:
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= SETTLE_FOR:
                return count
        else:
            last, stable_since = count, None
        time.sleep(POLL)
    return max(last, 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="library_scraper",
        description="Enumerate owned Creator x 3D-object pairs from the form.")
    parser.add_argument("--url", required=True, help="product creation form URL")
    parser.add_argument("--creator-selector", default="#creator-select")
    parser.add_argument("--object-selector", default="#object-select")
    parser.add_argument("--out-dir", default=".")
    parser.add_argument("--limit", type=int, help="stop after N creators")
    return parser


@survives_disconnect
def run(args) -> int:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import Select

    stamp = timestamp()
    out_path = os.path.join(args.out_dir, "mmf_library_%s.csv" % stamp)
    checkpoint = Checkpoint.load(
        os.path.join(args.out_dir, "library_progress_%s.json" % stamp))

    driver = attach_to_chrome(os.path.dirname(os.path.abspath(__file__)))
    driver.get(args.url)
    time.sleep(3)

    creator_element = driver.find_element(By.CSS_SELECTOR, args.creator_selector)
    creators = [(option.text.strip(), option.get_attribute("value"))
                for option in Select(creator_element).options
                if option.get_attribute("value")]
    if args.limit:
        creators = creators[:args.limit]

    print(checkpoint.startup_message(len(creators)))
    rows = []

    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()

        for index, (creator, creator_value) in enumerate(creators, start=1):
            if checkpoint.is_done(creator_value):
                continue

            Select(driver.find_element(
                By.CSS_SELECTOR, args.creator_selector)).select_by_value(creator_value)

            def count_options():
                try:
                    element = driver.find_element(By.CSS_SELECTOR, args.object_selector)
                    return len([o for o in Select(element).options
                                if o.get_attribute("value")])
                except Exception:                          # noqa: BLE001
                    return 0

            found = wait_for_options(count_options)
            element = driver.find_element(By.CSS_SELECTOR, args.object_selector)
            for option in Select(element).options:
                value = option.get_attribute("value")
                if not value:
                    continue
                row = {"creator": creator, "creator_value": creator_value,
                       "object_text": option.text.strip(), "object_value": value}
                writer.writerow(row)
                rows.append(row)

            handle.flush()
            checkpoint.mark_done(creator_value)
            print("  [%d/%d] %-32s %d objects"
                  % (index, len(creators), creator[:32], found), flush=True)

    checkpoint.save()
    print("\n%d creators, %d objects -> %s"
          % (len(creators), len(rows), out_path))
    return 0


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    code = main()
    pause_if_interactive()
    sys.exit(code)
