# Agent

Pi 在这个目录启动，读取 `AGENTS.md` 作为行为约束。

## 注册 MCP

在安装好 `backend/.venv` 依赖后，直接运行配置脚本即可自动写入本机路径并注册到 Pi（支持 Windows / Linux / macOS）：

```powershell
.\backend\.venv\Scripts\python.exe agent\setup_mcp.py
```

Linux / macOS：

```bash
./backend/.venv/bin/python agent/setup_mcp.py
```

也可以手动确认注册结果：

```powershell
pi mcp list
```

## 手动测试

```powershell
cd agent
pi --mode rpc --no-session
```

然后输入一行：

```json
{"id":"1","type":"prompt","message":"用 schematic-architect 生成一个 8x8 的小屋，导出到 C:/tmp/test.litematic"}
```
