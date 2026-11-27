"""Offline export: build designs with no game running.

A design is a :class:`Design`; :func:`export_all` writes it as a ``.litematic``
and, when the ``litematic-to-mcstructure`` converter is available, as a
Bedrock ``.mcstructure`` next to it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import litematic, render
from .design import Design


def export_litematic(design: Design, path: str | Path, description: str = "") -> Path:
    """Write the design as a ``.litematic`` file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    litematic.write(path, design.name, design.resolved(), description=description or design.name)
    return path


def export_preview(design: Design, path: str | Path, max_px: int = 900) -> Path:
    """Write an isometric PNG preview of the design."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    render.iso(design.resolved(), max_px=max_px).save(path)
    return path


def to_mcstructure(
    litematic_path: str | Path,
    out_dir: str | Path,
    converter: str | Path | None = None,
) -> Path:
    """Convert a ``.litematic`` to Bedrock ``.mcstructure`` with the external converter.

    The converter defaults to ``$LITEMATIC_TO_MCSTRUCTURE``.
    """
    litematic_path = Path(litematic_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    converter = Path(converter or os.environ.get("LITEMATIC_TO_MCSTRUCTURE", ""))
    if not converter.is_file():
        raise FileNotFoundError(
            "litematic-to-mcstructure converter not found; pass --converter or set "
            f"LITEMATIC_TO_MCSTRUCTURE (got {converter!s})"
        )
    subprocess.run(
        [sys.executable, str(converter), str(litematic_path), "-o", str(out_dir)],
        check=True,
    )
    return out_dir / f"{litematic_path.stem}.mcstructure"


def export_all(
    design: Design,
    out_dir: str | Path,
    *,
    converter: str | Path | None = None,
    preview: bool = True,
    description: str = "",
) -> dict[str, Path]:
    """Write ``.litematic``, ``.mcstructure`` and (optionally) a preview PNG."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {"litematic": export_litematic(design, out_dir / f"{design.name}.litematic", description)}
    if preview:
        result["preview"] = export_preview(design, out_dir / f"{design.name}_preview.png")
    result["mcstructure"] = to_mcstructure(result["litematic"], out_dir, converter)
    return result


def _load_design(script: str | Path) -> Design:
    """Load a design from a Python file defining ``build() -> Design``."""
    script = Path(script).resolve()
    namespace: dict = {"__name__": "design_script", "__file__": str(script)}
    code = compile(script.read_text(encoding="utf-8"), str(script), "exec")
    exec(code, namespace)  # noqa: S102 - the caller supplies the script
    build = namespace.get("build")
    if not callable(build):
        raise SystemExit(f"{script} must define build() -> Design")
    design = build()
    if not isinstance(design, Design):
        raise SystemExit(f"{script}: build() must return a Design, got {type(design).__name__}")
    return design


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m schematic_architect.offline --script design.py --out out``."""
    import argparse

    parser = argparse.ArgumentParser(prog="schematic_architect.offline", description=__doc__)
    parser.add_argument("--script", required=True, help="Python file defining build() -> Design")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--converter", default=None, help="Path to litematic-to-mcstructure/convert.py")
    parser.add_argument("--no-preview", action="store_true", help="Skip the PNG preview")
    parser.add_argument("--description", default="", help="Schematic description")
    args = parser.parse_args(argv)

    design = _load_design(args.script)
    paths = export_all(
        design,
        args.out,
        converter=args.converter,
        preview=not args.no_preview,
        description=args.description,
    )
    for kind, path in paths.items():
        print(f"{kind}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
