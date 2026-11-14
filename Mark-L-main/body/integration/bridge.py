"""
body/integration/bridge.py — Milestone B1: Non-Invasive State Bridge.

Connects JARVIS Brain / Core state changes to the Body Subsystem via one-way notification.
"""
from typing import Any, Dict, Optional
import logging

logger = logging.getLogger("BodyBridge")


class BodyStateBridge:
    """Non-blocking event router propagating JARVIS operational states to the body subsystem."""

    def __init__(self, ipc_server=None):
        self.ipc_server = ipc_server

    def set_ipc_server(self, ipc_server):
        self.ipc_server = ipc_server

    def notify_state(self, state: str, extra_data: Optional[Dict[str, Any]] = None):
        """Propagates state (IDLE, LISTENING, THINKING, SPEAKING, EXECUTING) to viewport."""
        if not self.ipc_server:
            return
        try:
            self.ipc_server.broadcast_state(state, extra_data)
        except Exception as e:
            logger.debug(f"[BodyBridge] Broadcast error: {e}")
