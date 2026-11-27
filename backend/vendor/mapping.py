# -*- coding: utf-8 -*-
"""
mapping.py - Java 版 → 基岩版 方块/方块状态翻译器

目标版本: 基岩版 1.20.x ~ 1.21.x（palette version 17959425）。
基岩版在游戏加载时会按 version 自动升级旧状态格式，因此旧式状态名
（如 direction / tallgrass）与新客户端兼容。

状态值类型: ("s", 字符串) | ("i", 整数) | ("b", 0/1 字节)
"""
import json
import os

# 基岩版方块状态数据版本（1.21.0），游戏加载时自动向后升级
BLOCK_STATE_VERSION = 17959425

# ---- 方向编码 ----
FACING_DIR6 = {"down": 0, "up": 1, "north": 2, "south": 3, "west": 4, "east": 5}
CARDINAL = {"south": 0, "west": 1, "north": 2, "east": 3}        # 床/门/南瓜/中继器等
WEIRDO = {"east": 0, "west": 1, "south": 2, "north": 3}          # 楼梯专用
TORCH_OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}

COLORS = ["white", "orange", "magenta", "light_blue", "yellow", "lime", "pink",
          "gray", "light_gray", "cyan", "purple", "blue", "brown", "green", "red", "black"]

WOODS = ["oak", "spruce", "birch", "jungle", "acacia", "dark_oak"]

# 状态构造辅助
def S(v):
    return ("s", str(v))

def I(v):
    return ("i", int(v))

def B(v):
    return ("b", 1 if v in (True, 1, "1", "true") else 0)


def _load_tables():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blockmap.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("rename", {}), data.get("replace", {})


RENAME, REPLACE = _load_tables()


class Result:
    """单个方块状态的翻译结果"""
    __slots__ = ("name", "states", "waterlogged", "status", "note")

    def __init__(self, name, states=None, waterlogged=False, status="ok", note=None):
        self.name = name
        self.states = states or {}
        self.waterlogged = waterlogged
        self.status = status  # ok / renamed / replaced / approx（近似转换）
        self.note = note


def _split_color(java_id):
    """minecraft:white_wool → ('white', 'wool')；无颜色前缀返回 None"""
    short = java_id.split(":", 1)[1]
    for c in COLORS:
        if short.startswith(c + "_"):
            return c, short[len(c) + 1:]
    return None


def translate(java_name, props):
    """主入口：java_name='minecraft:xxx', props=dict[str,str]
    返回 Result。"""
    props = dict(props)
    waterlogged = props.pop("waterlogged", "false") == "true"

    # 非原版（模组）方块：基岩版必然不存在 → 空气
    if not java_name.startswith("minecraft:"):
        return Result("minecraft:air", waterlogged=False, status="replaced",
                      note="模组方块，基岩版无法识别，已替换为空气")

    # 明确替代表（基岩版不存在该方块，优先于一切特殊处理器）
    if java_name in REPLACE:
        target = REPLACE[java_name]
        note = "基岩版无此方块，按映射表替换"
        if target.endswith("_slab") and props.get("type") == "top":
            return Result(target, {"minecraft:vertical_half": S("top")},
                          waterlogged=False, status="replaced", note=note)
        return Result(target, waterlogged=False, status="replaced", note=note)

    handler = _dispatch(java_name, props, waterlogged)
    if handler is not None:
        return handler

    # 简单改名表
    if java_name in RENAME:
        return Result(RENAME[java_name], waterlogged=waterlogged, status="renamed")

    # 兜底：同名直通 + 通用状态翻译
    states = _generic_states(props)
    return Result(java_name, states, waterlogged=waterlogged)


# ---------------------------------------------------------------- 分发

