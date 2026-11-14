"""
body/integration/ipc_server.py — Milestone B1: Secure Local Loopback IPC Server.

Enforces:
1. Loopback-only binding (127.0.0.1).
2. Ephemeral session token authentication.
3. Inbound message allowlisting (rejects unauthorized command injection).
4. One-way visual-state broadcast (body never commands desktop or Brain).
5. High-precision round-trip latency benchmarking (p50, p95, mean).
"""
import asyncio
import json
import logging
import secrets
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set
import numpy as np
import websockets

logger = logging.getLogger("BodyIPC")

ALLOWED_MESSAGE_TYPES = {
    "auth",
    "ping",
    "pong",
    "telemetry_report",
    "heartbeat",
    "state_ack",
}


class IPCServer:
    """Secure loopback WebSocket server managing bidirectional communication with the 3D viewport."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9876, session_token: Optional[str] = None):
        # Enforce loopback-only binding
        if host not in ("127.0.0.1", "localhost"):
            raise ValueError(f"Security violation: IPC server must bind to loopback (127.0.0.1), got '{host}'")
        self.host = "127.0.0.1"
        self.port = port
        self.session_token = session_token or secrets.token_urlsafe(24)

        self.server: Optional[Any] = None
        self.clients: Set[Any] = set()
        self.authenticated_clients: Set[Any] = set()

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        # Telemetry & Latency buffers
        self.rtt_samples: List[float] = []
        self.latest_telemetry: Dict[str, Any] = {
            "fps": 0.0,
            "frame_time_ms": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "frame_count": 0,
            "timestamp": 0.0,
        }
        self.telemetry_history: List[Dict[str, Any]] = []
        self.rejected_messages_count = 0
        self.on_state_ack_callback: Optional[Callable[[str], None]] = None

    def start(self):
        """Starts the WebSocket IPC server in a dedicated background daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_event_loop, daemon=True, name="BodyIPCServerThread")
        self._thread.start()

        # Wait until loop and server are active
        for _ in range(50):
            if self.server is not None and self._loop is not None:
                break
            time.sleep(0.05)

    def _run_event_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def _serve():
            async with websockets.serve(self._handle_client, self.host, self.port) as server:
                self.server = server
                while self._running:
                    await asyncio.sleep(0.1)

        try:
            self._loop.run_until_complete(_serve())
        except Exception as e:
            logger.error(f"[BodyIPC] Server loop error: {e}")
        finally:
            self._loop.close()

    def stop(self):
        """Stops the WebSocket IPC server cleanly."""
        self._running = False
        if self._loop and self.server:
            # Schedule server close
            asyncio.run_coroutine_threadsafe(self._close_all(), self._loop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    async def _close_all(self):
        for ws in list(self.clients):
            await ws.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle_client(self, websocket, path=None):
        self.clients.add(websocket)
        authenticated = False

        # 1. Handshake / Auth check: verify URL query parameter or wait for 'auth' message
        # In newer websockets, query is in request.path or we can parse path
        try:
            # Check path for ?token=...
            if hasattr(websocket, "request") and websocket.request:
                req_path = websocket.request.path
            else:
                req_path = getattr(websocket, "path", "")

            if f"token={self.session_token}" in req_path:
                authenticated = True
                self.authenticated_clients.add(websocket)
                await websocket.send(json.dumps({"type": "auth_ok", "status": "authenticated"}))

            # Main message receive loop
            async for raw_message in websocket:
                try:
                    data = json.loads(raw_message)
                except Exception:
                    self.rejected_messages_count += 1
                    continue

                msg_type = data.get("type", "")

                # Reject any un-whitelisted message types
                if msg_type not in ALLOWED_MESSAGE_TYPES:
                    self.rejected_messages_count += 1
                    logger.warning(f"[BodyIPC] [BLOCKED] Unauthorized message type: '{msg_type}'")
                    await websocket.send(json.dumps({
                        "type": "error",
                        "code": "UNAUTHORIZED_MESSAGE_TYPE",
                        "message": f"Message type '{msg_type}' is not allowed."
                    }))
                    continue

                # Handle auth message if not yet authenticated via URL
                if msg_type == "auth":
                    token = data.get("token", "")
                    if secrets.compare_digest(token, self.session_token):
                        authenticated = True
                        self.authenticated_clients.add(websocket)
                        await websocket.send(json.dumps({"type": "auth_ok", "status": "authenticated"}))
                    else:
                        logger.warning("[BodyIPC] [SECURITY] Client authentication failed (invalid token). Closing connection.")
                        await websocket.send(json.dumps({"type": "error", "code": "AUTH_FAILED"}))
                        await websocket.close(1008, "Authentication failed")
                        return

                if not authenticated:
                    self.rejected_messages_count += 1
                    await websocket.send(json.dumps({"type": "error", "code": "UNAUTHENTICATED"}))
                    continue

                # Handle Pong / Latency calculation
                if msg_type == "pong":
                    client_send_ts = data.get("client_ts", 0.0)
                    now = time.time()
                    if client_send_ts > 0:
                        rtt_ms = (now - client_send_ts) * 1000.0
                        self.rtt_samples.append(rtt_ms)
                        if len(self.rtt_samples) > 500:
                            self.rtt_samples.pop(0)

                # Handle Viewport Telemetry Reports
                elif msg_type == "telemetry_report":
                    report = {
                        "fps": float(data.get("fps", 0.0)),
                        "frame_time_ms": float(data.get("frame_time_ms", 0.0)),
                        "p50_ms": float(data.get("p50_ms", 0.0)),
                        "p95_ms": float(data.get("p95_ms", 0.0)),
                        "frame_count": int(data.get("frame_count", 0)),
                        "timestamp": time.time(),
                    }
                    self.latest_telemetry = report
                    self.telemetry_history.append(report)
                    if len(self.telemetry_history) > 300:
                        self.telemetry_history.pop(0)

                elif msg_type == "heartbeat":
                    await websocket.send(json.dumps({"type": "heartbeat_ack", "timestamp": time.time()}))

                elif msg_type == "state_ack":
                    state_name = data.get("state", "")
                    if self.on_state_ack_callback:
                        self.on_state_ack_callback(state_name)

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)
            self.authenticated_clients.discard(websocket)

    def broadcast_state(self, state: str, extra_data: Optional[Dict[str, Any]] = None):
        """Broadcasts a high-level visual state to the connected 3D viewport (one-way)."""
        if not self._loop or not self.authenticated_clients:
            return

        payload = {"type": "visual_state", "state": state, "timestamp": time.time()}
        if extra_data:
            payload.update(extra_data)

        msg = json.dumps(payload)
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)

    async def _broadcast(self, msg: str):
        for ws in list(self.authenticated_clients):
            try:
                await ws.send(msg)
            except Exception:
                pass

    def send_ping(self) -> float:
        """Sends a ping message to calculate round-trip latency."""
        now = time.time()
        msg = json.dumps({"type": "ping", "client_ts": now})
        if self._loop and self.authenticated_clients:
            asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)
        return now

    def get_latency_stats(self) -> Dict[str, float]:
        """Returns empirical round-trip latency distribution: p50, p95, and mean in ms."""
        if not self.rtt_samples:
            return {"count": 0, "mean_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0}

        arr = np.array(self.rtt_samples)
        return {
            "count": len(self.rtt_samples),
            "mean_ms": float(np.mean(arr)),
            "p50_ms": float(np.percentile(arr, 50)),
            "p95_ms": float(np.percentile(arr, 95)),
            "min_ms": float(np.min(arr)),
            "max_ms": float(np.max(arr)),
        }
