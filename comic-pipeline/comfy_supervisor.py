"""Recover a wedged ComfyUI process by restarting it.

ComfyUI's low-VRAM dynamic offloading has a known "sticky" CUDA
out-of-memory failure mode (see README.md's Known limitations): once it
occurs, every subsequent CUDA call fails -- including a plain health
check -- until the process is killed and restarted. This module automates
that one recovery action.

It is Windows-specific (shells out to PowerShell for process control) and
talks to real OS processes and a real ComfyUI instance, so it is not
exercised by the unit tests. Callers inject `restart_comfyui` (or a fake)
via `make_panel`'s `recover` parameter instead of calling it unconditionally
-- that keeps the retry *decision* logic in make_panel.py testable
independent of this module. See AS-11 in the companion Audio Studio repo
for the analogous, still-unimplemented classify-and-retry problem this
solves for PanelForge first.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import comfy_client

LAUNCHER_SCRIPT = Path(__file__).with_name("start-comfyui.ps1")


class RecoveryError(RuntimeError):
    """ComfyUI could not be recovered by restarting it."""


def _powershell(command: str, *, timeout: float = 30.0) -> None:
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def kill_comfyui() -> None:
    """Best-effort: stop any running ComfyUI main.py process.

    Filters on both `main.py` and `ComfyUI` in the command line so this
    never touches an unrelated Python process.
    """
    _powershell(
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -like '*main.py*' -and $_.CommandLine -like '*ComfyUI*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )


def start_comfyui(launcher_script: Path = LAUNCHER_SCRIPT) -> None:
    """Launch ComfyUI detached from this process, in its own window."""
    _powershell(
        "Start-Process powershell -ArgumentList "
        f"'-NoProfile','-ExecutionPolicy','Bypass','-File','{launcher_script}' -WindowStyle Hidden"
    )


def restart_comfyui(
    comfy_url: str,
    *,
    launcher_script: Path = LAUNCHER_SCRIPT,
    kill_wait: float = 3.0,
    health_timeout: float = 60.0,
    health_poll_interval: float = 2.0,
) -> None:
    """Kill any running ComfyUI process, start a fresh one, and wait for health.

    Raises RecoveryError if ComfyUI never answers a health check within
    health_timeout of being (re)started.
    """
    kill_comfyui()
    time.sleep(kill_wait)
    start_comfyui(launcher_script)
    deadline = time.monotonic() + health_timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            comfy_client.health(comfy_url)
            return
        except RuntimeError as error:
            last_error = error
            time.sleep(health_poll_interval)
    raise RecoveryError(f"ComfyUI did not become healthy after restart: {last_error}")