def _dispatch(java_id, props, waterlogged):
    short = java_id.split(":", 1)[1]
    colored = _split_color(java_id)

    # --- 染色方块族 ---
    if colored:
        color, base = colored
        r = _colored_family(color, base, props, waterlogged)
        if r:
            return r

    # --- 楼梯 ---
    if short.endswith("_stairs"):
        return _stairs(java_id, props, waterlogged)

    # --- 台阶 ---
    if short.endswith("_slab"):
        return _slab(java_id, props, waterlogged)

    # --- 门 / 活板门 / 栅栏门 ---
    if short.endswith("_door"):
        return _door(java_id, props, waterlogged)
    if short.endswith("_trapdoor"):
        return _trapdoor(java_id, props, waterlogged)
    if short.endswith("_fence_gate"):
        return _fence_gate(java_id, props, waterlogged)

    # --- 原木/菌柄/骨块类（axis）在通用状态中处理，这里处理改名的 ---
    # --- 树叶 ---
    if short.endswith("_leaves"):
        return _leaves(java_id, props, waterlogged)
    # --- 树苗 ---
    if short.endswith("_sapling"):
        return _sapling(java_id, props, waterlogged)
    # --- 木板 ---
    if short.endswith("_planks") and short.split("_")[0] in WOODS:
        return Result("minecraft:planks", {"wood_type": S(short.split("_")[0])},
                      waterlogged=waterlogged, status="renamed")
    # --- 栅栏 ---
    if short.endswith("_fence") and short.split("_")[0] in WOODS:
        return Result("minecraft:fence", {"wood_type": S(short.split("_")[0])},
                      waterlogged=waterlogged, status="renamed")
    # --- 墙 ---
    if short.endswith("_wall") and not short.endswith("_wall_sign") \
            and not short.endswith("_wall_torch") and "coral" not in short \
            and not short.endswith("_wall_fan") and "skull" not in short \
            and not short.endswith("_wall_head") and not short.endswith("_wall_banner") \
            and not short.endswith("_wall_corail"):
        r = _wall(java_id, props, waterlogged)
        if r:
            return r
    # --- 告示牌 ---
    if short.endswith("_wall_sign"):
        return _wall_sign(java_id, props, waterlogged)
    if short.endswith("_sign") and not short.endswith("_wall_sign"):
        return _standing_sign(java_id, props, waterlogged)
    # --- 旗帜 ---
    if short.endswith("_wall_banner"):
        return _wall_banner(java_id, props, waterlogged)
    if short.endswith("_banner"):
        return _standing_banner(java_id, props, waterlogged)
    # --- 头颅 ---
    if short.endswith("_wall_head") or short.endswith("_wall_skull"):
        return _skull(java_id, props, waterlogged, wall=True)
    if short.endswith("_head") or short.endswith("_skull"):
        return _skull(java_id, props, waterlogged, wall=False)
    # --- 珊瑚 ---
    if "_coral_wall_fan" in short:
        return _coral_wall_fan(java_id, props, waterlogged)
    if "_coral_fan" in short:
        return _coral_fan(java_id, props, waterlogged)
    if "_coral_block" in short:
        return _coral_block(java_id, props, waterlogged)
    if "_coral" in short:
        return _coral(java_id, props, waterlogged)
    # --- 火把 ---
    if short == "wall_torch" or short.endswith("_wall_torch"):
        return _wall_torch(java_id, props, waterlogged)
    if short == "torch" or short.endswith("_torch"):
        return _floor_torch(java_id, props, waterlogged)
    # --- 按钮 ---
    if short.endswith("_button"):
        return _button(java_id, props, waterlogged)
    # --- 压力板 ---
    if short.endswith("_pressure_plate"):
        return Result(java_id, {"redstone_signal": I(props.get("powered", "false") == "true" and 1 or 0)},
                      waterlogged=waterlogged)
    # --- 铁轨 ---
    if short == "rail" or short.endswith("_rail"):
        return _rail(java_id, props, waterlogged)

    # --- 精确匹配的特殊方块 ---
    exact = _exact(java_id, props, waterlogged)
    if exact is not None:
        return exact

    return None


# ---------------------------------------------------------------- 染色族

_COLORED_SIMPLE = {
    "wool": "minecraft:wool",
    "carpet": "minecraft:carpet",
    "concrete": "minecraft:concrete",
    "concrete_powder": "minecraft:concrete_powder",
    "terracotta": "minecraft:stained_hardened_clay",
    "stained_glass": "minecraft:stained_glass",
    "stained_glass_pane": "minecraft:stained_glass_pane",
}


def _colored_family(color, base, props, waterlogged):
    if base in _COLORED_SIMPLE:
        return Result(_COLORED_SIMPLE[base], {"color": S(color)},
                      waterlogged=waterlogged, status="renamed")
    if base == "bed":
        states = {"color": S(color),
                  "direction": I(CARDINAL.get(props.get("facing", "south"), 0))}
        if props.get("part") == "head":
            states["head_piece_bit"] = B(1)
        return Result("minecraft:bed", states, waterlogged=False,
                      status="renamed", note="床的 occupied 状态已忽略")
    if base == "shulker_box":
        states = {"color": S(color)}
        f = props.get("facing")
        if f in FACING_DIR6:
            states["facing_direction"] = I(FACING_DIR6[f])
        return Result("minecraft:shulker_box", states, waterlogged=waterlogged,
                      status="renamed")
    if base == "glazed_terracotta":
        states = {}
        f = props.get("facing")
        if f in FACING_DIR6:
            states["facing_direction"] = I(FACING_DIR6[f])
        return Result(f"minecraft:{color}_glazed_terracotta", states,
                      waterlogged=waterlogged)
    if base == "candle":
        states = {"candles": I(int(props.get("candles", "1")) - 1),
                  "lit": B(props.get("lit", "false"))}
        return Result(f"minecraft:{color}_candle", states, waterlogged=waterlogged)
    return None


# ---------------------------------------------------------------- 各类处理器

def _stairs(java_id, props, waterlogged):
    states = {}
    f = props.get("facing")
    if f in WEIRDO:
        states["weirdo_direction"] = I(WEIRDO[f])
    if props.get("half") == "top":
        states["upside_down_bit"] = B(1)
    # shape（拐角形状）基岩版自动连接，忽略
    return Result(java_id, states, waterlogged=waterlogged)


