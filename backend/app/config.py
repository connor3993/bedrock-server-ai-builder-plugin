import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


DATA_DIR = Path(_env("AI_BUILD_DATA_DIR", str(BASE_DIR / "data"))).resolve()
BUILD_DIR = DATA_DIR / "builds"
ARCHIVE_DIR = DATA_DIR / "archive"
TMP_DIR = DATA_DIR / "tmp"
DB_PATH = DATA_DIR / "tasks.db"

PI_BIN = _env("PI_BIN", "pi")
PI_WORKDIR = Path(_env("PI_WORKDIR", str(REPO_ROOT / "agent"))).resolve()
PI_IDLE_TIMEOUT = int(_env("PI_IDLE_TIMEOUT", "180"))

SCHEMATIC_SRC = Path(_env("SCHEMATIC_SRC", str(REPO_ROOT / "mcp" / "schematic-architect" / "src"))).resolve()
SCHEMATIC_PYTHON = Path(_env("SCHEMATIC_PYTHON", sys.executable)).resolve()
MCSTRUCTURE_CONVERTER = Path(_env("MCSTRUCTURE_CONVERTER", str(BASE_DIR / "vendor" / "convert.py"))).resolve()
MAX_SIZE = 32

for d in (DATA_DIR, BUILD_DIR, TMP_DIR, ARCHIVE_DIR):
    d.mkdir(parents=True, exist_ok=True)
