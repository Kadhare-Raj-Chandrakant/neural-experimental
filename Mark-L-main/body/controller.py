"""
body/controller.py — Milestone B1: Main Controller for the JARVIS Body Subsystem.

Coordinates:
- IPCServer (secure loopback WebSocket)
- RenderHost (isolated browser process + watchdog)
- TelemetryMonitor (real-time resource & FPS tracking)
- BodyStateBridge (one-way Brain state observer)
"""
import secrets
import time
from typing import Any, Dict, Optional

from body.integration.ipc_server import IPCServer
from body.integration.bridge import BodyStateBridge
from body.rendering.render_host import RenderHost
from body.rendering.telemetry import TelemetryMonitor


class BodyController:
    """Central orchestrator for the JARVIS Body Subsystem."""

    def __init__(self, ws_port: int = 9876, http_port: int = 8765):
        self.session_token = secrets.token_urlsafe(24)
        self.ipc_server = IPCServer(host="127.0.0.1", port=ws_port, session_token=self.session_token)
        self.render_host = RenderHost(
            ws_port=ws_port,
            http_port=http_port,
            session_token=self.session_token,
            auto_restart=True,
        )
        self.telemetry = TelemetryMonitor()
        self.bridge = BodyStateBridge(self.ipc_server)
        self._is_active = False

    def start(self):
        """Starts the IPC server, launches the standalone 3D viewport, and links telemetry."""
        if self._is_active:
            return
        self._is_active = True

        self.ipc_server.start()
        self.render_host.start()

        # Link telemetry to child process PID
        pid = self.render_host.get_pid()
        if pid:
            self.telemetry.set_target_pid(pid)

    def stop(self):
        """Gracefully halts the viewport, IPC server, and background threads."""
        self._is_active = False
        self.render_host.stop()
        self.ipc_server.stop()

    def get_live_metrics(self) -> Dict[str, Any]:
        """Returns comprehensive measured telemetry and budget compliance."""
        pid = self.render_host.get_pid()
        if pid and self.telemetry.target_pid != pid:
            self.telemetry.set_target_pid(pid)

        compliance = self.telemetry.evaluate_compliance(self.ipc_server.latest_telemetry)
        latency = self.ipc_server.get_latency_stats()
        crash_stats = self.render_host.get_crash_stats()

        return {
            "status": "ONLINE" if self.render_host.is_alive() else "OFFLINE",
            "compliance": compliance,
            "latency": latency,
            "crashes": crash_stats,
            "connected_clients": len(self.ipc_server.authenticated_clients),
        }

    def set_visual_state(self, state: str, extra_data: Optional[Dict[str, Any]] = None):
        """Dispatches an updated visual state (e.g. IDLE, THINKING, SPEAKING) to the viewport."""
        self.bridge.notify_state(state, extra_data)
