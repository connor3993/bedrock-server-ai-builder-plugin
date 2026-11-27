# -*- coding: utf-8 -*-
"""
litematic.py - .litematic 投影文件解析器

.litematic 结构（gzip 压缩的 Java NBT，大端）:
  root (Compound)
    ├── Version: int
    ├── MinecraftDataVersion: int
    ├── Metadata: Compound (Name/Author/TimeCreated/TotalBlocks...)
    └── Regions: Compound
          └── <区域名>: Compound
                ├── Position: Compound {x,y,z}   区域参考点世界坐标
                ├── Size: List<int> [sx,sy,sz]   可能为负（表示向负方向延伸）
                ├── BlockStatePalette: List<Compound {Name, Properties?}>
                ├── BlockStates: LongArray       位打包的调色板索引
                ├── TileEntities: List<Compound>
                └── Entities: List<Compound>
"""
import math

from nbtlib import (
    NBTError, load_java_nbt, CompoundTag, ListTag, LongArrayTag, StringTag,
)


class LitematicError(Exception):
    """投影文件解析错误（面向用户的友好提示）"""


def blockstate_to_string(name, props):
    """把调色板项转成 'minecraft:oak_log[axis=y]' 形式"""
    if not props:
        return name
    inner = ",".join(f"{k}={v}" for k, v in sorted(props.items()))
    return f"{name}[{inner}]"


def parse_blockstate(state_str):
    """解析 'minecraft:oak_log[axis=y]' → ('minecraft:oak_log', {'axis':'y'})"""
    if "[" in state_str:
        name, rest = state_str.split("[", 1)
        rest = rest.rstrip("]")
        props = {}
        for pair in rest.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                props[k.strip()] = v.strip()
        return name, props
    return state_str, {}


class Region:
    """一个已解析的 Litematica 区域"""

    def __init__(self, name, size, min_corner, blocks, tile_entities, entity_count):
        self.name = name
        self.size = size                # (sx, sy, sz) 正数
        self.min_corner = min_corner    # 区域最小角点世界坐标 (x,y,z)
        self.blocks = blocks            # blocks[y][z][x] → 调色板索引
        self.tile_entities = tile_entities  # [{x,y,z,id,...}] 区域相对坐标
        self.entity_count = entity_count


def _unpack_block_states(longs, total, palette_size):
    """解包位压缩的长整型数组 → 每个方块的调色板索引列表（LitematicaBitArray：连续位流，跨 long）"""
    if palette_size <= 1:
        return [0] * total
    bits = max(2, math.ceil(math.log2(palette_size)))
    mask = (1 << bits) - 1
    big = [w & 0xFFFFFFFFFFFFFFFF for w in longs]
    indices = []
    for i in range(total):
        start = i * bits
        word, offset = start >> 6, start & 63
        if word >= len(big):
            raise LitematicError(
                "BlockStates 数据长度不足（文件可能损坏或被不支持的版本修改过）"
            )
        v = big[word] >> offset
        if offset + bits > 64:
            if word + 1 >= len(big):
                raise LitematicError(
                    "BlockStates 数据长度不足（文件可能损坏或被不支持的版本修改过）"
                )
            v |= big[word + 1] << (64 - offset)
        indices.append(v & mask)
    return indices


def load(path):
    """加载 .litematic → (metadata, [(Region, palette), ...])"""
    try:
        _, root = load_java_nbt(path)
    except NBTError as e:
        raise LitematicError(f"无法读取 NBT 数据: {e}")

    if not isinstance(root, CompoundTag) or "Regions" not in root:
        raise LitematicError(
            "文件不是有效的 Litematica 投影：缺少 'Regions' 节点。"
            "（.litematic 与 .schem/.nbt 格式不同，请确认输入文件类型）"
        )

    meta = {}
    if "Metadata" in root:
        for k, v in root["Metadata"].value.items():
            meta[k] = getattr(v, "value", None)

    out = []
    for region_name, rtag in root["Regions"].value.items():
        out.append(_parse_region(region_name, rtag))

    if not out:
        raise LitematicError("投影文件不包含任何区域（Regions 为空）")
    return meta, out


def _parse_region(name, rtag):
    def missing(key):
        raise LitematicError(f"区域 '{name}' 缺少必要节点 '{key}'，文件可能损坏")

    for key in ("Size", "BlockStatePalette", "BlockStates", "Position"):
        if key not in rtag:
            missing(key)

    size_tag = rtag["Size"]
    if isinstance(size_tag, CompoundTag):
        raw_size = [size_tag[k].value for k in ("x", "y", "z")]
    else:
        raw_size = [t.value for t in size_tag.items]
    if len(raw_size) != 3:
        raise LitematicError(f"区域 '{name}' 的 Size 格式异常")
    size = tuple(abs(v) for v in raw_size)

    pos = rtag["Position"]
    px, py, pz = pos["x"].value, pos["y"].value, pos["z"].value
    # 负尺寸区域：向负方向延伸，最小角点 = pos + size + 1
    min_corner = (
        px if raw_size[0] >= 0 else px + raw_size[0] + 1,
        py if raw_size[1] >= 0 else py + raw_size[1] + 1,
        pz if raw_size[2] >= 0 else raw_size[2] + 1 + pz,
    )

    palette = []
    for entry in rtag["BlockStatePalette"].items:
        bname = entry["Name"].value
        props = {}
        if "Properties" in entry:
            props = {k: v.value for k, v in entry["Properties"].value.items()}
        palette.append(blockstate_to_string(bname, props))
    if not palette:
        raise LitematicError(f"区域 '{name}' 的方块调色板为空")

    block_states_tag = rtag["BlockStates"]
    if not isinstance(block_states_tag, LongArrayTag):
        raise LitematicError(f"区域 '{name}' 的 BlockStates 类型异常")

    total = size[0] * size[1] * size[2]
    indices = _unpack_block_states(block_states_tag.value, total, len(palette))

    sx, sy, sz = size
    blocks = [[[0] * sx for _ in range(sz)] for _ in range(sy)]
    for i, pal_idx in enumerate(indices):
        if pal_idx >= len(palette):
            raise LitematicError(f"区域 '{name}' 中存在越界的调色板索引 {pal_idx}")
        x = i % sx
        z = (i // sx) % sz
        y = i // (sx * sz)
        blocks[y][z][x] = pal_idx

    tile_entities = []
    if "TileEntities" in rtag:
        for te in rtag["TileEntities"].items:
            te_dict = {k: v for k, v in te.value.items()}
            tile_entities.append(te_dict)

    entity_count = 0
    if "Entities" in rtag:
        entity_count = len(rtag["Entities"].items)

    return Region(name, size, min_corner, blocks, tile_entities, entity_count), palette
