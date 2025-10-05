from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..services import dataset, search

router = APIRouter(prefix="/api", tags=["api"])


class InitRequest(BaseModel):
    force: bool = False
    recursive: bool = True
    output_format: str = "jpg"
    quality: int = 85
    tile_size: int = 2048
    tiles_dir: str = "tiles"
    converter: Optional[str] = None
    data_path: Optional[str] = None


@router.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@router.post("/init")
def initialize_dataset(payload: InitRequest) -> Dict[str, Any]:
    data_path = dataset.resolve_data_path(payload.data_path)
    existing = dataset.list_manifests(data_path)
    ready = dataset.manifests_ready(existing)
    if ready and not payload.force:
        return {
            "status": "skipped",
            "message": "Tiles already present; returning existing manifests.",
            "manifests": existing,
        }
    try:
        result = dataset.run_converter(
            data_path=data_path,
            recursive=payload.recursive,
            force=payload.force or not ready,
            output_format=payload.output_format,
            quality=payload.quality,
            tile_size=payload.tile_size,
            tiles_dir=payload.tiles_dir,
            converter=payload.converter,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        detail = exc.args[1] if len(exc.args) > 1 else str(exc)
        raise HTTPException(status_code=500, detail=detail) from exc
    except Exception as exc:  # pragma: no cover - surface error
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    manifests = result.get("manifests", dataset.list_manifests(data_path))
    return {
        "status": "ok",
        "stdout": result.get("stdout"),
        "stderr": result.get("stderr"),
        "manifests": manifests,
    }


@router.get("/images")
def list_images() -> Dict[str, Any]:
    manifests = dataset.list_manifests()
    return {"count": len(manifests), "items": manifests}


@router.get("/images/{scene_id}")
def get_image(scene_id: str) -> Dict[str, Any]:
    manifest = dataset.load_manifest_for_scene(scene_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="Scene not found")
    return manifest


@router.get("/images/{scene_id}/tiles/{row}/{col}")
def get_tile(scene_id: str, row: int, col: int):
    manifest = dataset.load_manifest_for_scene(scene_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="Scene not found")
    tiles_root = Path(manifest.get("tiles_root_path"))
    target = next(
        (
            tiles_root / tile.get("path")
            for tile in manifest.get("tiles", [])
            if tile.get("row") == row and tile.get("col") == col
        ),
        None,
    )
    if not target or not target.exists():
        raise HTTPException(status_code=404, detail="Tile not found")
    return FileResponse(target)


@router.get("/images/{scene_id}/crop")
def crop_scene(
    scene_id: str,
    min_lat: float = Query(...),
    min_lon: float = Query(...),
    max_lat: float = Query(...),
    max_lon: float = Query(...),
    background_tasks: BackgroundTasks = None,
) -> FileResponse:
    manifest = dataset.load_manifest_for_scene(scene_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="Scene not found")
    manifest_path = Path(manifest["manifest_path"])
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp:
        temp_path = Path(temp.name)
    try:
        dataset.crop_by_latlon(
            dataset.CropRequest(
                manifest_path=manifest_path,
                min_lat=min_lat,
                min_lon=min_lon,
                max_lat=max_lat,
                max_lon=max_lon,
                output_path=temp_path,
            )
        )
    except ValueError as exc:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if background_tasks is None:
        background_tasks = BackgroundTasks()
    background_tasks.add_task(_safe_unlink, temp_path)
    return FileResponse(temp_path, filename=f"{scene_id}_crop.png", background=background_tasks)


@router.get("/images/{scene_id}/full")
def download_full(scene_id: str, background_tasks: BackgroundTasks = None) -> FileResponse:
    manifest = dataset.load_manifest_for_scene(scene_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="Scene not found")
    manifest_path = Path(manifest["manifest_path"])
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temp:
        temp_path = Path(temp.name)
    dataset.stitch_full_scene(manifest_path, temp_path)
    if background_tasks is None:
        background_tasks = BackgroundTasks()
    background_tasks.add_task(_safe_unlink, temp_path)
    return FileResponse(temp_path, filename=f"{scene_id}_full.jpg", background=background_tasks)


@router.get("/images/{scene_id}/preview")
def download_preview(scene_id: str) -> FileResponse:
    manifest = dataset.load_manifest_for_scene(scene_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="Scene not found")
    preview_path = dataset.resolve_preview_path(manifest)
    if not preview_path:
        raise HTTPException(status_code=404, detail="Preview not available for this scene")
    return FileResponse(preview_path)


def _safe_unlink(path: Path) -> None:  # pragma: no cover - best effort cleanup
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


@router.get("/search")
def search_index(q: str, limit: int = 20) -> Dict[str, Any]:
    try:
        return search.search(q, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
