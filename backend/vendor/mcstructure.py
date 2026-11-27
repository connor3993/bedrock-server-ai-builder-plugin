# -*- coding: utf-8 -*-
"""
mcstructure.py - 基岩版 .mcstructure 结构文件生成器

.mcstructure 为**小端序、无压缩**的 NBT，结构如下:
  root (Compound, 名为空)
    ├── format_version: int = 1
    ├── size: List<int> [x, y, z]
    ├── structure_world_origin: List<int> [0, 0, 0]
    └── structure: Compound
          ├── block_indices: List [ List<int> 主层, List<int> 含水层 ]
          ├── entities: List (空)
          └── palette: Compound
                └── default: Compound
                      ├── block_palette: List<Compound {name, states, version}>
                      └── block_position_data: Compound { "<索引>": {block_entity_data} }

扁平索引顺序: index = (x * sizeY + y) * sizeZ + z  （x 最快，y 最慢）
"""
from nbtlib import (
    CompoundTag, ListTag, IntTag, StringTag, ByteTag, ShortTag,
    TAG_INT, TAG_COMPOUND, save_bedrock_nbt,
)
from mapping import BLOCK_STATE_VERSION


def states_to_nbt(states):
    """{"facing_direction": ("i", 3), "open_bit": ("b", 1)} → CompoundTag"""
    comp = CompoundTag()
    for k, (t, v) in states.items():
        if t == "s":
            comp[k] = StringTag(v)
        elif t == "i":
            comp[k] = IntTag(v)
        elif t == "b":
            comp[k] = ByteTag(v)
        else:
            raise ValueError(f"未知状态类型: {t}")
    return comp


class McStructureBuilder:
    """收集方块与方块实体，最终写出 .mcstructure"""

    def __init__(self, size):
        self.sx, self.sy, self.sz = size
        self._palette = []            # [(name, states_tuple)]
        self._palette_index = {}      # key -> idx
        self.air_idx = self.palette_id("minecraft:air", {})
        total = self.sx * self.sy * self.sz
        self.layer0 = [self.air_idx] * total
        self.layer1 = [-1] * total    # 含水层（第二层），-1 = 无
        self.block_entities = {}      # flat_index -> bedrock block_entity_data (dict)

    def palette_id(self, name, states):
        key = (name, tuple(sorted(states.items())))
        if key in self._palette_index:
            return self._palette_index[key]
        idx = len(self._palette)
        self._palette_index[key] = idx
        self._palette.append((name, states))
        return idx

    def flat(self, x, y, z):
        return (x * self.sy + y) * self.sz + z

    def set_block(self, x, y, z, pal_idx, layer=0):
        idx = self.flat(x, y, z)
        if layer == 0:
            self.layer0[idx] = pal_idx
        else:
            self.layer1[idx] = pal_idx

    def set_block_entity(self, x, y, z, be_data):
        self.block_entities[self.flat(x, y, z)] = be_data

    def save(self, path):
        # block_palette
        palette_list = ListTag(TAG_COMPOUND)
        for name, states in self._palette:
            entry = CompoundTag()
            entry["name"] = StringTag(name)
            entry["states"] = states_to_nbt(states)
            entry["version"] = IntTag(BLOCK_STATE_VERSION)
            palette_list.items.append(entry)

        # block_position_data
        pos_data = CompoundTag()
        for idx, be in self.block_entities.items():
            wrapper = CompoundTag()
            wrapper["block_entity_data"] = dict_to_compound(be)
            pos_data[str(idx)] = wrapper

        default = CompoundTag()
        default["block_palette"] = palette_list
        default["block_position_data"] = pos_data

        palette_comp = CompoundTag()
        palette_comp["default"] = default

        layer0_list = ListTag(TAG_INT, [IntTag(v) for v in self.layer0])
        layer1_list = ListTag(TAG_INT, [IntTag(v) for v in self.layer1])
        block_indices = ListTag(9, [layer0_list, layer1_list])  # List of List

        structure = CompoundTag()
        structure["block_indices"] = block_indices
        structure["entities"] = ListTag(TAG_COMPOUND, [])
        structure["palette"] = palette_comp

        root = CompoundTag()
        root["format_version"] = IntTag(1)
        root["size"] = ListTag(TAG_INT, [IntTag(self.sx), IntTag(self.sy), IntTag(self.sz)])
        root["structure_world_origin"] = ListTag(TAG_INT, [IntTag(0), IntTag(0), IntTag(0)])
        root["structure"] = structure

        save_bedrock_nbt(path, root)


