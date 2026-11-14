"""
JARVIS Body Subsystem — Visual Human Presence.
Milestone B1: Rendering & IPC Foundation.
"""
from body.controller import BodyController
from body.integration.ipc_server import IPCServer
from body.integration.bridge import BodyStateBridge
from body.rendering.render_host import RenderHost
from body.rendering.telemetry import TelemetryMonitor

__all__ = [
    "BodyController",
    "IPCServer",
    "BodyStateBridge",
    "RenderHost",
    "TelemetryMonitor",
]
