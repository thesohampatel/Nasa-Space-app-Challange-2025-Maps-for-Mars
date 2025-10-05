#!/usr/bin/env python3
"""Convert HiRISE JP2 products to lightweight imagery using external tools.

The script avoids heavy Python dependencies (numpy/rasterio) by delegating the
conversion to a system utility such as FFmpeg, GDAL, or ImageMagick. The first
available tool is used automatically, or you can force a specific one via the
command-line. Conversion happens in-place and now supports PNG, JPEG, or TIFF
outputs, with an optional Pillow-powered tiling step to break large products
into smaller pieces that are easier to handle downstream. When tiling is
enabled, the script also emits a JSON manifest that records basic metadata from
the accompanying `.LBL` file (if available) so tiles can be geolocated later.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - optional dependency
    tqdm = None

try:
    import pvl  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    pvl = None

CommandBuilder = Callable[[str, Path, Path, str, Optional[int]], List[str]]


def ffmpeg_builder(
    executable: str, src: Path, dst: Path, fmt: str, quality: Optional[int]
) -> List[str]:
    cmd = [executable, "-y", "-i", str(src)]
    if fmt == "jpeg" and quality:
        # ffmpeg expects lower values for higher quality (scale 1-31)
        clamped = max(1, min(31, 32 - round(quality * 31 / 100)))
        cmd.extend(["-q:v", str(clamped)])
    cmd.append(str(dst))
    return cmd


def gdal_builder(
    executable: str, src: Path, dst: Path, fmt: str, quality: Optional[int]
) -> List[str]:
    format_token = {
        "png": "PNG",
        "jpeg": "JPEG",
        "tiff": "GTiff",
    }[fmt]
    cmd = [executable, "-of", format_token]
    if fmt == "jpeg":
        # Force 8-bit output so downstream tooling (e.g. Pillow) can read the file.
        cmd.extend(["-ot", "Byte", "-scale"])
        q = quality if quality is not None else 90
        cmd.extend(["-co", f"QUALITY={q}"])
    cmd.extend([str(src), str(dst)])
    return cmd


def magick_builder(
    executable: str, src: Path, dst: Path, fmt: str, quality: Optional[int]
) -> List[str]:
    cmd = [executable, str(src)]
    if fmt == "jpeg":
        cmd.extend(["-quality", str(quality if quality is not None else 90)])
    cmd.append(str(dst))
    return cmd


def convert_builder(
    executable: str, src: Path, dst: Path, fmt: str, quality: Optional[int]
) -> List[str]:
    cmd = [executable, str(src)]
    if fmt == "jpeg":
        cmd.extend(["-quality", str(quality if quality is not None else 90)])
    cmd.append(str(dst))
    return cmd


CONVERTERS: Tuple[Tuple[str, CommandBuilder], ...] = (
    ("ffmpeg", ffmpeg_builder),
    ("gdal_translate", gdal_builder),
    ("magick", magick_builder),
    ("convert", convert_builder),
)


FORMAT_ALIASES = {
    "jpg": "jpeg",
    "jpeg": "jpeg",
    "png": "png",
    "tif": "tiff",
    "tiff": "tiff",
}


FORMAT_SUFFIX = {
    "png": ".png",
    "jpeg": ".jpg",
    "tiff": ".tif",
}


PIL_FORMAT = {
    "png": "PNG",
    "jpeg": "JPEG",
    "tiff": "TIFF",
}


SCENE_PATTERN = re.compile(
    r"^(?P<product>[A-Z0-9]+)_(?P<orbit>\d{6})_(?P<target>\d{4})_(?P<band>[A-Z0-9]+)$"
)


class ConverterNotFoundError(RuntimeError):
    pass


def normalize_label_value(value: Any) -> Any:
    """Convert PVL quantities into JSON-friendly primitives."""

    if isinstance(value, (str, int, float, bool)):
        return value
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [normalize_label_value(v) for v in value]
    if pvl is not None:
        try:
            from pvl.units import Quantity  # type: ignore
        except Exception:  # pragma: no cover - defensive
            Quantity = None  # type: ignore
        if Quantity is not None and isinstance(value, Quantity):  # type: ignore
            return {"value": value.value, "units": str(value.units)}
    return str(value)


def load_label_metadata(src: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Extract a subset of metadata from a matching `.LBL` file."""

    lbl_path = src.with_suffix(".LBL")
    if not lbl_path.exists():
        return None, None
    if pvl is None:
        return None, (
            f"Install 'pvl' to parse label metadata from {lbl_path.name}."
        )

    try:
        label = pvl.load(str(lbl_path))
    except Exception as exc:  # pragma: no cover - defensive
        return None, f"Failed to parse {lbl_path.name}: {exc}"

    image_section = label.get("IMAGE", {})
    projection = label.get("IMAGE_MAP_PROJECTION", {})

    def subset(source: Dict[str, Any], keys: Iterable[str]) -> Dict[str, Any]:
        return {
            key: normalize_label_value(source.get(key))
            for key in keys
            if key in source
        }

    image_keys = [
        "LINES",
        "LINE_SAMPLES",
        "BAND_STORAGE_TYPE",
        "BANDS",
        "FIRST_LINE",
        "FIRST_LINE_SAMPLE",
    ]

    projection_keys = [
        "MAP_PROJECTION_TYPE",
        "COORDINATE_SYSTEM_NAME",
        "CENTER_LATITUDE",
        "CENTER_LONGITUDE",
        "LINE_PROJECTION_OFFSET",
        "SAMPLE_PROJECTION_OFFSET",
        "MAP_RESOLUTION",
        "MAP_SCALE",
        "MAXIMUM_LATITUDE",
        "MINIMUM_LATITUDE",
        "EASTERNMOST_LONGITUDE",
        "WESTERNMOST_LONGITUDE",
        "A_AXIS_RADIUS",
        "B_AXIS_RADIUS",
        "C_AXIS_RADIUS",
    ]

    metadata: Dict[str, Any] = {
        "label_path": lbl_path.name,
        "image": subset(image_section, image_keys),
        "projection": subset(projection, projection_keys),
    }

    return metadata, None


