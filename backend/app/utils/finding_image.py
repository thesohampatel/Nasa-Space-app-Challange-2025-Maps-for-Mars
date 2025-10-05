#!/usr/bin/env python3
"""Utility helpers to query the HiRISE RDR index for related scenes.

The original script was interactive. This refactor exposes reusable functions so
that the backend API can surface search results (and still supports the CLI
workflow for manual use).
"""

from __future__ import annotations

import argparse
import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import urljoin

import requests
from tqdm import tqdm

INDEX_URL = "https://hirise-pds.lpl.arizona.edu/PDS/INDEX/RDRINDEX.TAB"
PDS_BASE = "https://hirise-pds.lpl.arizona.edu/PDS/"
DOWNLOAD_EXTENSIONS = [".JP2", ".LBL"]
TIMEOUT = 60

SCENE_REGEX = re.compile(r"_(?P<orbit>\d{6})_(?P<target>\d{4})_")


@dataclass
class IndexEntry:
    filename: str
    url: str
    target_code: str
    orbit_folder: Optional[str]
    verified: bool


def extract_target_code(scene_or_code: str) -> Optional[str]:
    scene_or_code = scene_or_code.strip()
    if re.fullmatch(r"\d{4}", scene_or_code):
        return scene_or_code
    match = SCENE_REGEX.search(scene_or_code)
    if match:
        return match.group("target")
    return None


def compute_orbit_folder(scene_id: str) -> Optional[str]:
    match = re.search(r"_(\d{6})_", scene_id)
    if not match:
        return None
    orbit = int(match.group(1))
    low = orbit - (orbit % 100)
    high = low + 99
    return f"ORB_{low:06d}_{high:06d}"


def iter_index_rows() -> Iterable[List[str]]:
    with requests.get(INDEX_URL, stream=True, timeout=TIMEOUT) as response:
        response.raise_for_status()
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            try:
                yield next(csv.reader([raw_line]))
            except csv.Error:
                continue


def search_index(scene_or_code: str, limit: Optional[int] = None) -> List[IndexEntry]:
    target_code = extract_target_code(scene_or_code)
    if not target_code:
        raise ValueError("Unable to determine target code from input.")

    entries: List[IndexEntry] = []
    for row in iter_index_rows():
        if len(row) < 2:
            continue
        filename = row[1].strip()
        if not filename or target_code not in filename:
            continue
        basename = os.path.basename(filename)
        if "JP2" not in basename.upper():
            continue
        match = SCENE_REGEX.search(basename)
        if not match or match.group("target") != target_code:
            continue

        url = urljoin(PDS_BASE, filename.lstrip("/"))
        orbit_folder = compute_orbit_folder(basename)
        verified = False
        try:
            head_response = requests.head(url, allow_redirects=True, timeout=TIMEOUT)
            verified = head_response.ok
        except requests.RequestException:
            verified = False

        entries.append(IndexEntry(filename=basename, url=url, target_code=target_code, orbit_folder=orbit_folder, verified=verified))

        if limit and len(entries) >= limit:
            break
    return entries


def download_entries(entries: List[IndexEntry], output_dir: Path) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: List[Path] = []

    for entry in tqdm(entries, desc="Downloading scenes", unit="scene"):
        base_url = entry.url.rsplit(".", 1)[0]
        for ext in DOWNLOAD_EXTENSIONS:
            file_url = base_url + ext
            destination = output_dir / Path(file_url).name
            if destination.exists():
                continue
            try:
                response = requests.get(file_url, stream=True, timeout=TIMEOUT)
                response.raise_for_status()
                total = int(response.headers.get("content-length", 0))
                with open(destination, "wb") as handle, tqdm(
                    total=total,
                    unit="B",
                    unit_scale=True,
                    desc=destination.name,
                    leave=False,
                ) as bar:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            handle.write(chunk)
                            bar.update(len(chunk))
            except Exception:
                continue
            saved_paths.append(destination)
    return saved_paths


def cli() -> None:
    parser = argparse.ArgumentParser(description="Search and optionally download HiRISE scenes sharing a target code.")
    parser.add_argument("scene", help="Scene name or target code (e.g. ESP_019308_2640_RED.JP2 or 2640)")
    parser.add_argument("--limit", type=int, default=20, help="Maximum number of entries to display")
    parser.add_argument("--download", action="store_true", help="Download the JP2/LBL files for the matches")
    parser.add_argument("--outdir", help="Optional output directory for downloads")
    args = parser.parse_args()

    entries = search_index(args.scene, limit=args.limit)
    if not entries:
        print("No entries found for the supplied target code.")
        return

    print(f"Found {len(entries)} entries for target code {entries[0].target_code}:")
    for entry in entries:
        print(f"- {entry.filename} | {entry.url} | verified={entry.verified}")

    if args.download:
        outdir = Path(args.outdir or f"hirise_downloads/{entries[0].target_code}")
        downloaded = download_entries(entries, outdir)
        print(f"Downloaded {len(downloaded)} files to {outdir}")


if __name__ == "__main__":  # pragma: no cover
    cli()
