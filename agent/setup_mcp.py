"""Generate agent/mcp.json with absolute paths for the current machine and register it in Pi."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def resolve_python(repo_root: Path) -> Path:
    candidates = [
        repo_root / "backend" / ".venv" / "Scripts" / "python.exe",
        repo_root / "backend" / ".venv" / "bin" / "python",
    ]
    for path in candidates:
        if path.is_file():
            return path.resolve()
    return Path(sys.executable).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure schematic-architect MCP server for Pi.")
    parser.add_argument("--push-base", default=os.environ.get("AI_BUILD_PUSH_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--no-register", action="store_true", help="Only write agent/mcp.json without calling pi mcp add")
    args = parser.parse_args()

    agent_dir = Path(__file__).resolve().parent
    repo_root = agent_dir.parent
    python_bin = resolve_python(repo_root)
    schematic_src = (repo_root / "mcp" / "schematic-architect" / "src").resolve()

    config = {
        "mcpServers": {
            "schematic-architect": {
                "command": python_bin.as_posix(),
                "args": ["-m", "schematic_architect.server"],
                "env": {
                    "PYTHONPATH": schematic_src.as_posix(),
                    "AI_BUILD_PUSH_BASE": args.push_base,
                },
            }
        }
    }
    mcp_file = agent_dir / "mcp.json"
    mcp_file.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {mcp_file}")
    print(f"  python:     {python_bin.as_posix()}")
    print(f"  PYTHONPATH: {schematic_src.as_posix()}")

    if args.no_register:
        return 0

    pi_bin = shutil.which("pi")
    if not pi_bin:
        print("pi CLI not found in PATH; install Pi and run: pi mcp add --file mcp.json")
        return 0

    proc = subprocess.run([pi_bin, "mcp", "add", "--file", str(mcp_file)], cwd=str(agent_dir))
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
