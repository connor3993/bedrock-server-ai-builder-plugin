"""Writes and reads Litematica schematics (.litematic): gzipped NBT with one region, block states packed into longs."""

import time
from pathlib import Path

from nbtlib import Compound, File, Int, List, Long, LongArray, String, load

from .design import Pos, join_state, split_state

# Minecraft 1.21.10: palette entries are written in Litematica's {Name, Properties} form, and the game brings a file
# of an older data version up to date when it loads it. Stamped with a newer version (26.3 is 5023) the game would take
# the old form as current and read every block as air.
DATA_VERSION = 4556
AIR = "minecraft:air"
# Empty positions are written as structure void: bots leave what stands there and can scaffold through it.
VOID = "minecraft:structure_void"

_SNOWY_BLOCKS = {"grass_block", "mycelium", "podzol"}
_AXIS_BLOCKS = {
    "bamboo_block", "stripped_bamboo_block", "basalt", "polished_basalt",
    "bone_block", "hay_block", "quartz_pillar", "purpur_pillar", "deepslate",
    "infested_deepslate", "muddy_mangrove_roots", "ochre_froglight",
    "verdant_froglight", "pearlescent_froglight", "chain", "iron_chain",
}
_DOUBLE_PLANTS = {
    "tall_grass", "large_fern", "sunflower", "lilac", "rose_bush",
    "peony", "pitcher_plant",
}
_FACING_BLOCKS = {
    "ladder", "chest", "trapped_chest", "ender_chest", "dispenser", "dropper",
    "stonecutter", "loom", "anvil", "chipped_anvil", "damaged_anvil",
    "carved_pumpkin", "jack_o_lantern", "shulker_box", "chiseled_bookshelf",
    "calibrated_sculk_sensor", "crafter", "big_dripleaf", "small_dripleaf",
    "pink_petals", "wildflowers", "leaf_litter",
}
_FACING_UP_BLOCKS = {
    "end_rod", "amethyst_cluster", "large_amethyst_bud",
    "medium_amethyst_bud", "small_amethyst_bud",
}
_AGE_BLOCKS = {
    "wheat", "carrots", "potatoes", "beetroots", "nether_wart",
    "sweet_berry_bush", "pumpkin_stem", "melon_stem", "sugar_cane",
    "cactus", "chorus_flower", "frosted_ice", "fire",
}


