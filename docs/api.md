# API

## POST /v1/build

```json
{
  "player_id": "Steve",
  "prompt": "一座中世纪小酒馆",
  "palette": "oak",
  "mode": "3d",
  "context": {
    "size": [8, 4, 8],
    "palette": [{"id": "minecraft:grass_block", "states": {}}],
    "blocks": "base64 int16 little-endian, -1 = skip"
  }
}
```

返回 `202`：

```json
{"task_id": "a1b2c3d4e5f6", "status": "queued"}
```

## GET /v1/build/{task_id}

```json
{
  "task_id": "a1b2c3d4e5f6",
  "player_id": "Steve",
  "status": "done",
  "result": {
    "size": [16, 12, 16],
    "palette": [{"id": "minecraft:oak_planks", "states": {}}],
    "blocks": "base64 int16"
  }
}
```

状态：`queued` / `generating` / `converting` / `done` / `failed`。

失败时带 `error` 字段。

## 体素数组约定

- 索引顺序：`y * sx * sz + z * sx + x`
- `Int16` 小端
- `-1` 表示跳过（空气或 structure_void）
- `palette[i]` 是方块 `{id, states}`