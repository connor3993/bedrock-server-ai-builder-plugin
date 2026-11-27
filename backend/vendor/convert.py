# -*- coding: utf-8 -*-
"""
convert.py - .litematic → .mcstructure 批量转换器（命令行入口）

用法:
  python convert.py <输入文件或目录> -o <输出目录> [--report 报告.json]

示例:
  python convert.py build.litematic -o out/
  python convert.py schematics/ -o out/ --report out/report.json
"""
import argparse
import json
import os
import sys
import traceback

from litematic import load as load_litematic, LitematicError, parse_blockstate
from mapping import translate
from mcstructure import McStructureBuilder, convert_block_entity


class ConversionError(Exception):
    """单文件转换失败（面向用户的友好提示）"""


def convert_file(input_path, output_path):
    """转换单个文件，返回该文件的报告 dict"""
    try:
        meta, regions = load_litematic(input_path)
    except LitematicError as e:
        raise ConversionError(str(e))
    except Exception as e:
        raise ConversionError(f"读取文件时发生未知错误: {e}")

    # ---- 计算所有区域的包围盒 ----
    min_x = min(r.min_corner[0] for r, _ in regions)
    min_y = min(r.min_corner[1] for r, _ in regions)
    min_z = min(r.min_corner[2] for r, _ in regions)
    max_x = max(r.min_corner[0] + r.size[0] for r, _ in regions)
    max_y = max(r.min_corner[1] + r.size[1] for r, _ in regions)
    max_z = max(r.min_corner[2] + r.size[2] for r, _ in regions)
    size = (max_x - min_x, max_y - min_y, max_z - min_z)
    if 0 in size:
        raise ConversionError("投影中存在尺寸为 0 的区域，无法生成结构")

    if size[0] * size[1] * size[2] > 100_000_000:
        raise ConversionError(f"结构过大（{size[0]}×{size[1]}×{size[2]}），超出安全限制")

    builder = McStructureBuilder(size)
    water_idx = builder.palette_id("minecraft:water", {})

    # ---- 翻译缓存 ----
    trans_cache = {}          # java_state_str -> Result
    replaced = {}             # java_state_str -> {bedrock, count, note}
    approx = {}               # java_state_str -> {bedrock, count, note}
    renamed = 0
    total_blocks = 0

    def translate_state(state_str):
        if state_str in trans_cache:
            return trans_cache[state_str]
        name, props = parse_blockstate(state_str)
        res = translate(name, props)
        trans_cache[state_str] = res
        return res

    pal_id_cache = {}  # java_state_str -> (layer0_idx, waterlogged)

    # ---- 逐区域填充 ----
    entity_count = 0
    te_notes = []
    for region, palette in regions:
        entity_count += region.entity_count
        ox = region.min_corner[0] - min_x
        oy = region.min_corner[1] - min_y
        oz = region.min_corner[2] - min_z
        sx, sy, sz = region.size

        # 预翻译本区域调色板
        local_pal = []
        for state_str in palette:
            res = translate_state(state_str)
            states_key = res.states
            idx = builder.palette_id(res.name, states_key)
            local_pal.append((res, idx))

        for y in range(sy):
            for z in range(sz):
                for x in range(sx):
                    res, pal_idx = local_pal[region.blocks[y][z][x]]
                    if res.name == "minecraft:structure_void":
                        builder.set_block(ox + x, oy + y, oz + z, -1, layer=0)
                        continue
                    builder.set_block(ox + x, oy + y, oz + z, pal_idx, layer=0)
                    if res.waterlogged:
                        builder.set_block(ox + x, oy + y, oz + z, water_idx, layer=1)

        # 统计
        for state_str in palette:
            if state_str == "minecraft:air":
                continue
            if trans_cache[state_str].name == "minecraft:structure_void":
                continue
            res = trans_cache[state_str]
            count = sum(
                1
                for yy in range(sy) for zz in range(sz) for xx in range(sx)
                if palette[region.blocks[yy][zz][xx]] == state_str
            )
            total_blocks += count
            if res.status == "replaced":
                d = replaced.setdefault(state_str, {"bedrock": res.name, "count": 0,
                                                    "note": res.note or ""})
                d["count"] += count
            elif res.status == "approx":
                d = approx.setdefault(state_str, {"bedrock": res.name, "count": 0,
                                                  "note": res.note or ""})
                d["count"] += count
            elif res.status == "renamed":
                renamed += count

        # 方块实体
        for te in region.tile_entities:
            try:
                tx = ox + te["x"].value
                ty = oy + te["y"].value
                tz = oz + te["z"].value
            except (KeyError, AttributeError):
                te_notes.append("跳过了一个坐标异常的方块实体")
                continue
            be_data, note = convert_block_entity(te, tx, ty, tz)
            if be_data is not None:
                builder.set_block_entity(tx, ty, tz, be_data)
            if note:
                te_notes.append(note)

    # ---- 写出 ----
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    builder.save(output_path)

    notes = []
    if entity_count:
        notes.append(f"投影中的 {entity_count} 个实体（生物/展示框等）未转换（.mcstructure 实体格式不同）")
    notes.extend(sorted(set(te_notes)))

    return {
        "input": input_path,
        "output": output_path,
        "size": list(size),
        "regions": len(regions),
        "total_blocks": total_blocks,
        "renamed_blocks": renamed,
        "replaced": replaced,
        "approximated": approx,
        "notes": notes,
    }