# 双层台阶 → 完整方块
_DOUBLE_SLAB_FULL = {
    "minecraft:stone_slab": "minecraft:stone",
    "minecraft:smooth_stone_slab": "minecraft:smooth_stone",
    "minecraft:sandstone_slab": "minecraft:sandstone",
    "minecraft:red_sandstone_slab": "minecraft:red_sandstone",
    "minecraft:cobblestone_slab": "minecraft:cobblestone",
    "minecraft:brick_slab": "minecraft:bricks",
    "minecraft:stone_brick_slab": "minecraft:stonebrick",
    "minecraft:nether_brick_slab": "minecraft:netherbrick",
    "minecraft:quartz_slab": "minecraft:quartz_block",
    "minecraft:smooth_quartz_slab": "minecraft:quartz_block",
    "minecraft:purpur_slab": "minecraft:purpur_block",
    "minecraft:prismarine_slab": "minecraft:prismarine",
    "minecraft:prismarine_brick_slab": "minecraft:prismarine",
    "minecraft:dark_prismarine_slab": "minecraft:prismarine",
    "minecraft:oak_slab": "minecraft:planks",
    "minecraft:spruce_slab": "minecraft:planks",
    "minecraft:birch_slab": "minecraft:planks",
    "minecraft:jungle_slab": "minecraft:planks",
    "minecraft:acacia_slab": "minecraft:planks",
    "minecraft:dark_oak_slab": "minecraft:planks",
    "minecraft:petrified_oak_slab": "minecraft:planks",
    "minecraft:cut_sandstone_slab": "minecraft:sandstone",
    "minecraft:cut_red_sandstone_slab": "minecraft:red_sandstone",
}


def _slab(java_id, props, waterlogged):
    stype = props.get("type", "bottom")
    if stype == "double":
        full = _DOUBLE_SLAB_FULL.get(java_id)
        if full:
            states = {}
            if full == "minecraft:planks":
                states["wood_type"] = S(java_id.split(":")[1].split("_")[0])
            if full == "minecraft:quartz_block" and "smooth" in java_id:
                states["chisel_type"] = S("smooth")
            if full == "minecraft:sandstone" and "cut" in java_id:
                states["sand_stone_type"] = S("cut")
            if full == "minecraft:red_sandstone" and "cut" in java_id:
                states["sand_stone_type"] = S("cut")
            return Result(full, states, waterlogged=False, status="approx",
                          note="双层台阶转为对应完整方块")
        # 无对应完整方块时退化为底部台阶
        return Result(java_id, {}, waterlogged=waterlogged, status="approx",
                      note="双层台阶无对应完整方块，已转为普通台阶")
    states = {}
    if stype == "top":
        states["minecraft:vertical_half"] = S("top")
    return Result(java_id, states, waterlogged=waterlogged)


def _door(java_id, props, waterlogged):
    states = {"direction": I(CARDINAL.get(props.get("facing", "south"), 0)),
              "open_bit": B(props.get("open", "false")),
              "upper_block_bit": B(props.get("half") == "upper"),
              "door_hinge_bit": B(props.get("hinge") == "right")}
    return Result(java_id, states, waterlogged=False)


def _trapdoor(java_id, props, waterlogged):
    states = {"direction": I(CARDINAL.get(props.get("facing", "south"), 0)),
              "open_bit": B(props.get("open", "false")),
              "upside_down_bit": B(props.get("half") == "top")}
    return Result(java_id, states, waterlogged=waterlogged)


def _fence_gate(java_id, props, waterlogged):
    states = {"direction": I(CARDINAL.get(props.get("facing", "south"), 0)),
              "open_bit": B(props.get("open", "false")),
              "in_wall_bit": B(props.get("in_wall", "false"))}
    return Result(java_id, states, waterlogged=waterlogged)


_LEAVES1 = {"oak", "spruce", "birch", "jungle"}
_LEAVES2 = {"acacia", "dark_oak"}


def _leaves(java_id, props, waterlogged):
    wood = java_id.split(":")[1][:-len("_leaves")]
    states = {"persistent_bit": B(props.get("persistent", "false"))}
    if wood in _LEAVES1:
        states["old_leaf_type"] = S(wood)
        return Result("minecraft:leaves", states, waterlogged=waterlogged,
                      status="renamed", note="distance 状态已忽略")
    if wood in _LEAVES2:
        states["new_leaf_type"] = S(wood)
        return Result("minecraft:leaves2", states, waterlogged=waterlogged,
                      status="renamed", note="distance 状态已忽略")
    # azalea/cherry/mangrove/flowering_azalea 等：基岩版同名独立 id
    return Result(java_id, states, waterlogged=waterlogged,
                  note="distance 状态已忽略")


def _sapling(java_id, props, waterlogged):
    wood = java_id.split(":")[1][:-len("_sapling")]
    if wood in WOODS:
        return Result("minecraft:sapling", {"sapling_type": S(wood)},
                      waterlogged=waterlogged, status="renamed",
                      note="stage 状态已忽略")
    return None  # bamboo_sapling / cherry 等走兜底


_WALL_TYPES = {
    "cobblestone_wall": "cobblestone",
    "mossy_cobblestone_wall": "mossy_cobblestone",
    "brick_wall": "brick",
    "stone_brick_wall": "stone_brick",
    "mossy_stone_brick_wall": "mossy_stone_brick",
    "granite_wall": "granite",
    "diorite_wall": "diorite",
    "andesite_wall": "andesite",
    "sandstone_wall": "sandstone",
    "red_sandstone_wall": "red_sandstone",
    "prismarine_wall": "prismarine",
    "nether_brick_wall": "nether_brick",
    "red_nether_brick_wall": "red_nether_brick",
    "end_stone_brick_wall": "end_brick",
}


