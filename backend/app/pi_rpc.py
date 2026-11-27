"""Minimal Pi RPC client: start `pi --mode rpc`, send one prompt, wait for agent_end."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from pathlib import Path

from .config import PI_BIN, PI_IDLE_TIMEOUT, PI_WORKDIR

logger = logging.getLogger("ai_build.pi")


class PiError(Exception):
    pass


def _resolve_pi() -> str:
    found = shutil.which(PI_BIN)
    if found:
        return found
    candidate = Path(PI_BIN)
    if candidate.exists():
        return str(candidate)
    raise PiError(f"pi executable not found: {PI_BIN}")


async def run_agent(prompt: str, idle_timeout: int = PI_IDLE_TIMEOUT, on_event=None) -> dict:
    _resolve_pi()
    node = shutil.which("node")
    cli_js = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@earendil-works" / "pi-coding-agent" / "dist" / "bundle" / "cli.js"
    if node and cli_js.exists():
        logger.info("starting pi via node exec: %s", cli_js)
        proc = await asyncio.create_subprocess_exec(
            node, str(cli_js), "--mode", "rpc", "--no-session", "--approve",
            cwd=str(PI_WORKDIR),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
        )
    else:
        logger.info("starting pi via shell")
        proc = await asyncio.create_subprocess_shell(
            f'"{_resolve_pi()}" --mode rpc --no-session --approve',
            cwd=str(PI_WORKDIR),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
        )

    stderr_chunks: list[bytes] = []

    async def drain_stderr() -> None:
        assert proc.stderr is not None
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                break
            stderr_chunks.append(chunk)

    stderr_task = asyncio.create_task(drain_stderr())

    async def wait_for_end() -> dict:
        assert proc.stdout is not None
        while True:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=idle_timeout)
            except asyncio.TimeoutError as exc:
                raise PiError(f"pi 已连续 {idle_timeout} 秒没有任何事件输出，判定卡死") from exc
            if not line:
                raise PiError("pi exited before agent_end")
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue
            etype = event.get("type")
            if etype in ("agent_end", "agent_settled"):
                logger.info("pi finished: %s", etype)
                return event
            if on_event is not None:
                    try:
                        on_event(event)
                    except Exception:  # noqa: BLE001
                        logger.exception("on_event callback failed")

    try:
        assert proc.stdin is not None
        payload = {"id": "1", "type": "prompt", "message": prompt}
        proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        await proc.stdin.drain()
        return await wait_for_end()
    finally:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass
        await stderr_task
        if stderr_chunks and proc.returncode not in (0, None) and sys.exc_info()[0] is None:
            tail = b"".join(stderr_chunks)[-2000:].decode("utf-8", "replace")
            if tail.strip():
                raise PiError(f"pi failed ({proc.returncode}): {tail}")