def dict_to_compound(d):
    """把 Python dict（值已是对应 Tag 或原生类型）转成 CompoundTag"""
    comp = CompoundTag()
    for k, v in d.items():
        comp[k] = _to_tag(v)
    return comp


def _to_tag(v):
    from nbtlib import Tag
    if isinstance(v, Tag):
        return v
    if isinstance(v, bool):
        return ByteTag(1 if v else 0)
    if isinstance(v, int):
        return IntTag(v)
    if isinstance(v, str):
        return StringTag(v)
    if isinstance(v, dict):
        return dict_to_compound(v)
    if isinstance(v, list):
        if not v:
            return ListTag(TAG_COMPOUND, [])
        tags = [_to_tag(x) for x in v]
        return ListTag(tags[0].tag_id, tags)
    raise ValueError(f"无法转换为 NBT 的值: {v!r}")


# ---------------- Java 方块实体 → 基岩版 ----------------

_BE_ID_MAP = {
    "minecraft:sign": "Sign", "minecraft:hanging_sign": "HangingSign",
    "minecraft:chest": "Chest", "minecraft:trapped_chest": "Chest",
    "minecraft:barrel": "Barrel", "minecraft:furnace": "Furnace",
    "minecraft:smoker": "Furnace", "minecraft:blast_furnace": "Furnace",
    "minecraft:dispenser": "Dispenser", "minecraft:dropper": "Dropper",
    "minecraft:hopper": "Hopper", "minecraft:shulker_box": "ShulkerBox",
    "minecraft:spawner": "MobSpawner", "minecraft:jukebox": "Jukebox",
    "minecraft:beacon": "Beacon", "minecraft:banner": "Banner",
    "minecraft:skull": "Skull", "minecraft:bed": "Bed",
    "minecraft:brewing_stand": "BrewingStand",
    "minecraft:enchanting_table": "EnchantTable",
    "minecraft:comparator": "Comparator", "minecraft:daylight_detector": "DaylightDetector",
    "minecraft:lectern": "Lectern", "minecraft:campfire": "Campfire",
    "minecraft:beehive": "Beehive", "minecraft:bell": "Bell",
    "minecraft:conduit": "Conduit", "minecraft:ender_chest": "EnderChest",
    "minecraft:command_block": "CommandBlock",
}


def convert_block_entity(te_tags, x, y, z):
    """把 Litematica 的 TileEntity（区域相对坐标）转成基岩版 block_entity_data。
    返回 (dict, note|None)；不支持时返回 (None, note)。"""
    te = {k: getattr(v, "value", v) for k, v in te_tags.items()}
    te_id = te.get("id", "")
    be_id = _BE_ID_MAP.get(te_id)
    if be_id is None:
        return None, f"方块实体 {te_id or '(未知)'} 暂不支持转换，已跳过（方块本身保留）"

    out = {"id": be_id, "x": x, "y": y, "z": z, "isMovable": 1}

    # 容器物品
    if "Items" in te_tags:
        items = []
        for item in te_tags["Items"].items:
            it = {k: getattr(v, "value", v) for k, v in item.value.items()}
            entry = {
                "Slot": ByteTag(it.get("Slot", 0)),
                "Name": it.get("id", "minecraft:air"),
                "Count": ByteTag(it.get("count", 1)),
                "Damage": ShortTag(0),
                "WasPickedUp": ByteTag(0),
            }
            items.append(entry)
        out["Items"] = items

    # 告示牌文字（Java 为 JSON 文本组件）
    if be_id in ("Sign", "HangingSign"):
        lines = []
        import json as _json
        for i in range(1, 5):
            raw = te.get(f"Text{i}", "")
            if isinstance(raw, str) and raw:
                try:
                    obj = _json.loads(raw)
                    txt = obj.get("text", "") if isinstance(obj, dict) else str(obj)
                except (ValueError, AttributeError):
                    txt = raw
                lines.append(txt)
        out["Text"] = "\n".join(lines).rstrip("\n")

    return out, None