# ---------------------------------------------------------------- CLI

def collect_inputs(input_path):
    if os.path.isfile(input_path):
        if not input_path.lower().endswith(".litematic"):
            raise ConversionError(
                f"输入文件 '{input_path}' 不是 .litematic 文件")
        return [input_path]
    if os.path.isdir(input_path):
        found = []
        for root, _, files in os.walk(input_path):
            for f in sorted(files):
                if f.lower().endswith(".litematic"):
                    found.append(os.path.join(root, f))
        if not found:
            raise ConversionError(f"目录 '{input_path}' 中未找到任何 .litematic 文件")
        return found
    raise ConversionError(f"输入路径不存在: {input_path}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="convert.py",
        description="将 Litematica 投影 (.litematic) 批量转换为基岩版结构文件 (.mcstructure)")
    parser.add_argument("input", help="输入 .litematic 文件或包含投影的目录")
    parser.add_argument("-o", "--output", required=True,
                        help="输出目录（每个输入生成同名 .mcstructure）")
    parser.add_argument("--report", default=None,
                        help="转换报告 JSON 输出路径（默认: <输出目录>/conversion_report.json）")
    parser.add_argument("-q", "--quiet", action="store_true", help="只输出汇总信息")
    args = parser.parse_args(argv)

    try:
        inputs = collect_inputs(args.input)
    except ConversionError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 2

    os.makedirs(args.output, exist_ok=True)
    report_path = args.report or os.path.join(args.output, "conversion_report.json")

    results, failures = [], []
    for path in inputs:
        out_path = os.path.join(
            args.output, os.path.splitext(os.path.basename(path))[0] + ".mcstructure")
        try:
            r = convert_file(path, out_path)
            results.append(r)
            if not args.quiet:
                print(f"[成功] {os.path.basename(path)} → {os.path.basename(out_path)} "
                      f"({r['size'][0]}×{r['size'][1]}×{r['size'][2]}, "
                      f"{r['total_blocks']} 方块, 替换 {sum(d['count'] for d in r['replaced'].values())})")
        except ConversionError as e:
            failures.append({"input": path, "error": str(e)})
            print(f"[失败] {os.path.basename(path)}: {e}", file=sys.stderr)
        except Exception as e:
            failures.append({"input": path, "error": f"内部错误: {e}"})
            print(f"[失败] {os.path.basename(path)}: 内部错误: {e}", file=sys.stderr)
            if os.environ.get("LTM_DEBUG"):
                traceback.print_exc()

    total_replaced = sum(
        d["count"] for r in results for d in r["replaced"].values())
    summary = {
        "success": len(results),
        "failed": len(failures),
        "total_blocks": sum(r["total_blocks"] for r in results),
        "total_replaced_blocks": total_replaced,
        "files": results,
        "failures": failures,
    }

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n========== 转换报告 ==========")
    print(f"成功: {len(results)}  失败: {len(failures)}")
    print(f"方块总数: {summary['total_blocks']}  被替换方块数: {total_replaced}")
    for r in results:
        for java, d in r["replaced"].items():
            print(f"  替换: {java} → {d['bedrock']} ×{d['count']}（{d['note']}）")
    print(f"详细报告: {report_path}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
