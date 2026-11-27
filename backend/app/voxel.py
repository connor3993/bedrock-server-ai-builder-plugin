"""Convert .litematic to the voxel JSON format shared with the plugin."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from array import array
from pathlib import Path
from typing import Any

VENDOR = Path(__file__).resolve().parent.parent / "vendor"
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

from litematic import LitematicError, load as load_litematic, parse_blockstate  # noqa: E402
from mapping import BLOCK_STATE_VERSION, translate  # noqa: E402

from .config import SCHEMATIC_PYTHON, SCHEMATIC_SRC  # noqa: E402


class VoxelError(Exception):
    pass


_WEIRDO_TO_FACING = {0: "east", 1: "west", 2: "south", 3: "north"}
_CARDINAL_TO_FACING = {0: "south", 1: "west", 2: "north", 3: "east"}
_DIR6_TO_FACING = {0: "down", 1: "up", 2: "north", 3: "south", 4: "west", 5: "east"}
_WATER_IDS = {"minecraft:water", "minecraft:flowing_water"}
_NON_GROUND_IDS = {
    "minecraft:air",
    "minecraft:cave_air",
    "minecraft:void_air",
    "minecraft:structure_void",
    "minecraft:water",
    "minecraft:flowing_water",
    "minecraft:lava",
    "minecraft:flowing_lava",
    "minecraft:seagrass",
    "minecraft:tall_seagrass",
    "minecraft:kelp",
    "minecraft:kelp_plant",
    "minecraft:short_grass",
    "minecraft:tallgrass",
    "minecraft:tall_grass",
    "minecraft:fern",
    "minecraft:large_fern",
    "minecraft:snow_layer",
    "minecraft:deadbush",
    "minecraft:dandelion",
    "minecraft:poppy",
    "minecraft:yellow_flower",
    "minecraft:red_flower",
    "minecraft:waterlily",
    "minecraft:lily_pad",
    "minecraft:vine",
    "minecraft:sugar_cane",
}


def translate_block_state(state_str: str) -> dict[str, Any] | None:
    """Translate a Java/schematic block state string into Bedrock {id, states, version}.

    Returns None for air / structure_void.
    """
    if not state_str:
        return None
    if state_str in ("minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:structure_void"):
        return None
    if not state_str.startswith("minecraft:"):
        raise VoxelError(f"无法识别的方块名：{state_str}（需要标准 ID，不能是本地化名称）")
    name, props = parse_blockstate(state_str)
    res = translate(name, props)
    if res.name in ("minecraft:air", "minecraft:structure_void"):
        return None
    states = {k: [t, v] for k, (t, v) in (res.states or {}).items()}
    if res.name.endswith("_stairs"):
        states.setdefault("weirdo_direction", ["i", 0])
        states.setdefault("upside_down_bit", ["b", 0])
    elif res.name in ("minecraft:lantern", "minecraft:soul_lantern"):
        hb = states.pop("hanging_bit", None)
        if "hanging" not in states:
            states["hanging"] = hb if hb is not None else ["b", 0]
    elif res.name.endswith("_slab"):
        states.setdefault("minecraft:vertical_half", ["s", "bottom"])
    return {
        "id": res.name,
        "states": states,
        "version": BLOCK_STATE_VERSION,
    }


def _unwrap_state_val(v: Any) -> Any:
    if isinstance(v, (list, tuple)) and len(v) == 2 and v[0] in ("s", "i", "b"):
        return v[1]
    return v


def _bedrock_to_java_state(block_id: str, raw_props: dict[str, Any] | None) -> str:
    """Normalize a Bedrock world snapshot block into a Java-compatible blockstate string."""
    bid = "minecraft:grass_block" if block_id == "minecraft:grass" else block_id
    props = raw_props or {}
    if not props:
        return bid
    out: dict[str, str] = {}
    short = bid.split(":", 1)[1] if ":" in bid else bid
    for k, rv in props.items():
        v = _unwrap_state_val(rv)
        if k == "pillar_axis" and str(v) in ("x", "y", "z"):
            out["axis"] = str(v)
        elif k == "weirdo_direction":
            try:
                f = _WEIRDO_TO_FACING.get(int(v))
                if f:
                    out["facing"] = f
            except (TypeError, ValueError):
                pass
        elif k == "upside_down_bit":
            out["half"] = "top" if bool(v) else "bottom"
        elif k == "minecraft:vertical_half" and str(v) in ("top", "bottom"):
            out["type"] = str(v)
        elif k in ("hanging", "hanging_bit"):
            out["hanging"] = "true" if bool(v) else "false"
        elif k == "liquid_depth":
            try:
                out["level"] = str(int(v))
            except (TypeError, ValueError):
                pass
        elif k == "minecraft:cardinal_direction" and str(v) in ("north", "south", "east", "west"):
            out["facing"] = str(v)
        elif k == "facing_direction":
            try:
                f = _DIR6_TO_FACING.get(int(v))
                if f:
                    out["facing"] = f
            except (TypeError, ValueError):
                pass
        elif k == "direction":
            try:
                f = _CARDINAL_TO_FACING.get(int(v))
                if f:
                    out["facing"] = f
            except (TypeError, ValueError):
                pass
        elif k == "open_bit":
            out["open"] = "true" if bool(v) else "false"
        elif k == "upper_block_bit" and (short.endswith("_door") or short in ("tall_grass", "large_fern", "sunflower", "lilac", "rose_bush", "peony")):
            out["half"] = "upper" if bool(v) else "lower"
        elif k == "door_hinge_bit":
            out["hinge"] = "right" if bool(v) else "left"
        elif k == "persistent_bit":
            out["persistent"] = "true" if bool(v) else "false"
        elif k == "torch_facing_direction":
            opp = {"north": "south", "south": "north", "east": "west", "west": "east"}
            if str(v) in opp:
                out["facing"] = opp[str(v)]
    if not out:
        return bid
    return bid + "[" + ",".join(f"{k}={v}" for k, v in sorted(out.items())) + "]"


def encode_int16(values: list[int]) -> str:
    return base64.b64encode(array("h", values).tobytes()).decode("ascii")


def decode_int16(data: str, count: int) -> list[int]:
    raw = base64.b64decode(data)
    values = array("h")
    values.frombytes(raw[: count * 2])
    return list(values)


def litematic_to_voxel(path: str | Path, max_y: int = 32, max_volume: int = 32 ** 3) -> dict[str, Any]:
    try:
        _meta, regions = load_litematic(str(path))
    except LitematicError as exc:
        raise VoxelError(str(exc)) from exc
    if not regions:
        raise VoxelError("litematic has no regions")

    min_x = min(r.min_corner[0] for r, _ in regions)
    min_y = min(r.min_corner[1] for r, _ in regions)
    min_z = min(r.min_corner[2] for r, _ in regions)
    max_x = max(r.min_corner[0] + r.size[0] for r, _ in regions)
    max_y = max(r.min_corner[1] + r.size[1] for r, _ in regions)
    max_z = max(r.min_corner[2] + r.size[2] for r, _ in regions)
    sx, sy, sz = max_x - min_x, max_y - min_y, max_z - min_z
    if 0 in (sx, sy, sz):
        raise VoxelError("litematic has a zero-sized region")
    if sy > max_y or sx * sy * sz > max_volume:
        raise VoxelError(f"structure too large: {sx}x{sy}x{sz}")

    total = sx * sy * sz
    values = [-1] * total
    palette: list[dict[str, Any]] = []
    index: dict[tuple[str, str], int] = {}

    cache: dict[str, dict[str, Any] | None] = {}

    def resolve(state_str: str) -> dict[str, Any] | None:
        if state_str not in cache:
            cache[state_str] = translate_block_state(state_str)
        return cache[state_str]

    for region, region_palette in regions:
        ox = region.min_corner[0] - min_x
        oy = region.min_corner[1] - min_y
        oz = region.min_corner[2] - min_z
        rsx, rsy, rsz = region.size
        local: list[int] = []
        for state_str in region_palette:
            entry = resolve(state_str)
            if entry is None:
                local.append(-1)
                continue
            key = (entry["id"], json.dumps(entry["states"], sort_keys=True))
            idx = index.get(key)
            if idx is None:
                idx = len(palette)
                palette.append(entry)
                index[key] = idx
            local.append(idx)

        for y in range(rsy):
            for z in range(rsz):
                for x in range(rsx):
                    v = local[region.blocks[y][z][x]]
                    if v < 0:
                        continue
                    i = (oy + y) * sx * sz + (oz + z) * sx + (ox + x)
                    values[i] = v

    return {
        "size": [sx, sy, sz],
        "palette": palette,
        "blocks": encode_int16(values),
    }

def voxel_to_litematic(context: dict[str, Any], path: str | Path, name: str, mode: str = "3d") -> None:
    """Write a plugin voxel snapshot (base64 int16 + palette) as a real .litematic.

    The file is produced by schematic_architect.litematic.write() running in that
    project's own venv: the backend vendors a minimal nbtlib shim that would
    shadow the real package. Bounds are forced to the full selection size.
    """
    try:
        sx, sy, sz = (int(v) for v in context["size"])
        palette = context.get("palette") or []
        values = decode_int16(context.get("blocks", ""), sx * sy * sz)
    except Exception as exc:  # noqa: BLE001
        raise VoxelError(f"bad context: {exc}") from exc
    if sx * sy * sz > 32 ** 3:
        raise VoxelError(f"selection too large: {sx}x{sy}x{sz} (limit 32^3)")

    blocks: dict[str, str] = {}
    for i, v in enumerate(values):
        if v < 0 or v >= len(palette):
            continue
        entry = palette[v]
        block_id = str(entry.get("id") or "")
        if not block_id or block_id in ("minecraft:air", "minecraft:cave_air", "minecraft:void_air"):
            continue
        x = i % sx
        y = i // (sx * sz)
        z = (i // sx) % sz
        props = entry.get("states") or {}
        state = _bedrock_to_java_state(block_id, props)
        blocks[f"{x},{y},{z}"] = state

    # litematic.write() derives bounds from the furthest block; anchor the far
    # corner with structure_void so the design is always the full selection size.
    if mode == "2d":
        for ax, az in ((0, 0), (sx - 1, 0), (0, sz - 1), (sx - 1, sz - 1)):
            blocks.setdefault(f"{ax},0,{az}", "minecraft:structure_void")
        blocks.setdefault(f"{sx - 1},{sy - 1},{sz - 1}", "minecraft:structure_void")
    else:
        blocks.setdefault("0,0,0", "minecraft:structure_void")
        blocks.setdefault(f"{sx - 1},{sy - 1},{sz - 1}", "minecraft:structure_void")

    payload = Path(tempfile.gettempdir()) / f"aibuild_{Path(path).stem}.json"
    payload.write_text(
        json.dumps({"name": name, "out": str(path), "blocks": blocks}, ensure_ascii=False),
        encoding="utf-8",
    )
    script = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from schematic_architect import litematic\n"
        "d = json.load(open(sys.argv[1], encoding='utf-8'))\n"
        "b = {tuple(int(v) for v in k.split(',')): s for k, s in d['blocks'].items()}\n"
        "litematic.write(Path(d['out']), d['name'], b)\n"
    )
    env = dict(os.environ, PYTHONPATH=str(SCHEMATIC_SRC))
    proc = subprocess.run(
        [str(SCHEMATIC_PYTHON), "-c", script, str(payload)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    if proc.returncode != 0:
        raise VoxelError(f"litematic write failed: {(proc.stderr or proc.stdout).strip()[:400]}")
def ground_heights(context: dict[str, Any]) -> list[list[int]]:
    """Highest solid non-water y per (x, z) column, or -1 when the column has no solid block."""
    sx, sy, sz = (int(v) for v in context["size"])
    palette = context.get("palette") or []
    values = decode_int16(context.get("blocks", ""), sx * sy * sz)
    rows: list[list[int]] = []
    for x in range(sx):
        row: list[int] = []
        for z in range(sz):
            height = -1
            for y in range(sy - 1, -1, -1):
                v = values[y * sx * sz + z * sx + x]
                if v < 0 or v >= len(palette):
                    continue
                bid = str((palette[v] or {}).get("id") or "")
                if bid in _NON_GROUND_IDS:
                    continue
                height = y
                break
            row.append(height)
        rows.append(row)
    return rows


def terrain_map(context: dict[str, Any]) -> dict[str, Any]:
    """Build a compact per-column terrain summary separating solid land from water surface."""
    sx, sy, sz = (int(v) for v in context["size"])
    palette = context.get("palette") or []
    values = decode_int16(context.get("blocks", ""), sx * sy * sz)
    solid = [[-1] * sz for _ in range(sx)]
    water = [[-1] * sz for _ in range(sx)]
    water_ys: set[int] = set()
    solid_ys: list[int] = []
    water_cols = 0
    land_cols = 0

    for x in range(sx):
        for z in range(sz):
            sy_top = -1
            wy_top = -1
            for y in range(sy - 1, -1, -1):
                v = values[y * sx * sz + z * sx + x]
                if v < 0 or v >= len(palette):
                    continue
                bid = str((palette[v] or {}).get("id") or "")
                if bid in _WATER_IDS:
                    if wy_top < 0:
                        wy_top = y
                    continue
                if bid in _NON_GROUND_IDS:
                    continue
                if sy_top < 0:
                    sy_top = y
                    break
            solid[x][z] = sy_top
            water[x][z] = wy_top
            if wy_top > sy_top:
                water_ys.add(wy_top)
                water_cols += 1
            elif sy_top >= 0:
                solid_ys.append(sy_top)
                land_cols += 1

    lines: list[str] = []
    for z in range(sz):
        cells: list[str] = []
        for x in range(sx):
            if water[x][z] > solid[x][z]:
                cells.append(f"w{water[x][z]}")
            elif solid[x][z] >= 0:
                cells.append(str(solid[x][z]))
            else:
                cells.append(".")
        lines.append(f"z={z}: " + " ".join(cells))

    return {
        "size": [sx, sy, sz],
        "lines": lines,
        "water_ys": sorted(water_ys),
        "solid_min": min(solid_ys) if solid_ys else None,
        "solid_max": max(solid_ys) if solid_ys else None,
        "water_cols": water_cols,
        "land_cols": land_cols,
    }
