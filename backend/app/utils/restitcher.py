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
    if isinstance(value, (list, tuple)):
        for item in value:
            try:
                return coerce_float(item)
            except ValueError:
                continue
        raise ValueError(f"Cannot interpret numeric value from {value!r}")
    text = str(value)
    for token in text.replace(",", " ").split():
        try:
            return float(token)
        except ValueError:
            continue
    raise ValueError(f"Cannot interpret numeric value from {value!r}")


def extract_bounds(manifest: Dict[str, Any]) -> Dict[str, float]:
    label_meta = manifest.get("label_metadata")
    if not label_meta:
        raise ValueError("Manifest does not include label metadata; cannot crop by lat/lon.")
    projection = label_meta.get("projection")
    if not projection:
        raise ValueError("Manifest does not include projection metadata; cannot crop by lat/lon.")
    try:
        min_lat = coerce_float(projection.get("MINIMUM_LATITUDE"))
        max_lat = coerce_float(projection.get("MAXIMUM_LATITUDE"))
        west_lon = coerce_float(projection.get("WESTERNMOST_LONGITUDE"))
        east_lon = coerce_float(projection.get("EASTERNMOST_LONGITUDE"))
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive guard
        raise ValueError("Projection metadata missing convertible latitude/longitude values.") from exc

    if None in (min_lat, max_lat, west_lon, east_lon):
        raise ValueError("Projection metadata incomplete; cannot derive bounds.")

    return {
        "min_lat": min(min_lat, max_lat),
        "max_lat": max(min_lat, max_lat),
        "west_lon": west_lon,
        "east_lon": east_lon,
    }


def crop_by_latlon(
    manifest: Dict[str, Any],
    manifest_path: Path,
    latlon_bounds: Tuple[float, float, float, float],
    output_path: Path,
) -> None:
    bounds = extract_bounds(manifest)
    lat_span = bounds["max_lat"] - bounds["min_lat"]
    lon_span = bounds["east_lon"] - bounds["west_lon"]
    if lat_span <= 0 or lon_span <= 0:
        raise ValueError("Scene metadata does not contain a valid geographic extent.")

    height = manifest["image_size"]["height"]
    width = manifest["image_size"]["width"]

    req_min_lat, req_min_lon, req_max_lat, req_max_lon = latlon_bounds
    min_lat = max(bounds["min_lat"], min(bounds["max_lat"], min(req_min_lat, req_max_lat)))
    max_lat = max(bounds["min_lat"], min(bounds["max_lat"], max(req_min_lat, req_max_lat)))
    min_lon = max(bounds["west_lon"], min(bounds["east_lon"], min(req_min_lon, req_max_lon)))
    max_lon = max(bounds["west_lon"], min(bounds["east_lon"], max(req_min_lon, req_max_lon)))

    if max_lat - min_lat <= 0 or max_lon - min_lon <= 0:
        raise ValueError("Requested crop is outside the scene bounds.")

    def lat_to_y(lat: float) -> float:
        return (bounds["max_lat"] - lat) / lat_span * height

    def lon_to_x(lon: float) -> float:
        return (lon - bounds["west_lon"]) / lon_span * width

    top = lat_to_y(max_lat)
    bottom = lat_to_y(min_lat)
    left = lon_to_x(min_lon)
    right = lon_to_x(max_lon)

    left = max(0.0, min(width, left))
    right = max(0.0, min(width, right))
    top = max(0.0, min(height, top))
    bottom = max(0.0, min(height, bottom))

    left_i = math.floor(left)
    top_i = math.floor(top)
    right_i = math.ceil(right)
    bottom_i = math.ceil(bottom)

    if left_i >= right_i or top_i >= bottom_i:
        raise ValueError("Requested bounds yield an empty region.")

    compose_region(manifest, manifest_path, (left_i, top_i, right_i, bottom_i), output_path)


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