def default_properties(block: str) -> dict[str, str]:
    """Return required standard Java Edition default blockstate properties for a block ID.

    Standard Litematica files store full blockstate Properties on every BlockStatePalette entry;
    omitting defaults causes strict Java viewers (e.g. DeepSlate / Litematica mod) to miss variants.
    """
    short = block.split(":", 1)[1] if ":" in block else block
    if short in ("air", "cave_air", "void_air", "structure_void"):
        return {}
    if short in _SNOWY_BLOCKS:
        return {"snowy": "false"}
    if (
        short.endswith(("_log", "_wood", "_stem", "_hyphae"))
        or short in _AXIS_BLOCKS
    ):
        return {"axis": "y"}
    if short.endswith("_stairs"):
        return {"facing": "north", "half": "bottom", "shape": "straight"}
    if short.endswith("_slab"):
        return {"type": "bottom"}
    if short.endswith("_door"):
        return {"facing": "north", "half": "lower", "hinge": "left", "open": "false"}
    if short.endswith("_trapdoor"):
        return {"facing": "north", "half": "bottom", "open": "false"}
    if short.endswith("_fence_gate"):
        return {"facing": "north", "in_wall": "false", "open": "false"}
    if short.endswith("_button"):
        return {"face": "wall", "facing": "north", "powered": "false"}
    if short in ("heavy_weighted_pressure_plate", "light_weighted_pressure_plate"):
        return {"power": "0"}
    if short.endswith("_pressure_plate"):
        return {"powered": "false"}
    if short.endswith("_bed"):
        return {"facing": "north", "part": "foot"}
    if short == "lantern" or short.endswith("_lantern"):
        return {"hanging": "false"}
    if short == "candle_cake" or short.endswith("_candle_cake"):
        return {"lit": "false"}
    if short == "candle" or short.endswith("_candle"):
        return {"candles": "1", "lit": "false"}
    if short == "redstone_wall_torch":
        return {"facing": "north", "lit": "true"}
    if short == "redstone_torch":
        return {"lit": "true"}
    if short.endswith((
        "_wall_hanging_sign", "_wall_sign", "_wall_torch", "_wall_banner",
        "_wall_fan", "_wall_skull", "_wall_head", "_glazed_terracotta", "_shulker_box",
    )) or short == "wall_torch":
        return {"facing": "north"}
    if short.endswith("_hanging_sign"):
        return {"attached": "false", "rotation": "0"}
    if short.endswith(("_sign", "_banner")):
        return {"rotation": "0"}
    if short in ("furnace", "blast_furnace", "smoker"):
        return {"facing": "north", "lit": "false"}
    if short in ("campfire", "soul_campfire"):
        return {"facing": "north", "lit": "true"}
    if short == "barrel":
        return {"facing": "up", "open": "false"}
    if short == "bell":
        return {"attachment": "floor", "facing": "north"}
    if short == "hopper":
        return {"facing": "down"}
    if short == "grindstone":
        return {"face": "floor", "facing": "north"}
    if short == "lectern":
        return {"facing": "north", "has_book": "false"}
    if short == "observer":
        return {"facing": "north", "powered": "false"}
    if short == "lightning_rod" or short.endswith("_lightning_rod"):
        return {"facing": "up", "powered": "false"}
    if short in ("piston", "sticky_piston"):
        return {"extended": "false", "facing": "north"}
    if short == "repeater":
        return {"delay": "1", "facing": "north", "locked": "false", "powered": "false"}
    if short == "comparator":
        return {"facing": "north", "mode": "compare", "powered": "false"}
    if short == "lever":
        return {"face": "wall", "facing": "north", "powered": "false"}
    if short == "rail":
        return {"shape": "north_south"}
    if short in ("powered_rail", "detector_rail", "activator_rail"):
        return {"powered": "false", "shape": "north_south"}
    if short in _DOUBLE_PLANTS:
        return {"half": "lower"}
    if short == "snow":
        return {"layers": "1"}
    if short == "farmland":
        return {"moisture": "0"}
    if short == "composter":
        return {"level": "0"}
    if short in ("water_cauldron", "powder_snow_cauldron"):
        return {"level": "1"}
    if short in ("water", "lava"):
        return {"level": "0"}
    if short in ("beehive", "bee_nest"):
        return {"facing": "north", "honey_level": "0"}
    if short == "end_portal_frame":
        return {"eye": "false", "facing": "north"}
    if short.endswith("_copper_bulb") or short == "copper_bulb":
        return {"lit": "false", "powered": "false"}
    if short in _FACING_UP_BLOCKS:
        return {"facing": "up"}
    if short in _FACING_BLOCKS:
        return {"facing": "north"}
    if short in _AGE_BLOCKS:
        return {"age": "0"}
    return {}


def _normalize_state(state: str) -> str:
    block, props = split_state(state)
    defaults = default_properties(block)
    if not defaults:
        return state
    merged = dict(defaults)
    merged.update(props)
    return join_state(block, merged)


def _vec(x, y, z) -> Compound:
    return Compound({"x": Int(x), "y": Int(y), "z": Int(z)})


def _bits(entries: int) -> int:
    return max(2, (entries - 1).bit_length())


