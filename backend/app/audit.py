"""Audit artifacts for AI build tasks.

The audit archive is structure-only: each task keeps the input .litematic the
agent opened, the output .litematic it exported, and a manifest with hashes.
Voxel JSON is a transport/cache artifact and is not stored here.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import MCSTRUCTURE_CONVERTER


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_pi_log(src: Path, task_dir: Path) -> Path | None:
    """Copy the raw Pi JSONL stream into the task archive when available."""
    if not src.is_file():
        return None
    dst = task_dir / "pi.jsonl"
    shutil.copy2(src, dst)
    return dst


def convert_litematic_to_mcstructure(src: Path, task_dir: Path, report_name: str) -> dict[str, Any]:
    """Best-effort conversion to Bedrock's native .mcstructure format.

    The source .litematic remains the authoritative pipeline artifact. A failed
    conversion is recorded in the manifest and must not fail the build task.
    """
    src = Path(src)
    task_dir = Path(task_dir)
    result: dict[str, Any] = {
        "ok": False,
        "input": src.name,
        "output": None,
        "report": None,
    }
    if not src.is_file():
        result["error"] = "source missing"
        return result
    if not MCSTRUCTURE_CONVERTER.is_file():
        result["error"] = f"converter missing: {MCSTRUCTURE_CONVERTER}"
        return result

    report = task_dir / report_name
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(MCSTRUCTURE_CONVERTER),
                str(src),
                "-o",
                str(task_dir),
                "--report",
                str(report),
                "--quiet",
            ],
            cwd=str(MCSTRUCTURE_CONVERTER.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"converter exception: {exc}"
        return result

    output = task_dir / f"{src.stem}.mcstructure"
    result["report"] = report.name if report.is_file() else None
    result["output"] = output.name if output.is_file() else None
    if proc.returncode != 0 or not output.is_file():
        detail = (proc.stderr or proc.stdout or "").strip()
        result["error"] = detail[:1200] or f"converter exit {proc.returncode}"
        return result

    result["ok"] = True
    return result


def build_manifest(
    task_id: str,
    task_dir: Path,
    status: str,
    payload: dict[str, Any],
    conversions: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    task_dir = Path(task_dir)
    files: list[dict[str, Any]] = []
    structure_suffixes = {".litematic", ".mcstructure"}
    for item in sorted(task_dir.iterdir()):
        if not item.is_file() or item.name == "manifest.json":
            continue
        if item.suffix.lower() not in structure_suffixes:
            continue
        files.append(
            {
                "name": item.name,
                "size": item.stat().st_size,
                "sha256": sha256_file(item),
            }
        )

    context = payload.get("context") or {}
    manifest = {
        "task_id": task_id,
        "player_id": payload.get("player_id") or None,
        "status": status,
        "completed_at": utc_now(),
        "prompt": payload.get("prompt"),
        "mode": payload.get("mode") or "3d",
        "palette": payload.get("palette"),
        "size": context.get("size"),
        "error": error,
        "conversions": conversions or {},
        "files": files,
        "notes": [
            "input.litematic 是 worker 从插件 context 生成、AI 实际 open_schematic 读取的输入结构。",
            "output.litematic 是 AI export 的最终结构文件。",
            "审计目录只保留结构文件和 manifest；体素 JSON 放在 builds 缓存，不进入审计归档。",
        ],
    }
    write_json(task_dir / "manifest.json", manifest)
    return manifest