def _wall(java_id, props, waterlogged):
    short = java_id.split(":")[1]
    if short in _WALL_TYPES:
        return Result("minecraft:cobblestone_wall",
                      {"wall_block_type": S(_WALL_TYPES[short])},
                      waterlogged=waterlogged, status="renamed",
                      note="墙的连接状态由基岩版自动计算")
    # 现代独立 id 的墙（blackstone_wall 等）：直通，丢连接状态
    return Result(java_id, {}, waterlogged=waterlogged,
                  note="墙的连接状态由基岩版自动计算")


def _standing_sign(java_id, props, waterlogged):
    states = {}
    if "rotation" in props:
        states["ground_sign_direction"] = I(int(props["rotation"]))
    return Result("minecraft:standing_sign", states, waterlogged=False,
                  status="renamed")


def _wall_sign(java_id, props, waterlogged):
    states = {}
    f = props.get("facing")
    if f in FACING_DIR6:
        states["facing_direction"] = I(FACING_DIR6[f])
    return Result("minecraft:wall_sign", states, waterlogged=False, status="renamed")


def _standing_banner(java_id, props, waterlogged):
    states = {}
    if "rotation" in props:
        states["ground_sign_direction"] = I(int(props["rotation"]))
    return Result("minecraft:standing_banner", states, waterlogged=False,
                  status="approx", note="旗帜颜色保存在方块实体中，颜色信息丢失（默认白色）")


def _wall_banner(java_id, props, waterlogged):
    states = {}
    f = props.get("facing")
    if f in FACING_DIR6:
        states["facing_direction"] = I(FACING_DIR6[f])
    return Result("minecraft:wall_banner", states, waterlogged=False,
                  status="approx", note="旗帜颜色保存在方块实体中，颜色信息丢失（默认白色）")


_SKULL_IDS = {
    "skeleton_skull", "skeleton_wall_skull", "wither_skeleton_skull",
    "wither_skeleton_wall_skull", "zombie_head", "zombie_wall_head",
    "player_head", "player_wall_head", "creeper_head", "creeper_wall_head",
    "dragon_head", "dragon_wall_head", "piglin_head", "piglin_wall_head",
}


def _skull(java_id, props, waterlogged, wall):
    short = java_id.split(":")[1]
    states = {}
    if wall:
        base = short.replace("_wall_", "_")
        f = props.get("facing")
        states["facing_direction"] = I(FACING_DIR6.get(f, 2))
        return Result(f"minecraft:{base}", states, waterlogged=False,
                      status="renamed")
    # 地面头颅：基岩版无旋转状态
    states["facing_direction"] = I(1)
    note = "基岩版地面头颅不支持 16 向旋转，rotation 已忽略"
    if short == "player_head":
        note += "；自定义头颅皮肤数据丢失"
    return Result(java_id, states, waterlogged=False, status="approx", note=note)


_CORAL_COLORS = {"tube": "blue", "brain": "pink", "bubble": "purple",
                 "fire": "red", "horn": "yellow"}


def _coral_color_of(short):
    for k, v in _CORAL_COLORS.items():
        if k in short:
            return v
    return "blue"


def _coral_block(java_id, props, waterlogged):
    short = java_id.split(":")[1]
    dead = short.startswith("dead_")
    return Result("minecraft:coral_block",
                  {"coral_color": S(_coral_color_of(short)), "dead_bit": B(dead)},
                  waterlogged=waterlogged, status="renamed")


def _coral(java_id, props, waterlogged):
    short = java_id.split(":")[1]
    if short.startswith("dead_"):
        return Result("minecraft:coral",
                      {"coral_color": S(_coral_color_of(short)), "dead_bit": B(1)},
                      waterlogged=waterlogged, status="approx",
                      note="基岩版无枯萎珊瑚丛，以同色珊瑚近似")
    return Result("minecraft:coral", {"coral_color": S(_coral_color_of(short))},
                  waterlogged=waterlogged, status="renamed")


def _coral_fan(java_id, props, waterlogged):
    short = java_id.split(":")[1]
    dead = short.startswith("dead_")
    bid = "minecraft:coral_fan_dead" if dead else "minecraft:coral_fan"
    return Result(bid, {"coral_color": S(_coral_color_of(short)),
                        "coral_fan_direction": I(0)},
                  waterlogged=waterlogged, status="renamed" if not dead else "approx")


def _coral_wall_fan(java_id, props, waterlogged):
    short = java_id.split(":")[1]
    dead = short.startswith("dead_")
    bid = "minecraft:coral_wall_fan_dead" if dead else "minecraft:coral_wall_fan"
    f = props.get("facing", "south")
    return Result(bid, {"coral_color": S(_coral_color_of(short)),
                        "coral_direction": I(CARDINAL.get(f, 0))},
                  waterlogged=waterlogged, status="renamed" if not dead else "approx")


def _wall_torch(java_id, props, waterlogged):
    short = java_id.split(":")[1]
    base = "soul_torch" if short.startswith("soul") else \
        "redstone_torch" if short.startswith("redstone") else "torch"
    f = props.get("facing", "north")
    states = {"torch_facing_direction": S(TORCH_OPPOSITE.get(f, "top"))}
    if base == "redstone_torch" and props.get("lit") == "false":
        return Result("minecraft:unlit_redstone_torch", states,
                      waterlogged=False, status="renamed")
    return Result(f"minecraft:{base}", states, waterlogged=False)


