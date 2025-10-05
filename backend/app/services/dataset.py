from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils import restitcher
from ..utils.converter import iter_jp2_files, parse_scene_name

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = PROJECT_ROOT / "data"
CONVERTER_SCRIPT = PROJECT_ROOT / "backend" / "app" / "utils" / "converter.py"


def resolve_data_path(path: Optional[str]) -> Path:
    if not path:
        return DATA_ROOT
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def find_jp2_files(data_path: Path = DATA_ROOT, recursive: bool = True) -> List[Path]:
    return list(iter_jp2_files(data_path, recursive))


def manifests_ready(manifests: List[Dict[str, Any]]) -> bool:
    if not manifests:
        return False
    for manifest in manifests:
        if not manifest.get("tiles"):
            return False
        if manifest.get("bounds") is None:
            return False
    return True


def run_converter(
    data_path: Path = DATA_ROOT,
    recursive: bool = True,
    force: bool = False,
    output_format: str = "jpg",
    quality: int = 85,
    tile_size: int = 2048,
    tiles_dir: str = "tiles",
    converter: Optional[str] = None,
) -> Dict[str, Any]:
    """Invoke the converter CLI to process JP2 files into tiles."""

    if not data_path.exists():
        raise FileNotFoundError(f"Data path does not exist: {data_path}")

    jp2_files = find_jp2_files(data_path, recursive)
    if not jp2_files:
        raise FileNotFoundError(
            f"No JP2 files found under {data_path}. Download HiRISE scenes first."
        )

    cmd = [
        sys.executable,
        str(CONVERTER_SCRIPT),
        str(data_path),
    ]

    if recursive:
        cmd.append("--recursive")
    if force:
        cmd.append("--force")
    if converter:
        cmd.extend(["--converter", converter])

    cmd.extend(["--format", output_format])
    cmd.extend(["--tile-size", str(tile_size)])
    cmd.extend(["--tiles-dir", tiles_dir])
    if quality is not None:
        cmd.extend(["--quality", str(quality)])

    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    payload: Dict[str, Any] = {
        "command": cmd,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "jp2_count": len(jp2_files),
    }

    if result.returncode != 0:
        raise RuntimeError(
            "Converter command failed",
            payload,
        )

    payload["manifests"] = list_manifests(data_path)
    return payload


def list_manifests(data_path: Path = DATA_ROOT) -> List[Dict[str, Any]]:
    manifests: List[Dict[str, Any]] = []
    for manifest_path in sorted(data_path.rglob("metadata.json")):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        data["manifest_path"] = str(manifest_path)
        data["tiles_root_path"] = str(manifest_path.parent)
        data["tiles_count"] = len(data.get("tiles", []))
        data["tile_rows"] = max((t.get("row", 0) for t in data.get("tiles", [])), default=-1) + 1
        data["tile_cols"] = max((t.get("col", 0) for t in data.get("tiles", [])), default=-1) + 1
        data["bounds"] = compute_bounds_from_manifest(data)
        scene_info = parse_scene_name(Path(data.get("source", manifest_path.stem)))
        for key, value in scene_info.items():
            data.setdefault(key, value)
        data["preview_available"] = bool(resolve_preview_path(data, data_path))
        manifests.append(data)
    return manifests


def load_manifest_for_scene(scene_id: str, data_path: Path = DATA_ROOT) -> Optional[Dict[str, Any]]:
    for manifest_path in sorted(data_path.rglob("metadata.json")):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        info = parse_scene_name(Path(data.get("source", manifest_path.stem)))
        if info.get("scene_id") == scene_id:
            data.setdefault("manifest_path", str(manifest_path))
            data.setdefault("tiles_root_path", str(manifest_path.parent))
            data.setdefault("tile_rows", max((t.get("row", 0) for t in data.get("tiles", [])), default=-1) + 1)
            data.setdefault("tile_cols", max((t.get("col", 0) for t in data.get("tiles", [])), default=-1) + 1)
            data.setdefault("tiles_count", len(data.get("tiles", [])))
            data["bounds"] = data.get("bounds") or compute_bounds_from_manifest(data)
            data.update(info)
            data["preview_available"] = bool(resolve_preview_path(data, data_path))
            return data
    return None


def resolve_preview_path(manifest: Dict[str, Any], data_path: Path = DATA_ROOT) -> Optional[Path]:
    output_path = manifest.get("output_path")
    if not output_path:
        return None
    candidate = Path(output_path)
    if not candidate.is_absolute():
        candidate = data_path / candidate
    return candidate if candidate.exists() else None


def compute_bounds_from_manifest(manifest: Dict[str, Any]) -> Optional[Dict[str, float]]:
    projection = manifest.get("label_metadata", {}).get("projection") if manifest.get("label_metadata") else None
    if not projection:
        return None
    try:
        min_lat = _coerce_float(projection.get("MINIMUM_LATITUDE"))
        max_lat = _coerce_float(projection.get("MAXIMUM_LATITUDE"))
        west_lon = _coerce_float(projection.get("WESTERNMOST_LONGITUDE"))
        east_lon = _coerce_float(projection.get("EASTERNMOST_LONGITUDE"))
    except (TypeError, ValueError):
        return None
    if None in (min_lat, max_lat, west_lon, east_lon):
        return None
    return {
        "min_lat": min_lat,
        "max_lat": max_lat,
        "west_lon": west_lon,
        "east_lon": east_lon,
    }


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict) and "value" in value:
        try:
            return float(value["value"])
        except (TypeError, ValueError):
            return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


@dataclass
class CropRequest:
    manifest_path: Path
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float
    output_path: Path


def crop_by_latlon(request: CropRequest) -> Path:
    manifest = json.loads(request.manifest_path.read_text(encoding="utf-8"))
    restitcher.crop_by_latlon(
        manifest,
        request.manifest_path,
        (request.min_lat, request.min_lon, request.max_lat, request.max_lon),
        request.output_path,
    )
    return request.output_path


def stitch_full_scene(manifest_path: Path, output_path: Path) -> Path:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    restitcher.stitch_full(manifest, manifest_path, output_path)
    return output_path


__all__ = [
    "resolve_data_path",
    "find_jp2_files",
    "manifests_ready",
    "run_converter",
    "list_manifests",
    "load_manifest_for_scene",
    "resolve_preview_path",
    "crop_by_latlon",
    "stitch_full_scene",
    "compute_bounds_from_manifest",
]