def pack(values: list[int], bits: int) -> list[int]:
    """values packed bits wide into 64-bit words, low bits first, an entry running on into the next word (Litematica's
    LitematicaBitArray), as signed longs."""
    words = [0] * ((len(values) * bits + 63) // 64)
    mask = (1 << 64) - 1
    for i, v in enumerate(values):
        start = i * bits
        word, offset = start >> 6, start & 63
        words[word] |= (v << offset) & mask
        if offset + bits > 64:
            words[word + 1] |= v >> (64 - offset)
    return [w - (1 << 64) if w >= 1 << 63 else w for w in words]


def unpack(words: list[int], bits: int, count: int) -> list[int]:
    big = [w & ((1 << 64) - 1) for w in words]
    out = []
    for i in range(count):
        start = i * bits
        word, offset = start >> 6, start & 63
        v = big[word] >> offset
        if offset + bits > 64:
            v |= big[word + 1] << (64 - offset)
        out.append(v & ((1 << bits) - 1))
    return out


def write(path: Path, name: str, blocks: dict[Pos, str], author: str = "schematic-architect", description: str = ""):
    """Writes blocks (lowest corner at 0, 0, 0) as a one-region schematic named name; the rest of the bounds is
    structure void."""
    sx, sy, sz = (max(p[i] for p in blocks) + 1 for i in range(3)) if blocks else (1, 1, 1)
    norm_blocks = {pos: _normalize_state(state) for pos, state in blocks.items()}
    palette = [VOID] + sorted(set(norm_blocks.values()))
    index = {state: i for i, state in enumerate(palette)}
    values = [0] * (sx * sy * sz)
    for (x, y, z), state in norm_blocks.items():
        values[(y * sz + z) * sx + x] = index[state]
    now = Long(int(time.time() * 1000))
    states = List[Compound]()
    for state in palette:
        block, props = split_state(state)
        entry = Compound({"Name": String(block)})
        if props:
            entry["Properties"] = Compound({k: String(v) for k, v in props.items()})
        states.append(entry)
    region = Compound({
        "Position": _vec(0, 0, 0),
        "Size": _vec(sx, sy, sz),
        "BlockStatePalette": states,
        "BlockStates": LongArray(pack(values, _bits(len(palette)))),
        "TileEntities": List[Compound](),
        "Entities": List[Compound](),
        "PendingBlockTicks": List[Compound](),
        "PendingFluidTicks": List[Compound](),
    })
    root = Compound({
        "Version": Int(6),
        "SubVersion": Int(1),
        "MinecraftDataVersion": Int(DATA_VERSION),
        "Metadata": Compound({
            "Name": String(name),
            "Author": String(author),
            "Description": String(description),
            "RegionCount": Int(1),
            "TotalVolume": Int(sx * sy * sz),
            "TotalBlocks": Int(len(blocks)),
            "TimeCreated": now,
            "TimeModified": now,
            "EnclosingSize": _vec(sx, sy, sz),
        }),
        "Regions": Compound({name: region}),
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    File(root, gzipped=True).save(path)


def read(path: Path) -> tuple[str, dict[Pos, str]]:
    """The name and blocks of a schematic, its regions merged from their lowest corner (later regions win), air and structure void left out."""
    root = load(path, gzipped=True)
    name = str(root["Metadata"]["Name"])
    blocks: dict[Pos, str] = {}
    for region in root["Regions"].values():
        pos = [int(region["Position"][k]) for k in "xyz"]
        size = [int(region["Size"][k]) for k in "xyz"]
        lo = [p + s + 1 if s < 0 else p for p, s in zip(pos, size)]
        sx, sy, sz = (abs(s) for s in size)
        palette = [join_state(str(e["Name"]), {k: str(v) for k, v in e.get("Properties", {}).items()})
                   for e in region["BlockStatePalette"]]
        values = unpack([int(w) for w in region["BlockStates"]], _bits(len(palette)), sx * sy * sz)
        for i, v in enumerate(values):
            x, z, y = i % sx, (i // sx) % sz, i // (sx * sz)
            p = (lo[0] + x, lo[1] + y, lo[2] + z)
            if v < len(palette) and palette[v] not in (AIR, VOID, "minecraft:cave_air"):
                blocks[p] = palette[v]
            else:
                blocks.pop(p, None)
    if blocks:
        low = [min(p[i] for p in blocks) for i in range(3)]
        blocks = {(x - low[0], y - low[1], z - low[2]): b for (x, y, z), b in blocks.items()}
    return name, blocks