def write_manifest(manifest_dir: Path, manifest: Dict[str, Any]) -> Path:
    manifest_path = manifest_dir / "metadata.json"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def relative_or_absolute(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def parse_scene_name(path: Path) -> Dict[str, Any]:
    stem = path.stem
    match = SCENE_PATTERN.match(stem)
    if not match:
        return {"scene_id": stem}
    info = match.groupdict()
    info["scene_id"] = stem
    info["target_code"] = info.get("target")
    info["orbit_number"] = info.get("orbit")
    info["band"] = info.get("band")
    info["product_id"] = info.get("product")
    return info


def normalize_format(fmt: str) -> str:
    key = fmt.lower()
    if key not in FORMAT_ALIASES:
        valid = ", ".join(sorted(FORMAT_SUFFIX))
        raise ValueError(f"Unsupported format '{fmt}'. Valid choices: {valid}")
    normalized = FORMAT_ALIASES[key]
    if normalized not in FORMAT_SUFFIX:
        valid = ", ".join(sorted(FORMAT_SUFFIX))
        raise ValueError(f"Unhandled format '{fmt}'. Valid choices: {valid}")
    return normalized


def find_converters(preferred: Optional[str] = None) -> List[Tuple[str, CommandBuilder, str]]:
    """Return list of available converters in priority order.

    Each entry is `(executable_path, builder, name)`. If *preferred* is given,
    only that converter is returned (or an error is raised if missing).
    """

    def resolve(name: str) -> Optional[str]:
        return shutil.which(name)

    if preferred:
        for name, builder in CONVERTERS:
            if name == preferred:
                path = resolve(name)
                if not path:
                    raise ConverterNotFoundError(
                        f"Requested converter '{preferred}' not found on PATH."
                    )
                return [(path, builder, name)]
        raise ConverterNotFoundError(
            f"Unknown converter '{preferred}'. Valid options: {', '.join(n for n, _ in CONVERTERS)}"
        )

    available: List[Tuple[str, CommandBuilder, str]] = []
    for name, builder in CONVERTERS:
        path = resolve(name)
        if path:
            available.append((path, builder, name))

    if not available:
        raise ConverterNotFoundError(
            "No external converter found. Install ffmpeg, gdal (gdal_translate), or ImageMagick (magick/convert)."
        )
    return available


def iter_jp2_files(root: Path, recursive: bool) -> Iterable[Path]:
    pattern = "**/*.JP2" if recursive else "*.JP2"
    yield from root.glob(pattern)


def try_convert_with(
    src: Path,
    dst: Path,
    overwrite: bool,
    executable: str,
    builder: CommandBuilder,
    fmt: str,
    quality: Optional[int],
) -> Tuple[str, Optional[str]]:
    if dst.exists() and not overwrite:
        return "skip", f"Already exists: {dst.name}"

    cmd = builder(executable, src, dst, fmt, quality)
    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        return "error", exc.stderr.strip() or exc.stdout.strip() or str(exc)

    diagnostics = completed.stderr.strip() if completed.stderr else None
    return "ok", diagnostics


def split_into_tiles(
    image_path: Path,
    tile_size: int,
    output_format: str,
    quality: Optional[int],
    tiles_root: Optional[Path],
    root: Path,
) -> Tuple[int, List[Dict[str, Any]], Tuple[int, int], str, Path]:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Tile splitting requires Pillow. Install it with 'pip install Pillow'."
        ) from exc

    if tile_size <= 0:
        raise ValueError("Tile size must be a positive integer.")

    base_dir = tiles_root if tiles_root else image_path.parent / f"{image_path.stem}_tiles"
    if tiles_root:
        base_dir = tiles_root / image_path.stem
    base_dir.mkdir(parents=True, exist_ok=True)

    suffix = FORMAT_SUFFIX[output_format]
    pil_format = PIL_FORMAT[output_format]
    save_kwargs = {}
    if output_format == "jpeg" and quality is not None:
        save_kwargs["quality"] = quality

    count = 0
    tile_records: List[Dict[str, Any]] = []
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(image_path) as img:
        width, height = img.size
        for top in range(0, height, tile_size):
            for left in range(0, width, tile_size):
                right = min(left + tile_size, width)
                bottom = min(top + tile_size, height)
                tile = img.crop((left, top, right, bottom))
                row = top // tile_size
                col = left // tile_size
                tile_path = base_dir / f"{image_path.stem}_r{row:03d}_c{col:03d}{suffix}"
                tile.save(tile_path, format=pil_format, **save_kwargs)
                count += 1
                record: Dict[str, Any] = {
                    "row": row,
                    "col": col,
                    "x": left,
                    "y": top,
                    "width": right - left,
                    "height": bottom - top,
                    "path": tile_path.name,
                }
                try:
                    record["relative_to_root"] = str(tile_path.relative_to(root))
                except ValueError:
                    record["absolute"] = str(tile_path)
                tile_records.append(record)
        mode = img.mode
    return count, tile_records, (width, height), mode, base_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert JP2 files to PNG/JPEG/TIFF using external tools and optionally tile the result."
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Directory containing JP2 files (default: current directory).",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Search directories recursively for JP2 files.",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Overwrite existing output files.",
    )
    parser.add_argument(
        "-c",
        "--converter",
        choices=[name for name, _ in CONVERTERS],
        help="Force use of a specific external converter.",
    )
    parser.add_argument(
        "--format",
        default="png",
        choices=sorted(FORMAT_ALIASES),
        help="Output image format (default: png).",
    )
    parser.add_argument(
        "--quality",
        type=int,
        help="JPEG quality (1-100). Applied when --format=jpg/jpeg.",
    )
    parser.add_argument(
        "--tile-size",
        type=int,
        help="Split output into square tiles of this size (pixels). Requires Pillow.",
    )
    parser.add_argument(
        "--tiles-dir",
        help="Directory to store generated tiles (defaults to <image>_tiles next to the output).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.path).expanduser().resolve()
    if not root.exists():
        print(f"❌ Path not found: {root}", file=sys.stderr)
        return 1

    try:
        output_format = normalize_format(args.format)
    except ValueError as err:
        print(f"❌ {err}", file=sys.stderr)
        return 1

    quality: Optional[int] = None
    if output_format == "jpeg":
        if args.quality is not None and not (1 <= args.quality <= 100):
            print("❌ JPEG quality must be between 1 and 100.", file=sys.stderr)
            return 1
        quality = args.quality if args.quality is not None else 90
    elif args.quality is not None:
        print("⚠️  --quality is only used for JPEG output. Ignoring.")

    if args.tile_size is not None and args.tile_size <= 0:
        print("❌ --tile-size must be a positive integer.", file=sys.stderr)
        return 1

    tiles_root: Optional[Path] = None
    if args.tiles_dir:
        tiles_root = Path(args.tiles_dir)
        if not tiles_root.is_absolute():
            tiles_root = root / tiles_root
        tiles_root.mkdir(parents=True, exist_ok=True)

    output_suffix = FORMAT_SUFFIX[output_format]

    try:
        converters = find_converters(args.converter)
    except ConverterNotFoundError as err:
        print(f"❌ {err}", file=sys.stderr)
        return 1

    files = sorted(iter_jp2_files(root, args.recursive))
    if not files:
        print("⚠️  No JP2 files found.")
        return 0

    converter_names = ", ".join(Path(path).name for path, _, _ in converters)
    print(f"🔧 Using converter order: {converter_names}")
    converted = skipped = failed = 0

    progress_iter: Iterable[Path]
    total_files = len(files)
    if tqdm and sys.stderr.isatty():
        progress_iter = tqdm(files, desc="Converting", unit="file")
    else:
        progress_iter = files

    for jp2 in progress_iter:
        rel = jp2.relative_to(root)
        dst_path = jp2.with_suffix(output_suffix)
        last_error: Optional[str] = None
        status: Optional[str] = None
        detail: Optional[str] = None
        used_converter: Optional[str] = None
        for executable, builder, name in converters:
            status, detail = try_convert_with(
                jp2,
                dst_path,
                args.force,
                executable,
                builder,
                output_format,
                quality,
            )
            if status in {"ok", "skip"}:
                used_converter = name
                break
            # status == 'error'
            last_error = f"{name}: {detail}"
        else:
            failed += 1
            message = last_error or "All converters failed."
            if tqdm and sys.stderr.isatty():
                tqdm.write(f"❌ Failed {rel}: {message}")
            else:
                print(f"❌ Failed {rel}: {message}")
            continue

        if status == "ok":
            converted += 1
            message = f"✅ {rel} → {dst_path.name} [{used_converter}]"
            if detail:
                message = f"{message}\n{detail}"
            if tqdm and sys.stderr.isatty():
                tqdm.write(message)
            else:
                print(message)
        elif status == "skip":
            skipped += 1
            message = f"⏭️  Skipped {rel}: {detail}"
            if tqdm and sys.stderr.isatty():
                tqdm.write(message)
            else:
                print(message)

        label_metadata: Optional[Dict[str, Any]] = None
        label_warning: Optional[str] = None
        if args.tile_size:
            label_metadata, label_warning = load_label_metadata(jp2)
            if label_warning:
                warn_message = f"⚠️  {label_warning}"
                if tqdm and sys.stderr.isatty():
                    tqdm.write(warn_message)
                else:
                    print(warn_message)

        if args.tile_size and dst_path.exists():
            try:
                (
                    tiles_created,
                    tile_records,
                    (img_width, img_height),
                    image_mode,
                    base_tiles_dir,
                ) = split_into_tiles(
                    dst_path,
                    args.tile_size,
                    output_format,
                    quality if output_format == "jpeg" else None,
                    tiles_root,
                    root,
                )
            except Exception as tile_err:  # pragma: no cover - defensive
                message = f"⚠️  Tiling failed for {dst_path.name}: {tile_err}"
                if tqdm and sys.stderr.isatty():
                    tqdm.write(message)
                else:
                    print(message)
            else:
                try:
                    location = base_tiles_dir.relative_to(root)
                except ValueError:
                    location = base_tiles_dir
                scene_info = parse_scene_name(jp2)
                manifest = {
                    "source": jp2.name,
                    "source_path": relative_or_absolute(jp2, root),
                    "output_path": relative_or_absolute(dst_path, root),
                    "output_format": output_format,
                    "image_size": {"width": img_width, "height": img_height},
                    "tile_size": args.tile_size,
                    "image_mode": image_mode,
                    "tiles": tile_records,
                    "tiles_root": relative_or_absolute(base_tiles_dir, root),
                    "project_root": str(root),
                }
                manifest.update(scene_info)
                if label_metadata:
                    manifest["label_metadata"] = label_metadata
                manifest_path = write_manifest(base_tiles_dir, manifest)
                message = (
                    f"🧩  Created {tiles_created} tile(s) in {location}"
                    f" (manifest: {manifest_path.name})"
                )
                if tqdm and sys.stderr.isatty():
                    tqdm.write(message)
                else:
                    print(message)

    print(
        f"\nDone. Converted: {converted}, Skipped: {skipped}, Failed: {failed}."
    )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
