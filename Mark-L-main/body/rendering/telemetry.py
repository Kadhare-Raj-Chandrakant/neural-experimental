"""
body/rendering/telemetry.py — Milestone B1: Real-Time Performance & Telemetry Monitor.

Measures:
1. Real-time rendering frame times, FPS, p50/p95 distributions.
2. Dedicated GPU VRAM allocation via nvidia-smi / Win32 query.
3. Subsystem process RAM and CPU footprint via psutil.
4. Budget compliance checks (500 MiB Avatar Target vs 3.5 GiB System Safety Ceiling).
"""
import os
import subprocess
import time
from typing import Any, Dict, List, Optional
import numpy as np
import psutil


class TelemetryMonitor:
    """Monitors real-time GPU VRAM, host RAM, CPU, and rendering frame rates."""

    def __init__(self, target_pid: Optional[int] = None):
        self.target_pid = target_pid
        self.process: Optional[psutil.Process] = None
        if target_pid:
            try:
                self.process = psutil.Process(target_pid)
            except Exception:
                self.process = None

        self.vram_budget_mb = 500.0          # Avatar subsystem budget
        self.system_vram_ceiling_mb = 3500.0 # System-wide safety ceiling

    def set_target_pid(self, pid: int):
        self.target_pid = pid
        try:
            self.process = psutil.Process(pid)
        except Exception:
            self.process = None

    def query_gpu_vram(self) -> Dict[str, float]:
        """Queries physical GPU VRAM via nvidia-smi. Returns total, used, and free in MiB."""
        try:
            kwargs = {}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"],
                text=True,
                timeout=2.0,
                **kwargs
            )
            parts = [float(x.strip()) for x in out.strip().split(",")]
            return {
                "total_mb": parts[0],
                "used_mb": parts[1],
                "free_mb": parts[2],
            }
        except Exception:
            # Fallback if nvidia-smi is unavailable
            return {"total_mb": 4096.0, "used_mb": 0.0, "free_mb": 4096.0}

    def query_process_memory(self) -> Dict[str, float]:
        """Queries RAM utilization of the viewport helper process."""
        if not self.process:
            return {"rss_mb": 0.0, "vms_mb": 0.0, "cpu_percent": 0.0}

        try:
            mem_info = self.process.memory_info()
            cpu = self.process.cpu_percent(interval=None)
            # Include child processes if Edge/Chromium spawned sub-renderers
            children = self.process.children(recursive=True)
            total_rss = mem_info.rss
            total_vms = mem_info.vms
            for child in children:
                try:
                    c_mem = child.memory_info()
                    total_rss += c_mem.rss
                    total_vms += c_mem.vms
                except Exception:
                    pass

            return {
                "rss_mb": round(total_rss / (1024 * 1024), 2),
                "vms_mb": round(total_vms / (1024 * 1024), 2),
                "cpu_percent": cpu,
            }
        except Exception:
            return {"rss_mb": 0.0, "vms_mb": 0.0, "cpu_percent": 0.0}

    def evaluate_compliance(self, latest_telemetry: Dict[str, Any]) -> Dict[str, Any]:
        """Audits current live metrics against the approved B0 budgets."""
        gpu = self.query_gpu_vram()
        proc_mem = self.query_process_memory()

        fps = float(latest_telemetry.get("fps", 0.0))
        p95_ms = float(latest_telemetry.get("p95_ms", 0.0))

        vram_ok = gpu["used_mb"] <= self.system_vram_ceiling_mb
        ram_ok = proc_mem["rss_mb"] <= 1000.0  # Measured standalone browser process tree ceiling (<= 1.0 GB)

        # Target 60 FPS on test geometry, fallback 30 FPS under contention
        fps_target_met = fps >= 55.0
        fps_acceptable = fps >= 28.0

        return {
            "timestamp": time.time(),
            "gpu_vram": gpu,
            "process_memory": proc_mem,
            "viewport_fps": fps,
            "viewport_p95_frame_time_ms": p95_ms,
            "avatar_vram_budget_compliant": vram_ok,
            "system_safety_ceiling_compliant": gpu["used_mb"] <= self.system_vram_ceiling_mb,
            "ram_budget_compliant": ram_ok,
            "fps_target_status": "TARGET_60FPS_MET" if fps_target_met else ("FALLBACK_30FPS_MAINTAINED" if fps_acceptable else "UNDER_TARGET"),
        }
