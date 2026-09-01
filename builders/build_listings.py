"""Brief 06's entry point: a products YAML in, a Vela import CSV out.

    python -m builders.build_listings --products products.yaml --config config.yaml
    python -m builders.build_listings --products products.yaml --only PROD-001,PROD-002

**Build a two-listing test file before a full rebuild.** The strong preference
on this project is incremental: build two, import them, confirm variations and
photos render, then run the full set. Every full rebuild that fails costs a
re-import cycle, and `--only` is what makes the small version one flag rather
than a hand-edited input file.
"""

from __future__ import annotations

import argparse
import os
import sys

from common.progress import pause_if_interactive
from common.vela import write_post_import_todo, write_vela_csv
from .vela_builder import ProductSpec, build_listing, format_summary
from .video_map import map_videos, summary as video_summary, write_review_file


def load_yaml(path: str) -> dict | list:
    import yaml

    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def to_specs(entries: list[dict]) -> list[ProductSpec]:
    """Turn YAML entries into specs, ignoring keys the model does not carry."""
    known = set(ProductSpec.__dataclass_fields__)
    specs = []
    for entry in entries:
        unknown = set(entry) - known
        if unknown:
            print("  note: ignoring unrecognised key(s) %s on %r"
                  % (", ".join(sorted(unknown)), entry.get("sku", "?")))
        specs.append(ProductSpec(**{k: v for k, v in entry.items() if k in known}))
    return specs


def file_timestamps(paths: list[str]) -> list[float]:
    """EXIF capture time where available, file mtime otherwise.

    EXIF first because a file copied through a cloud folder loses its mtime but
    keeps the time the shutter actually fired -- which is the signal the video
    mapping depends on.
    """
    times = []
    for path in paths:
        if not os.path.exists(path):
            continue
        stamp = _exif_time(path)
        times.append(stamp if stamp is not None else os.path.getmtime(path))
    return times


def _exif_time(path: str) -> float | None:
    try:
        from datetime import datetime

        from PIL import Image

        with Image.open(path) as image:
            exif = image.getexif()
            raw = exif.get(36867) or exif.get(306)     # DateTimeOriginal, DateTime
        if not raw:
            return None
        return datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S").timestamp()
    except Exception:                                   # noqa: BLE001
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build_listings",
        description="Build a Vela import CSV from a products YAML.")
    parser.add_argument("--products", required=True, help="products YAML")
    parser.add_argument("--config", help="config.yaml (multiplier, photo layout)")
    parser.add_argument("--out", default="vela_import.csv")
    parser.add_argument("--only", help="comma-separated SKUs. Build two and "
                                       "import them before running the full set")
    parser.add_argument("--videos-dir", help="folder of videos to map by timestamp")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    config = load_yaml(args.config) if args.config else {}
    multiplier = (config.get("pricing") or {}).get("multiplier", 1.0)
    layout = (config.get("listings") or {}).get("photo_layout")
    if layout:
        layout = [(name, int(slots)) for name, slots in layout]

    entries = load_yaml(args.products)
    if isinstance(entries, dict):
        entries = entries.get("products", [])
    specs = to_specs(entries)

    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        specs = [spec for spec in specs if spec.sku in wanted]
        missing = wanted - {spec.sku for spec in specs}
        if missing:
            print("--only: no such SKU(s): %s" % ", ".join(sorted(missing)),
                  file=sys.stderr)
            return 2

    if not specs:
        print("Nothing to build.", file=sys.stderr)
        return 2

    print("Building %d listing(s) at multiplier %.2f" % (len(specs), multiplier))
    listings, summaries = [], []
    for spec in specs:
        listing, summary = build_listing(spec, multiplier, layout)
        listings.append(listing)
        summaries.append(summary)

    if args.videos_dir and os.path.isdir(args.videos_dir):
        videos = {
            name: os.path.getmtime(os.path.join(args.videos_dir, name))
            for name in os.listdir(args.videos_dir)
            if name.lower().endswith((".mp4", ".mov", ".m4v"))
        }
        photos = {spec.sku: file_timestamps(
            [p for group in spec.photos.values()
             for p in (group if isinstance(group, list) else [group])])
            for spec in specs}
        matches = map_videos(videos, photos)
        print(video_summary(matches))
        needing = write_review_file(matches, "video_review.txt")
        if needing:
            print("  %d video(s) need a human -> video_review.txt" % needing)

    rows = write_vela_csv(listings, args.out)
    grouped = write_post_import_todo(listings, "post_import_todo.md")

    print(format_summary(summaries))
    print("\n%d rows -> %s" % (len(rows), args.out))
    if grouped:
        print("%d Vela Profile(s) to apply by hand -> post_import_todo.md"
              % len(grouped))
    return 0


if __name__ == "__main__":
    code = main()
    pause_if_interactive()
    sys.exit(code)
