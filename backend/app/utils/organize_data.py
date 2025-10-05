#!/usr/bin/env python3
"""Utility to organize HiRISE downloads by orbit."""

import argparse
import re
from pathlib import Path
from typing import Iterable, Optional

NAME_PATTERN = re.compile(
    r"^(?P<phase>[A-Z]+)_(?P<orbit>\d{6})_(?P<target>\d{4})_(?P<band>[A-Z0-9]+)\.(?P<ext>JP2|LBL)$"
)


def iter_candidate_files(root: Path) -> Iterable[Path]:
    """Yield all files beneath *root* (skipping directories)."""
    for path in root.rglob("*"):
        if path.is_file():
            yield path


def parse_filename(path: Path) -> Optional[re.Match]:
    """Return regex match object if file follows expected HiRISE naming convention."""
    return NAME_PATTERN.match(path.name)


def organize_data(data_root: Path) -> None:
    """Move HiRISE files under `data_root` into orbit-based subdirectories."""
    if not data_root.exists():
        raise FileNotFoundError(f"Data root not found: {data_root}")

    moved = 0
    skipped = 0

    for file_path in iter_candidate_files(data_root):
        if file_path.name.startswith("."):
            skipped += 1
            continue
        match = parse_filename(file_path)
        if not match:
            skipped += 1
            print(f"⚠️  Skipping (unrecognised name): {file_path.relative_to(data_root)}")
            continue

        orbit = match.group("orbit")
        target = match.group("target")
        orbit_dir = data_root / target / orbit
        orbit_dir.mkdir(exist_ok=True)

        destination = orbit_dir / file_path.name
        if destination.exists():
            skipped += 1
            print(f"⚠️  Already organised: {destination.relative_to(data_root)}")
            continue

        try:
            current_rel = file_path.relative_to(data_root)
        except ValueError:
            current_rel = file_path

        file_path.rename(destination)
        moved += 1
        print(
            f"✅  Moved {current_rel} → {destination.relative_to(data_root)}"
        )

    print(f"\nDone. {moved} file(s) moved, {skipped} file(s) skipped.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Organise HiRISE downloads by orbit.")
    parser.add_argument(
        "data_root",
        nargs="?",
        default="data",
        help="Path to the directory containing target-code folders (default: ./data)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    organize_data(Path(args.data_root).resolve())


if __name__ == "__main__":
    main()
