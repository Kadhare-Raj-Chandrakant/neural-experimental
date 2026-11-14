"""
body/rendering/render_host.py — Milestone B1: Standalone Viewport Process Host & Watchdog.

Features:
1. Spawns decoupled Three.js viewport in dedicated browser app process.
2. Serves static WebGL 2 assets via loopback HTTP server.
3. Watchdog monitor detecting crashes and triggering automated recovery.
4. Total fault isolation: viewport death cannot stall JARVIS.
"""
import http.server
import logging
import os
import shutil
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("RenderHost")


class ViewportHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """Silent HTTP handler serving webgl_viewport directory."""

    def log_message(self, format, *args):
        # Suppress noisy HTTP request logging
        pass


class RenderHost:
    """Manages the standalone viewport process, HTTP asset server, and crash recovery watchdog."""

    def __init__(
        self,
        ws_port: int = 9876,
        http_port: int = 8765,
        session_token: str = "",
        auto_restart: bool = True,
    ):
        self.ws_port = ws_port
        self.http_port = http_port
        self.session_token = session_token
        self.auto_restart = auto_restart

        self.webgl_dir = Path(__file__).resolve().parent / "webgl_viewport"
        self.user_data_dir = Path(os.environ.get("TEMP", ".")) / "jarvis_viewport_profile"

        self.httpd: Optional[socketserver.TCPServer] = None
        self._http_thread: Optional[threading.Thread] = None

        self.process: Optional[subprocess.Popen] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._running = False

        self.crash_history: List[Dict[str, Any]] = []
        self.restart_count = 0

    def start_http_server(self):
        """Starts lightweight loopback HTTP server to serve Three.js assets."""
        if self.httpd:
            return

        web_dir = str(self.webgl_dir)

        class CustomHandler(ViewportHTTPHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=web_dir, **kwargs)

        # Allow socket reuse to avoid TIME_WAIT conflicts
        socketserver.TCPServer.allow_reuse_address = True
        self.httpd = socketserver.TCPServer(("127.0.0.1", self.http_port), CustomHandler)
        self._http_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="ViewportHTTPThread")
        self._http_thread.start()

    def stop_http_server(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def _locate_browser_binary(self) -> str:
        """Finds Edge, Chrome, or Brave binary for application-mode viewport."""
        candidates = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        for path in candidates:
            if os.path.exists(path):
                return path

        # Fallback to PATH
        for name in ("msedge", "brave", "chrome"):
            found = shutil.which(name)
            if found:
                return found

        raise FileNotFoundError("No Chromium-based browser (Edge, Chrome, Brave) found for standalone WebGL viewport.")

    def spawn_viewport(self):
        """Launches the viewport as an isolated native app process."""
        browser_bin = self._locate_browser_binary()
        url = f"http://127.0.0.1:{self.http_port}/index.html?ws={self.ws_port}&token={self.session_token}"

        args = [
            browser_bin,
            f"--app={url}",
            "--window-size=540,740",
            f"--user-data-dir={self.user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--enable-gpu-rasterization",
            "--enable-zero-copy",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
        ]

        kwargs = {}
        # Notice: we allow normal GUI window creation for the viewport, do NOT force CREATE_NO_WINDOW here
        self.process = subprocess.Popen(args, **kwargs)
        logger.info(f"[RenderHost] Spawned standalone viewport process PID={self.process.pid}")

    def start(self):
        """Starts the HTTP server, launches the viewport process, and starts the watchdog."""
        if self._running:
            return
        self._running = True

        self.start_http_server()
        self.spawn_viewport()

        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True, name="ViewportWatchdogThread")
        self._watchdog_thread.start()

    def _watchdog_loop(self):
        """Monitors viewport process. If it crashes unexpectedly, recovers by respawning."""
        while self._running:
            if self.process:
                ret = self.process.poll()
                if ret is not None:
                    # Process died!
                    crash_info = {
                        "pid": self.process.pid,
                        "exit_code": ret,
                        "timestamp": time.time(),
                        "restarted": False,
                    }
                    logger.warning(f"[RenderHost] [WATCHDOG] Viewport process PID={self.process.pid} terminated with code {ret}!")

                    if self.auto_restart and self._running:
                        time.sleep(0.5)
                        logger.info("[RenderHost] [WATCHDOG] Automatically recovering viewport process...")
                        try:
                            self.spawn_viewport()
                            self.restart_count += 1
                            crash_info["restarted"] = True
                            crash_info["new_pid"] = self.process.pid
                        except Exception as re:
                            logger.error(f"[RenderHost] Failed to restart viewport: {re}")

                    self.crash_history.append(crash_info)
            time.sleep(0.2)

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def get_pid(self) -> Optional[int]:
        if self.is_alive():
            return self.process.pid
        return None

    def kill_viewport(self):
        """Forces immediate termination of the viewport (for testing crash recovery)."""
        if self.process and self.process.poll() is None:
            pid = self.process.pid
            try:
                # Use taskkill /F /T /PID on Windows
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=5)
            except Exception:
                self.process.kill()

    def stop(self):
        """Stops the viewport, watchdog, and HTTP server cleanly."""
        self._running = False
        self.auto_restart = False
        self.kill_viewport()
        self.stop_http_server()
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=1.0)
        if self._http_thread and self._http_thread.is_alive():
            self._http_thread.join(timeout=1.0)

    def get_crash_stats(self) -> Dict[str, Any]:
        return {
            "total_crashes": len(self.crash_history),
            "restart_count": self.restart_count,
            "crash_history": self.crash_history,
        }
