#!/usr/bin/env python3
"""Reconstruct images or crops from tile sets produced by converter.py.

The converter emits a metadata manifest (`metadata.json`) alongside each tile
set when `--tile-size` is supplied. This utility loads that manifest, stitches
the tile imagery back together, and optionally crops the mosaic either by
pixel coordinates or by latitude/longitude using the provenance stored from
the original `.LBL` file.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from PIL import Image


def load_manifest(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    required_keys = {"image_size", "tile_size", "tiles"}
    missing = required_keys - data.keys()
    if missing:
        raise ValueError(f"Manifest missing required fields: {', '.join(sorted(missing))}")
    return data


def ensure_directory(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def create_canvas(manifest: Dict[str, Any], width: int, height: int) -> Image.Image:
    mode = manifest.get("image_mode", "RGB")
    return Image.new(mode, (width, height))


def tiles_from_manifest(manifest: Dict[str, Any], manifest_path: Path) -> Iterable[Tuple[Dict[str, Any], Path]]:
    base_dir = manifest_path.parent
    project_root: Optional[Path] = None
    if "project_root" in manifest:
        try:
            project_root = Path(manifest["project_root"]).expanduser().resolve()
        except Exception:
            project_root = None
    for tile in manifest["tiles"]:
        candidate = base_dir / tile["path"]
        if candidate.exists():
            yield tile, candidate
            continue
        if project_root and "relative_to_root" in tile:
            candidate = project_root / tile["relative_to_root"]
        elif "absolute" in tile:
            candidate = Path(tile["absolute"])
        if not candidate.exists():
            raise FileNotFoundError(f"Tile referenced by manifest does not exist: {tile}")
        yield tile, candidate


def compose_region(
    manifest: Dict[str, Any],
    manifest_path: Path,
    bounds_px: Tuple[int, int, int, int],
    output_path: Path,
) -> None:
    left, top, right, bottom = bounds_px
    width = max(0, right - left)
    height = max(0, bottom - top)
    if width == 0 or height == 0:
        raise ValueError("Requested bounds yield an empty region.")

    canvas = create_canvas(manifest, width, height)

    for tile_meta, tile_path in tiles_from_manifest(manifest, manifest_path):
        tile_left = tile_meta["x"]
        tile_top = tile_meta["y"]
        tile_right = tile_left + tile_meta["width"]
        tile_bottom = tile_top + tile_meta["height"]

        inter_left = max(left, tile_left)
        inter_top = max(top, tile_top)
        inter_right = min(right, tile_right)
        inter_bottom = min(bottom, tile_bottom)

        if inter_left >= inter_right or inter_top >= inter_bottom:
            continue

        crop_box = (
            inter_left - tile_left,
            inter_top - tile_top,
            inter_right - tile_left,
            inter_bottom - tile_top,
        )
        paste_box = (inter_left - left, inter_top - top)

        with Image.open(tile_path) as tile_img:
            fragment = tile_img.crop(crop_box)
            canvas.paste(fragment, paste_box)

    ensure_directory(output_path)
    canvas.save(output_path)


def stitch_full(manifest: Dict[str, Any], manifest_path: Path, output_path: Path) -> None:
    width = manifest["image_size"]["width"]
    height = manifest["image_size"]["height"]
    compose_region(manifest, manifest_path, (0, 0, width, height), output_path)


def coerce_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict) and "value" in value:
        return float(value["value"])
    text = str(value)
    for token in text.replace(",", " ").split():
        try:
            return float(token)
        except ValueError:
            continue
    raise ValueError(f"Cannot interpret numeric value from {value!r}")


@dataclass
class SimpleProjection:
    sample_offset: float
    line_offset: float
    center_lon: float
    center_lat: float
    map_resolution: float

    @classmethod
    def from_metadata(cls, label_meta: Dict[str, Any]) -> "SimpleProjection":
        proj = label_meta.get("projection") if label_meta else None
        if not proj:
            raise ValueError("Manifest does not contain projection metadata.")

        required = [
            "SAMPLE_PROJECTION_OFFSET",
            "LINE_PROJECTION_OFFSET",
            "CENTER_LONGITUDE",
            "CENTER_LATITUDE",
            "MAP_RESOLUTION",
        ]
        missing = [key for key in required if key not in proj]
        if missing:
            raise ValueError(
                "Projection metadata missing keys: " + ", ".join(missing)
            )

        return cls(
            sample_offset=coerce_float(proj["SAMPLE_PROJECTION_OFFSET"]),
            line_offset=coerce_float(proj["LINE_PROJECTION_OFFSET"]),
            center_lon=coerce_float(proj["CENTER_LONGITUDE"]),
            center_lat=coerce_float(proj["CENTER_LATITUDE"]),
            map_resolution=coerce_float(proj["MAP_RESOLUTION"]),
        )

    @staticmethod
    def _wrap_delta(delta: float) -> float:
        while delta > 180.0:
            delta -= 360.0
        while delta < -180.0:
            delta += 360.0
        return delta

    def lonlat_to_pixel(self, lon: float, lat: float) -> Tuple[float, float]:
        lon_delta = self._wrap_delta(lon - self.center_lon)
        sample = self.sample_offset + lon_delta * self.map_resolution
        line = self.line_offset - (lat - self.center_lat) * self.map_resolution
        return sample, line


def crop_by_latlon(
    manifest: Dict[str, Any],
    manifest_path: Path,
    latlon_bounds: Tuple[float, float, float, float],
    output_path: Path,
) -> None:
    label_meta = manifest.get("label_metadata")
    if not label_meta:
        raise ValueError("Manifest does not include label metadata; cannot crop by lat/lon.")

    projection = SimpleProjection.from_metadata(label_meta)

    min_lat, min_lon, max_lat, max_lon = latlon_bounds
    # Convert the four corners and derive pixel-aligned bounds.
    px_coords = [
        projection.lonlat_to_pixel(lon, lat)
        for lat in (min_lat, max_lat)
        for lon in (min_lon, max_lon)
    ]

    xs = [coord[0] for coord in px_coords]
    ys = [coord[1] for coord in px_coords]

    left = math.floor(min(xs))
    top = math.floor(min(ys))
    right = math.ceil(max(xs))
    bottom = math.ceil(max(ys))

    width = manifest["image_size"]["width"]
    height = manifest["image_size"]["height"]

    left = max(0, left)
    top = max(0, top)
    right = min(width, right)
    bottom = min(height, bottom)

    compose_region(manifest, manifest_path, (left, top, right, bottom), output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="Path to metadata.json emitted by converter.py")
    parser.add_argument("output", help="Output image path (PNG/JPEG/TIFF)")
    parser.add_argument(
        "--crop-pixels",
        nargs=4,
        type=int,
        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
        help="Crop region using pixel bounds (inclusive-exclusive).",
    )
    parser.add_argument(
        "--crop-latlon",
        nargs=4,
        type=float,
        metavar=("MIN_LAT", "MIN_LON", "MAX_LAT", "MAX_LON"),
        help="Crop region using latitude/longitude bounds (degrees).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = load_manifest(manifest_path)
    output_path = Path(args.output).expanduser()

    if args.crop_pixels and args.crop_latlon:
        raise ValueError("Specify either --crop-pixels or --crop-latlon, not both.")

    if args.crop_pixels:
        left, top, right, bottom = args.crop_pixels
        compose_region(manifest, manifest_path, (left, top, right, bottom), output_path)
        return

    if args.crop_latlon:
        min_lat, min_lon, max_lat, max_lon = args.crop_latlon
        crop_by_latlon(manifest, manifest_path, (min_lat, min_lon, max_lat, max_lon), output_path)
        return

    stitch_full(manifest, manifest_path, output_path)


if __name__ == "__main__":
    main()