def _floor_torch(java_id, props, waterlogged):
    short = java_id.split(":")[1]
    if short.startswith("soul"):
        return Result("minecraft:soul_torch", waterlogged=False)
    if short.startswith("redstone"):
        bid = "minecraft:redstone_torch" if props.get("lit", "true") == "true" \
            else "minecraft:unlit_redstone_torch"
        return Result(bid, {"torch_facing_direction": S("top")}, waterlogged=False,
                      status="renamed" if "unlit" in bid else "ok")
    return Result("minecraft:torch", {"torch_facing_direction": S("top")},
                  waterlogged=False)


def _button(java_id, props, waterlogged):
    face = props.get("face", "wall")
    f = props.get("facing", "north")
    if face == "floor":
        fd = 1
    elif face == "ceiling":
        fd = 0
    else:
        fd = FACING_DIR6.get(f, 2)
    return Result(java_id, {"facing_direction": I(fd),
                            "button_pressed_bit": B(props.get("powered", "false"))},
                  waterlogged=False)


_RAIL_SHAPES = {
    "north_south": 0, "east_west": 1, "ascending_east": 2, "ascending_west": 3,
    "ascending_north": 4, "ascending_south": 5, "south_east": 6, "south_west": 7,
    "north_west": 8, "north_east": 9,
}


def _rail(java_id, props, waterlogged):
    states = {"rail_direction": I(_RAIL_SHAPES.get(props.get("shape", "north_south"), 0))}
    if "powered" in props and java_id != "minecraft:rail":
        states["rail_data_bit"] = B(props.get("powered", "false"))
    return Result(java_id, states, waterlogged=waterlogged)


# ---------------------------------------------------------------- 精确匹配表

def _facing6(props, default=2):
    return I(FACING_DIR6.get(props.get("facing", "north"), default))


def _cardinal(props):
    return I(CARDINAL.get(props.get("facing", "south"), 0))


def _growth(props, key="age"):
    return I(int(props.get(key, "0")))


