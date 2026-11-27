from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .config import ARCHIVE_DIR
from .db import create_task, get_task, init_db
from .voxel import VoxelError, translate_block_state
from .worker import new_task_id, run_task


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AI Build", version="0.1.0", lifespan=lifespan)


_chunks: dict[str, dict] = {}
_chunk_lock = threading.Lock()


def _valid_pos(key: Any) -> bool:
    parts = str(key).split(",")
    if len(parts) != 3:
        return False
    try:
        [int(part) for part in parts]
    except ValueError:
        return False
    return True


@app.post("/internal/voxels/{task_id}")
async def push_voxels(task_id: str, payload: dict) -> dict[str, bool]:
    sets = payload.get("set") or {}
    removes = payload.get("remove") or []
    if not isinstance(sets, dict) or not isinstance(removes, list):
        raise HTTPException(status_code=400, detail="set must be an object and remove must be an array")
    bad_pos = [key for key in sets if not _valid_pos(key)] + [key for key in removes if not _valid_pos(key)]
    if bad_pos:
        raise HTTPException(status_code=400, detail=f"invalid voxel position: {bad_pos[0]}")

    translated_sets: dict[str, dict[str, Any]] = {}
    translated_removes: list[str] = list(removes)
    for key, value in sets.items():
        if isinstance(value, str):
            if not value.startswith("minecraft:"):
                raise HTTPException(status_code=400, detail="block ids must be minecraft:* strings")
            try:
                entry = translate_block_state(value)
            except VoxelError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if entry is None:
                if value in ("minecraft:air", "minecraft:cave_air", "minecraft:void_air"):
                    translated_removes.append(str(key))
                continue
            translated_sets[str(key)] = entry
        elif isinstance(value, dict) and isinstance(value.get("id"), str) and value["id"].startswith("minecraft:"):
            translated_sets[str(key)] = value
        else:
            raise HTTPException(status_code=400, detail="block ids must be minecraft:* strings or translated block objects")

    if not translated_sets and not translated_removes:
        return {"ok": True}
    with _chunk_lock:
        slot = _chunks.setdefault(task_id, {"seq": 0, "items": []})
        slot["seq"] += 1
        record = {
            "seq": slot["seq"],
            "received_at": datetime.now(timezone.utc).isoformat(),
            "set": translated_sets,
            "remove": translated_removes,
        }
        slot["items"].append(record)
    return {"ok": True}


@app.get("/v1/build/{task_id}/chunks")
def get_chunks(task_id: str, after: int = 0) -> dict[str, Any]:
    with _chunk_lock:
        slot = _chunks.get(task_id) or {"seq": 0, "items": []}
        items = [item for item in slot["items"] if item["seq"] > after]
        return {"seq": slot["seq"], "items": items}


@app.get("/v1/build/{task_id}/files")
def list_files(task_id: str) -> dict[str, Any]:
    folder = ARCHIVE_DIR / task_id
    if not folder.is_dir():
        return {"task_id": task_id, "files": []}
    structure_suffixes = {".litematic", ".mcstructure"}
    files = [
        {"name": item.name, "size": item.stat().st_size}
        for item in sorted(folder.iterdir())
        if item.is_file() and item.suffix.lower() in structure_suffixes
    ]
    return {"task_id": task_id, "files": files}
@app.get("/v1/build/{task_id}/audit")
def get_audit(task_id: str) -> dict[str, Any]:
    manifest = ARCHIVE_DIR / task_id / "manifest.json"
    if not manifest.is_file():
        raise HTTPException(status_code=404, detail="audit not found")
    return json.loads(manifest.read_text(encoding="utf-8"))


@app.get("/v1/build/{task_id}/download/{name}")
def download_file(task_id: str, name: str) -> FileResponse:
    folder = (ARCHIVE_DIR / task_id).resolve()
    target = (folder / name).resolve()
    if folder not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(target, filename=name)


class BuildRequest(BaseModel):
    player_id: str | None = None
    prompt: str = Field(default="改造这个区域")
    palette: str = "oak"
    mode: str = "3d"
    context: dict[str, Any] | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/build", status_code=202)
async def create_build(req: BuildRequest) -> dict[str, str]:
    task_id = new_task_id()
    size = req.context.get("size") if req.context else None
    size_text = "x".join(str(v) for v in size) if size else "?"
    create_task(task_id, req.player_id, req.prompt, req.palette, size_text)
    asyncio.create_task(run_task(task_id, req.model_dump()))
    return {"task_id": task_id, "status": "queued"}


@app.get("/v1/build/{task_id}")
def get_build(task_id: str) -> dict[str, Any]:
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    resp: dict[str, Any] = {
        "task_id": task_id,
        "player_id": task.get("player_id"),
        "status": task["status"],
    }
    if task.get("error"):
        resp["error"] = task["error"]
    if task["status"] == "done" and task.get("result_path"):
        resp["result"] = json.loads(Path(task["result_path"]).read_text(encoding="utf-8"))
    elapsed = 0
    started = task.get("started_at")
    if started:
        try:
            elapsed = int((datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds() * 1000)
        except Exception:
            elapsed = 0
    resp["progress"] = {
        "stage": task.get("stage") or task["status"],
        "steps": task.get("steps") or 0,
        "tool": task.get("tool") or "",
        "elapsed_ms": max(0, elapsed),
    }
    return resp
