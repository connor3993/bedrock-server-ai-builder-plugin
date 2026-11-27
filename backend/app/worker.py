"""Task worker: prompt + selection -> Pi (open_schematic) -> litematic -> voxel JSON."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import logging

from .audit import build_manifest
from .config import ARCHIVE_DIR, BUILD_DIR, TMP_DIR
from .db import update_progress, update_task
from .pi_rpc import PiError, run_agent
from .voxel import VoxelError, litematic_to_voxel, terrain_map, voxel_to_litematic

logger = logging.getLogger("ai_build.worker")


TOOL_EVENT_TYPES = ("tool_execution_start", "tool_call", "toolcall_start")


class Progress:
    """Counts Pi tool calls and mirrors them into the task row (throttled to 1/s)."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.steps = 0
        self.tool = ""
        self._last = 0.0
        self._seen: set[str] = set()

    def on_event(self, event: dict[str, Any]) -> None:
        etype = str(event.get("type") or "")
        if etype not in self._seen:
            self._seen.add(etype)
            logger.info("pi event type: %s", etype)
        if etype in TOOL_EVENT_TYPES:
            self.steps += 1
            call = event.get("toolCall") or {}
            self.tool = str(event.get("toolName") or event.get("name") or call.get("name") or "")
        now = time.monotonic()
        if now - self._last < 1.0:
            return
        self._last = now
        update_progress(self.task_id, steps=self.steps, tool=self.tool)

def build_prompt(task_id: str, payload: dict[str, Any], sel_path: Path, out_path: Path) -> str:
    prompt = str(payload.get("prompt") or "改造这个区域")
    palette = str(payload.get("palette") or "").strip()
    palette_line = (chr(10) + "调色板：" + palette) if palette and palette not in ("默认", "default") else ""
    context = payload.get("context") or {}
    size = context.get("size") or []
    dims = "x".join(str(v) for v in size) if size else "未知"

    mode = str(payload.get("mode") or "3d").lower()
    if mode == "2d":
        brief = """你在 Minecraft 基岩版服务器里建造。

2D 模式：选区只给出平面范围（X-Z），高度不设限制——input.litematic 已包含当前范围内的真实地形起伏（含水底、水面与坡地），你可以在选区 X-Z 范围内向上自由建高，想建多高就建多高；X、Z 不要超出选区，Y 可以超出 input.litematic 高度。

依照地形而建：地形包括地面、坡地、水面和凹地。建筑底部必须贴合地形或打桩落到水底/地面，不要悬空，也不要撞进旁边高出的山坡；顺着地势安排建筑，不要整平场地。除非玩家明确要求，否则保留原有地形，不要挖掉已有的地面和水。

空间布局与细节：结合整个选区尺寸合理铺开布局，主体建筑之外可搭配协调的配套环境（如延伸码头/栈道、小路、庭院、围栏、船只、灯柱、岸边小景等），充分利用可用空间，避免把建筑缩在角落留下大片空地。屋顶坡面两侧楼梯使用对应对称朝向（如西坡 facing=east、东坡 facing=west；北坡 facing=south、南坡 facing=north），屋顶填料与屋檐配色统一协调。玩家提示词优先，提示词里要求什么就按提示词来。"""
    else:
        brief = """你在 Minecraft 基岩版服务器里改造一个已经存在的选区结构。

3D 模式：只能在选区内改造，X/Y/Z 都不能超出选区。

选区里的地形、水、空气都已经在文件里。依照地形而建：顺着地势安排建筑，不要整平场地。除非玩家明确要求，否则保留原有地形，不要挖掉已有的地面和水。屋顶坡面两侧楼梯使用对应对称朝向，门窗、原木、栏杆等注意朝向和配色协调。玩家提示词优先，提示词里要求什么就按提示词来。"""

    height_block = ""
    if mode == "2d" and size:
        tmap = terrain_map(context)
        water_desc = ",".join(str(y) for y in tmap["water_ys"]) if tmap["water_ys"] else "无"
        if tmap["solid_min"] is not None:
            solid_desc = f"{tmap['solid_min']}..{tmap['solid_max']}"
        else:
            solid_desc = "无"
        hlines = [
            f"地形概览：input.litematic 高度 sy={tmap['size'][1]}（y>={tmap['size'][1]} 以上为空气可自由建高）；"
            f"水面列 {tmap['water_cols']} 格（水面 y={water_desc}），陆地列 {tmap['land_cols']} 格（实心地面 y={solid_desc}）。",
            "选区地形图（数字=该列最高实心地面 y；wN=水面 y=N 且水底在其下方；.=空列）：",
        ]
        hlines.extend(tmap["lines"])
        height_block = "\n".join(hlines)

    return f"""{brief}

{height_block}

任务：{prompt}{palette_line}
选区尺寸：{dims}（结构文件里就是这个大小）

第一步必须调用：
  open_schematic(path="{sel_path.as_posix()}", design="{task_id}")

禁止调用 new_design。

然后按需使用 box / line / cylinder / set_block / replace 等工具编辑。

最后必须调用：
  export(design="{task_id}", datapack="{out_path.as_posix()}", namespace="aibuild")

只调用工具，不要输出长篇解释。"""