def _exact(java_id, props, waterlogged):
    s = java_id.split(":")[1]

    # ---- 熔炉 / 箱子 / 容器（facing → facing_direction）----
    if s in ("furnace", "smoker", "blast_furnace", "chest", "trapped_chest",
             "ender_chest", "dispenser", "dropper", "observer", "barrel",
             "hopper", "stonecutter"):
        states = {"facing_direction": _facing6(props)}
        if s == "barrel":
            states["open_bit"] = B(props.get("open", "false"))
        note = None
        if s in ("chest", "trapped_chest"):
            note = "箱子 type(左/右半) 状态由基岩版自动配对"
        return Result(java_id, states, waterlogged=waterlogged, note=note)

    if s in ("piston", "sticky_piston"):
        return Result(java_id, {"facing_direction": _facing6(props)},
                      waterlogged=False)
    if s == "end_rod" or s == "lightning_rod":
        return Result(java_id, {"facing_direction": _facing6(props)},
                      waterlogged=False)
    if s == "shulker_box":
        return Result("minecraft:undyed_shulker_box",
                      {"facing_direction": _facing6(props)}, waterlogged=False,
                      status="renamed")

    # ---- 南瓜类（facing → direction 南0）----
    if s in ("carved_pumpkin", "jack_o_lantern", "beehive", "bee_nest",
             "end_portal_frame"):
        states = {"direction": _cardinal(props)}
        bid = java_id
        if s == "jack_o_lantern":
            bid = "minecraft:lit_pumpkin"
        if s == "end_portal_frame":
            states["end_portal_eye_bit"] = B(props.get("eye", "false"))
        note = "honey_level 状态已忽略" if s in ("beehive", "bee_nest") else None
        return Result(bid, states, waterlogged=False,
                      status="renamed" if bid != java_id else "ok", note=note)

    # ---- 红石元件 ----
    if s == "redstone_wire":
        return Result(java_id, {"redstone_signal": I(int(props.get("power", "0")))},
                      waterlogged=False, note="红石线连接状态由基岩版自动计算")
    if s == "repeater":
        return Result(java_id, {"direction": _cardinal(props),
                                "repeater_delay": I(int(props.get("delay", "1")) - 1)},
                      waterlogged=False)
    if s == "comparator":
        return Result(java_id, {"direction": _cardinal(props),
                                "output_lit_bit": B(props.get("powered", "false")),
                                "output_subtract_bit": B(props.get("mode") == "subtract")},
                      waterlogged=False)
    if s == "lever":
        face = props.get("face", "wall")
        f = props.get("facing", "north")
        if face == "wall":
            ld = f
        elif face == "floor":
            ld = "up_north_south" if f in ("north", "south") else "up_east_west"
        else:
            ld = "down_north_south" if f in ("north", "south") else "down_east_west"
        return Result(java_id, {"lever_direction": S(ld),
                                "open_bit": B(props.get("powered", "false"))},
                      waterlogged=False)
    if s == "observer":
        return Result(java_id, {"facing_direction": _facing6(props),
                                "powered_bit": B(props.get("powered", "false"))},
                      waterlogged=False)
    if s in ("daylight_detector", "daylight_detector_inverted"):
        bid = "minecraft:daylight_detector_inverted" if props.get("inverted") == "true" \
            else "minecraft:daylight_detector"
        return Result(bid, {"redstone_signal": I(int(props.get("power", "0")))},
                      waterlogged=False)
    if s == "tripwire":
        return Result("minecraft:trip_wire",
                      {"attached_bit": B(props.get("attached", "false")),
                       "powered_bit": B(props.get("powered", "false"))},
                      waterlogged=False, status="renamed",
                      note="绊线连接状态由基岩版自动计算")
    if s == "tripwire_hook":
        return Result(java_id, {"direction": _cardinal(props),
                                "attached_bit": B(props.get("attached", "false")),
                                "powered_bit": B(props.get("powered", "false"))},
                      waterlogged=False)

    # ---- 方块实体类方块 ----
    if s == "furnace_lit" or (s == "furnace"):
        pass  # 已在上面的分支处理
    if s in ("smoker", "blast_furnace"):
        pass
    if s == "campfire" or s == "soul_campfire":
        return Result(java_id, {"direction": _cardinal(props),
                                "extinguished": B(props.get("lit", "true") == "false")},
                      waterlogged=waterlogged)
    if s == "bell":
        attach = {"floor": "standing", "ceiling": "hanging",
                  "single_wall": "side", "double_wall": "multiple"}
        return Result(java_id, {"direction": _cardinal(props),
                                "attachment": S(attach.get(props.get("attachment", "floor"), "standing"))},
                      waterlogged=False)
    if s in ("lantern", "soul_lantern"):
        return Result(java_id, {"hanging_bit": B(props.get("hanging", "false"))},
                      waterlogged=False)
    if s == "ladder":
        return Result(java_id, {"facing_direction": _facing6(props)},
                      waterlogged=waterlogged)

    # ---- 方块状态数值类 ----
    if s == "water" or s == "lava":
        return Result(java_id, {"liquid_depth": I(int(props.get("level", "0")))},
                      waterlogged=False)
    if s == "snow":
        layers = int(props.get("layers", "1"))
        if layers >= 8:
            return Result("minecraft:snow_block", waterlogged=False, status="renamed")
        return Result("minecraft:snow_layer", {"height": I(layers - 1),
                                               "covered_bit": B(0)},
                      waterlogged=False, status="renamed")
    if s == "farmland":
        return Result(java_id, {"moisturized_amount": I(int(props.get("moisture", "0")))},
                      waterlogged=False)
    if s == "composter":
        return Result(java_id, {"composter_fill_level": I(int(props.get("level", "0")))},
                      waterlogged=False)
    if s == "light":
        return Result("minecraft:light_block",
                      {"block_light_level": I(int(props.get("level", "15")))},
                      waterlogged=waterlogged, status="renamed")
    if s == "respawn_anchor":
        return Result(java_id, {"respawn_anchor_charge": I(int(props.get("charges", "0")))},
                      waterlogged=False)
    if s == "cake":
        return Result(java_id, {"bite_counter": I(int(props.get("bites", "0")))},
                      waterlogged=False)
    if s == "candle":
        return Result(java_id, {"candles": I(int(props.get("candles", "1")) - 1),
                                "lit": B(props.get("lit", "false"))},
                      waterlogged=waterlogged)
    if s == "sea_pickle":
        return Result(java_id, {"cluster_count": I(int(props.get("pickles", "1")) - 1),
                                "dead_bit": B(props.get("waterlogged", "false") == "false")},
                      waterlogged=False)
    if s == "turtle_egg":
        count = {"1": "one", "2": "two", "3": "three", "4": "four"}
        cracked = {"0": "no_cracks", "1": "cracked", "2": "max_cracked"}
        return Result(java_id,
                      {"turtle_egg_count": S(count.get(props.get("eggs", "1"), "one")),
                       "cracked_state": S(cracked.get(props.get("hatch", "0"), "no_cracks"))},
                      waterlogged=False)
    if s == "candle_cake":
        return Result(java_id, {"lit": B(props.get("lit", "false"))}, waterlogged=False)

    # ---- 作物 ----
    if s in ("wheat", "carrots", "potatoes", "torchflower_crop"):
        bid = "minecraft:wheat" if s == "torchflower_crop" else java_id
        return Result(bid, {"growth": _growth(props)}, waterlogged=False)
    if s == "beetroots":
        return Result("minecraft:beetroot", {"growth": _growth(props)},
                      waterlogged=False, status="renamed")
    if s in ("pumpkin_stem", "melon_stem", "sweet_berry_bush"):
        return Result(java_id, {"growth": _growth(props)}, waterlogged=False)
    if s == "nether_wart":
        return Result(java_id, {"age": _growth(props)}, waterlogged=False)
    if s == "cocoa":
        return Result(java_id, {"age": _growth(props),
                                "direction": _cardinal(props)}, waterlogged=False)
    if s == "chorus_flower":
        return Result(java_id, {"age": _growth(props)}, waterlogged=False)
    if s == "fire":
        return Result(java_id, {"age": _growth(props)}, waterlogged=False)

    # ---- 泥土/石头大家族 ----
    if s == "coarse_dirt":
        return Result("minecraft:dirt", {"dirt_type": S("coarse")},
                      waterlogged=False, status="renamed")
    if s == "red_sand":
        return Result("minecraft:sand", {"sand_type": S("red")},
                      waterlogged=False, status="renamed")
    if s in ("granite", "diorite", "andesite", "polished_granite",
             "polished_diorite", "polished_andesite"):
        return Result("minecraft:stone", {"stone_type": S(s)},
                      waterlogged=False, status="renamed")
    if s in ("stone_bricks", "mossy_stone_bricks", "cracked_stone_bricks",
             "chiseled_stone_bricks"):
        t = {"stone_bricks": "default", "mossy_stone_bricks": "mossy",
             "cracked_stone_bricks": "cracked",
             "chiseled_stone_bricks": "chiseled"}[s]
        return Result("minecraft:stonebrick", {"stone_brick_type": S(t)},
                      waterlogged=False, status="renamed")
    if s in ("sandstone", "cut_sandstone", "chiseled_sandstone", "smooth_sandstone",
             "red_sandstone", "cut_red_sandstone", "chiseled_red_sandstone",
             "smooth_red_sandstone"):
        red = s.startswith("red_") or s.startswith("cut_red") or \
            s.startswith("chiseled_red") or s.startswith("smooth_red")
        t = "default"
        if "cut" in s:
            t = "cut"
        elif "chiseled" in s:
            t = "chiseled"
        elif "smooth" in s:
            t = "smooth"
        return Result("minecraft:red_sandstone" if red else "minecraft:sandstone",
                      {"sand_stone_type": S(t)}, waterlogged=False, status="renamed")
    if s == "quartz_block":
        return Result(java_id, {"chisel_type": S("default")}, waterlogged=False)
    if s == "chiseled_quartz_block":
        return Result("minecraft:quartz_block", {"chisel_type": S("chiseled")},
                      waterlogged=False, status="renamed")
    if s == "quartz_pillar":
        return Result("minecraft:quartz_block",
                      {"chisel_type": S("lines"),
                       "pillar_axis": S(props.get("axis", "y"))},
                      waterlogged=False, status="renamed")
    if s == "smooth_quartz":
        return Result("minecraft:quartz_block", {"chisel_type": S("smooth")},
                      waterlogged=False, status="renamed")
    if s in ("prismarine", "prismarine_bricks", "dark_prismarine"):
        t = {"prismarine": "default", "prismarine_bricks": "bricks",
             "dark_prismarine": "dark"}[s]
        return Result("minecraft:prismarine", {"prismarine_block_type": S(t)},
                      waterlogged=False, status="renamed")
    if s == "purpur_pillar":
        return Result("minecraft:purpur_block",
                      {"chisel_type": S("lines"),
                       "pillar_axis": S(props.get("axis", "y"))},
                      waterlogged=False, status="renamed")
    if s == "wet_sponge":
        return Result("minecraft:sponge", {"sponge_type": S("wet")},
                      waterlogged=False, status="renamed")
    if s.startswith("infested_") and s != "infested_deepslate":
        t = s[len("infested_"):]
        tmap = {"stone": "stone", "cobblestone": "cobblestone",
                "stone_bricks": "stone_brick", "mossy_stone_bricks": "mossy_stone_brick",
                "cracked_stone_bricks": "cracked_stone_brick",
                "chiseled_stone_bricks": "chiseled_stone_brick"}
        return Result("minecraft:monster_egg",
                      {"monster_egg_stone_type": S(tmap.get(t, "stone"))},
                      waterlogged=False, status="renamed")

    # ---- 蘑菇方块 ----
    if s in ("brown_mushroom_block", "red_mushroom_block", "mushroom_stem"):
        faces = [props.get(d, "false") == "true" for d in
                 ("north", "south", "east", "west", "up", "down")]
        bits = 15 if all(faces) else 14
        return Result(java_id, {"huge_mushroom_bits": I(bits)},
                      waterlogged=False, status="approx",
                      note="基岩版蘑菇方块按位掩码存储表面，已取最近似值")

    # ---- 植物 ----
    if s == "grass":  # Java 1.20.3+ 短草
        return Result("minecraft:tallgrass", {"tall_grass_type": S("tall")},
                      waterlogged=False, status="renamed")
    if s == "fern":
        return Result("minecraft:tallgrass", {"tall_grass_type": S("fern")},
                      waterlogged=False, status="renamed")
    if s in ("tall_grass", "large_fern", "sunflower", "lilac", "rose_bush", "peony",
             "pitcher_plant"):
        tmap = {"tall_grass": "grass", "large_fern": "fern", "sunflower": "sunflower",
                "lilac": "syringa", "rose_bush": "rose", "peony": "paeonia"}
        if s == "pitcher_plant":
            return Result(java_id, {"upper_block_bit": B(props.get("half") == "upper")},
                          waterlogged=False)
        return Result("minecraft:double_plant",
                      {"double_plant_type": S(tmap[s]),
                       "upper_block_bit": B(props.get("half") == "upper")},
                      waterlogged=False, status="renamed")
    if s == "vine":
        bits = 0
        for d, b in (("south", 1), ("west", 2), ("north", 4), ("east", 8)):
            if props.get(d) == "true":
                bits |= b
        return Result(java_id, {"vine_direction_bits": I(bits)},
                      waterlogged=False, note="藤蔓 up 状态已并入位掩码")
    if s == "seagrass":
        return Result(java_id, {"sea_grass_type": S("default")}, waterlogged=False)
    if s == "tall_seagrass":
        t = "double_top" if props.get("half") == "upper" else "double_bot"
        return Result("minecraft:seagrass", {"sea_grass_type": S(t)},
                      waterlogged=False, status="renamed")
    if s == "bamboo":
        return Result(java_id, {"bamboo_stalk_thickness": S("thin"),
                                "bamboo_leaf_size": S("no_leaves"),
                                "age_bit": B(props.get("age") == "1")},
                      waterlogged=False, status="approx",
                      note="竹子形态状态为近似转换")
    if s == "pink_petals":
        return Result(java_id,
                      {"growth": I(min((int(props.get("flower_amount", "1")) - 1) * 2, 7)),
                       "direction": _cardinal(props)},
                      waterlogged=False, status="approx",
                      note="花瓣数量状态为近似转换")

    # ---- 紫水晶 ----
    if s in ("amethyst_cluster", "large_amethyst_bud", "medium_amethyst_bud",
             "small_amethyst_bud"):
        return Result(java_id,
                      {"minecraft:block_face": S(props.get("facing", "up"))},
                      waterlogged=waterlogged)
    if s == "pointed_dripstone":
        return Result(java_id,
                      {"hanging_bit": B(props.get("vertical_direction") == "down")},
                      waterlogged=waterlogged, status="approx",
                      note="滴水石锥 thickness 状态已忽略")

    # ---- 炼药锅 ----
    if s == "cauldron":
        return Result(java_id, {"fill_level": I(0), "cauldron_liquid": S("water")},
                      waterlogged=False)
    if s == "water_cauldron":
        return Result("minecraft:cauldron",
                      {"fill_level": I(int(props.get("level", "1")) * 2),
                       "cauldron_liquid": S("water")},
                      waterlogged=False, status="renamed")
    if s == "lava_cauldron":
        return Result("minecraft:cauldron",
                      {"fill_level": I(6), "cauldron_liquid": S("lava")},
                      waterlogged=False, status="renamed")
    if s == "powder_snow_cauldron":
        return Result("minecraft:cauldron",
                      {"fill_level": I(int(props.get("level", "1")) * 2),
                       "cauldron_liquid": S("powder_snow")},
                      waterlogged=False, status="renamed")

    # ---- 深板岩/幽匿 ----
    if s == "sculk_sensor" or s == "calibrated_sculk_sensor":
        phase = {"inactive": 0, "active": 1, "cooldown": 2}
        return Result(java_id,
                      {"sculk_sensor_phase": I(phase.get(props.get("sculk_sensor_phase", "inactive"), 0))},
                      waterlogged=waterlogged)
    if s == "sculk_shrieker":
        return Result(java_id, {"active": B(props.get("shrieking", "false"))},
                      waterlogged=waterlogged,
                      note="can_summon 状态已忽略")
    if s == "sculk_catalyst":
        return Result(java_id, {"bloom": B(props.get("bloom", "false"))},
                      waterlogged=waterlogged)

    # ---- 花盆 ----
    if s == "flower_pot" or s.startswith("potted_"):
        note = None if s == "flower_pot" else "盆栽内容保存在方块实体中，植物信息丢失（保留空花盆）"
        return Result("minecraft:flower_pot", waterlogged=False,
                      status="ok" if s == "flower_pot" else "approx", note=note)

    # ---- 画框 ----
    if s == "item_frame" or s == "glow_item_frame":
        bid = "minecraft:frame" if s == "item_frame" else "minecraft:glow_frame"
        return Result(bid, {"facing_direction": _facing6(props)},
                      waterlogged=False, status="renamed",
                      note="展示框内物品数据已忽略")

    # ---- 命令方块 ----
    if s in ("command_block", "chain_command_block", "repeating_command_block"):
        return Result(java_id, {"conditional_bit": B(props.get("conditional", "false")),
                                "facing_direction": _facing6(props)},
                      waterlogged=False)

    # ---- 传送门 ----
    if s == "nether_portal":
        return Result("minecraft:portal", {"portal_axis": S(props.get("axis", "x"))},
                      waterlogged=False, status="renamed")

    # ---- 铁砧 ----
    if s in ("anvil", "chipped_anvil", "damaged_anvil"):
        dmg = {"anvil": "undamaged", "chipped_anvil": "slightly_damaged",
               "damaged_anvil": "very_damaged"}[s]
        return Result("minecraft:anvil", {"damage": S(dmg), "direction": _cardinal(props)},
                      waterlogged=False, status="renamed" if s != "anvil" else "ok")

    # ---- 海晶灯/其他直通过滤 ----
    if s == "big_dripleaf" or s == "small_dripleaf":
        states = {"direction": _cardinal(props)}
        if s == "small_dripleaf":
            states["upper_block_bit"] = B(props.get("half") == "upper")
        return Result(java_id, states, waterlogged=waterlogged,
                      note="垂滴叶 tilt 状态已忽略")

    return None


# ---------------------------------------------------------------- 通用状态翻译

def _generic_states(props):
    """兜底方块的状态翻译：只处理高置信度状态，其余丢弃"""
    states = {}
    axis = props.get("axis")
    if axis in ("x", "y", "z"):
        states["pillar_axis"] = S(axis)
    facing = props.get("facing")
    if facing in FACING_DIR6 and facing in ("up", "down"):
        states["facing_direction"] = I(FACING_DIR6[facing])
    return states
