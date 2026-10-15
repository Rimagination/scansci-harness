"""Conservative, offline hardware guidance for one local assistant model."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import platform
import shutil
import subprocess
from typing import Any

from .ollama_runtime import OLLAMA_CHAT_MODEL_SPECS, is_local_ollama_url


def _ram_gib() -> float:
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        state = MemoryStatus()
        state.length = ctypes.sizeof(state)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
            return round(state.total_physical / 1024**3, 1)
        return 0.0
    try:
        return round(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024**3, 1)
    except (AttributeError, OSError, ValueError):
        return 0.0


def detect_local_hardware() -> dict[str, Any]:
    """Read installed memory and NVIDIA VRAM without loading a model runtime."""

    gpu_name = ""
    vram_gib = 0.0
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in result.stdout.splitlines():
            name, memory_mib = line.rsplit(",", 1)
            size = float(memory_mib.strip()) / 1024
            if size > vram_gib:
                gpu_name, vram_gib = name.strip(), size
    except (FileNotFoundError, OSError, subprocess.SubprocessError, ValueError):
        pass

    storage_path = os.getenv("OLLAMA_MODELS", "").strip()
    free_gib: float | None = None
    if storage_path:
        target = Path(storage_path).expanduser()
        while not target.exists() and target != target.parent:
            target = target.parent
        try:
            free_gib = round(shutil.disk_usage(target).free / 1024**3, 1)
        except OSError:
            pass
    return {
        "platform": platform.system(),
        "gpu_name": gpu_name,
        "vram_gib": round(vram_gib, 1),
        "ram_gib": _ram_gib(),
        "model_storage": storage_path,
        "storage_free_gib": free_gib,
    }


def recommend_chat_model(hardware: dict[str, Any], ollama: dict[str, Any]) -> dict[str, Any]:
    """Choose one supported Ollama model; installation remains user initiated."""

    vram = float(hardware.get("vram_gib") or 0)
    ram = float(hardware.get("ram_gib") or 0)
    system = str(hardware.get("platform") or "")
    if (vram >= 12 and ram >= 16) or (system == "Darwin" and ram >= 24):
        model_id = "qwen3.5:9b"
        reason = "本机内存与显存适合 9B 四位量化；Pi 任务从 16K 上下文开始。"
    elif vram >= 5 or ram >= 24:
        model_id = "qwen3.5:4b"
        reason = "4B 四位量化为本机留出更多运行空间。"
    elif ram >= 8:
        model_id = "qwen3.5:2b"
        reason = "当前硬件适合轻量模型；复杂科研任务建议继续使用已配置的在线模型。"
    else:
        return {"available": False, "reason": "未检测到适合充当本地主力助手的资源配置。", "hardware": hardware}

    local_runtime = bool(ollama.get("reachable")) and is_local_ollama_url(str(ollama.get("base_url") or ""))
    if not local_runtime:
        return {
            "available": False,
            "reason": "ScanSci 正在完善一体化本地主力模型；当前继续使用已配置的模型。已有 Ollama 可在“运行时”连接后复用。",
            "hardware": hardware,
        }
    names = {
        str(item.get("name") or item.get("model") or "").strip()
        for item in (ollama.get("models") or [])
        if isinstance(item, dict)
    }
    model = OLLAMA_CHAT_MODEL_SPECS[model_id]
    installed = model_id in names or f"{model_id}:latest" in names
    free_gib = hardware.get("storage_free_gib")
    storage_blocked = bool(
        not installed and free_gib is not None and float(free_gib) < model["download_gb"] * 1.4
    )
    return {
        "available": True,
        "id": model_id,
        "name": model["name"],
        "runtime": "ollama",
        "download_gb": model["download_gb"],
        "context_tokens": model["context_tokens"],
        "reason": reason,
        "installed": installed,
        "runtime_reachable": True,
        "storage_blocked": storage_blocked,
        "hardware": hardware,
    }