async def run_task(task_id: str, payload: dict[str, Any]) -> None:
    task_dir = ARCHIVE_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    out_path = task_dir / "output.litematic"
    sel_path = task_dir / "input.litematic"
    context = payload.get("context") or {}
    event_log = TMP_DIR / f"{task_id}.pi.jsonl"
    final_status = "failed"
    final_error: str | None = None
    try:
        update_task(task_id, "generating")
        update_progress(
            task_id,
            stage="generating",
            steps=0,
            tool="",
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        progress = Progress(task_id)
        if not context.get("size"):
            raise VoxelError("请求缺少选区数据（context.size）")
        mode = str(payload.get("mode") or "3d").lower()
        voxel_to_litematic(context, sel_path, task_id, mode)
        prompt = build_prompt(task_id, payload, sel_path, out_path)
        retry_prompt = prompt + "\n\n上一轮中途断开了。请从头重新开始：先 open_schematic，然后直接调用建造工具，不要长篇规划，最后必须调用 export。"
        tail: list[str] = []

        def on_event(event: dict[str, Any]) -> None:
            try:
                with event_log.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            except Exception:  # noqa: BLE001
                logger.exception("write pi event log failed")
            raw = json.dumps(event, ensure_ascii=False)
            tail.append(raw[:400])
            del tail[:-8]
            progress.on_event(event)

        event_log.write_text("", encoding="utf-8")
        attempts = 2
        for attempt in range(1, attempts + 1):
            if out_path.exists():
                out_path.unlink()
            tail.clear()
            try:
                await run_agent(prompt if attempt == 1 else retry_prompt, on_event=on_event)
            except PiError as exc:
                if attempt >= attempts:
                    raise
                logger.warning("pi attempt %s failed: %s", attempt, exc)
                continue
            if out_path.exists():
                break
            if attempt >= attempts:
                raise PiError(f"agent finished but {out_path.name} was not written; 最近事件: " + " | ".join(tail[-4:]))
            logger.warning("pi attempt %s finished without output, retrying", attempt)

        update_task(task_id, "converting")
        update_progress(task_id, stage="converting", steps=progress.steps, tool=progress.tool)
        voxel = litematic_to_voxel(out_path, max_y=96 if mode == "2d" else 32, max_volume=400000 if mode == "2d" else 32 ** 3)
        want = [int(v) for v in context["size"]]
        got = [int(v) for v in voxel["size"]]
        if mode == "2d":
            if got[0] != want[0] or got[2] != want[2]:
                raise VoxelError(f"平面范围不一致：选区 {want[0]}x{want[2]} vs 产物 {got[0]}x{got[2]}")
            if got[1] < want[1]:
                raise VoxelError(f"高度不足：选区 {want[1]} vs 产物 {got[1]}")
        elif got != want:
            raise VoxelError(f"尺寸不一致：选区 {want} vs 产物 {got}")
        result_path = BUILD_DIR / f"{task_id}.json"
        result_path.write_text(json.dumps(voxel, ensure_ascii=False), encoding="utf-8")
        final_status = "done"
        update_task(task_id, "done", result_path=str(result_path))
        update_progress(task_id, stage="done")
    except (PiError, VoxelError) as exc:
        final_error = str(exc)
        update_task(task_id, "failed", error=final_error)
        update_progress(task_id, stage="failed")
    except Exception as exc:  # noqa: BLE001
        final_error = f"internal error: {exc}"
        update_task(task_id, "failed", error=final_error)
        update_progress(task_id, stage="failed")
    finally:
        try:
            build_manifest(task_id, task_dir, final_status, payload, error=final_error)
        except Exception:
            logger.exception("write archive manifest failed")

def new_task_id() -> str:
    return uuid.uuid4().hex[:12]