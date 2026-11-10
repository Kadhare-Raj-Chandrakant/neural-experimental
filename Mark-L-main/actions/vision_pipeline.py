"""
actions/vision_pipeline.py — Continuous Vision Pipeline, Latest-Frame Queue & Safety Lifecycle.

Provides:
- LatestFrameQueue: Capacity=1 bounded frame buffer with timestamp freshness validation (drops stale frames).
- AdaptiveFrameScheduler: Dynamically throttles capture rate (1 FPS passive, 2 FPS burst).
- VisionSessionManager: Centralized safety gate, emergency cancellation event, and observation lifecycle.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional, Tuple


class LatestFrameQueue:
    """
    Thread-safe bounded frame queue holding strictly ONE pending frame.
    Replaces pending frames with newer ones and drops frames that exceed max_age.
    """

    def __init__(self, max_age: float = 1.5):
        self._lock = threading.Lock()
        self._frame: Optional[bytes] = None
        self._mime_type: Optional[str] = None
        self._timestamp: float = 0.0
        self._max_age: float = max_age
        self._dropped_count: int = 0
        self._served_count: int = 0

    def put_frame(self, data: bytes, mime_type: str = "image/jpeg") -> None:
        """Atomically stores the latest frame, replacing any unconsumed older frame."""
        with self._lock:
            if self._frame is not None:
                self._dropped_count += 1
            self._frame = data
            self._mime_type = mime_type
            self._timestamp = time.monotonic()

    def get_frame(self) -> Optional[Tuple[bytes, str]]:
        """
        Retrieves the pending frame if it is fresh.
        Returns None if no frame is present or if the frame exceeds max_age.
        """
        with self._lock:
            if self._frame is None:
                return None

            age = time.monotonic() - self._timestamp
            if age > self._max_age:
                # Stale frame: discard
                self._frame = None
                self._mime_type = None
                self._dropped_count += 1
                return None

            data = self._frame
            mime = self._mime_type or "image/jpeg"
            # Consume the frame
            self._frame = None
            self._mime_type = None
            self._served_count += 1
            return data, mime

    def clear(self) -> None:
        """Flushes the queue immediately."""
        with self._lock:
            self._frame = None
            self._mime_type = None
            self._timestamp = 0.0

    @property
    def stats(self) -> dict:
        with self._lock:
            return {
                "served": self._served_count,
                "dropped": self._dropped_count,
                "has_pending": self._frame is not None,
            }


class AdaptiveFrameScheduler:
    """
    Controls capture frequency:
    - 1.0 FPS (1.0s interval) during passive observation.
    - 2.0 FPS (0.5s interval) burst during active commands or visual queries.
    """

    def __init__(self, passive_interval: float = 1.0, burst_interval: float = 0.5):
        self._passive_interval = passive_interval
        self._burst_interval = burst_interval
        self._burst_until: float = 0.0
        self._lock = threading.Lock()

    def request_burst(self, duration: float = 5.0) -> None:
        """Requests high-frequency capture (2 FPS) for the specified duration."""
        with self._lock:
            self._burst_until = max(self._burst_until, time.monotonic() + duration)

    def get_interval(self) -> float:
        """Returns the appropriate sleep interval for the next frame."""
        with self._lock:
            if time.monotonic() < self._burst_until:
                return self._burst_interval
            return self._passive_interval

    @property
    def is_burst_active(self) -> bool:
        with self._lock:
            return time.monotonic() < self._burst_until


class VisionSessionManager:
    """
    Central safety controller and observation lifecycle manager.
    Separates Observation from Control and manages emergency stop states.
    """

    _instance: Optional["VisionSessionManager"] = None
    _singleton_lock = threading.Lock()

    def __init__(self):
        self.observation_active: bool = False
        self.control_enabled: bool = True
        self.is_paused: bool = False
        self.cancellation_event = threading.Event()
        self.frame_queue = LatestFrameQueue(max_age=1.5)
        self.scheduler = AdaptiveFrameScheduler(passive_interval=1.0, burst_interval=0.5)
        self._status_listeners: list[Callable[[str], None]] = []
        self._lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "VisionSessionManager":
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def add_status_listener(self, listener: Callable[[str], None]) -> None:
        with self._lock:
            self._status_listeners.append(listener)

    def _notify_status(self) -> None:
        status_text = self.get_status_text()
        for listener in self._status_listeners:
            try:
                listener(status_text)
            except Exception:
                pass

    def get_status_text(self) -> str:
        if self.is_paused:
            return "PAUSED"
        if not self.observation_active:
            return "IDLE"
        if not self.control_enabled:
            return "EYES ON (VIEW ONLY)"
        return "EYES ON / ACTIVE"

    def start_observation(self) -> str:
        with self._lock:
            self.observation_active = True
            self.is_paused = False
            self.cancellation_event.clear()
            self.frame_queue.clear()
        self._notify_status()
        print("[VisionPipeline] [STATE] Continuous Screen Observation: ACTIVE")
        return "Observation started."

    def stop_observation(self) -> str:
        with self._lock:
            self.observation_active = False
            self.frame_queue.clear()
        self._notify_status()
        print("[VisionPipeline] [STATE] Continuous Screen Observation: STOPPED")
        return "Observation stopped."

    def emergency_stop(self) -> str:
        """Immediate emergency pause: halts all in-flight and pending desktop actions."""
        with self._lock:
            self.is_paused = True
            self.cancellation_event.set()
            self.frame_queue.clear()
        self._notify_status()
        print("[VisionPipeline] [EMERGENCY] Emergency Stop Triggered! Desktop actions cancelled.")
        return "Emergency stop activated. All actions halted."

    def resume_from_pause(self) -> str:
        with self._lock:
            self.is_paused = False
            self.cancellation_event.clear()
        self._notify_status()
        print("[VisionPipeline] [STATE] Resumed from Emergency Pause.")
        return "Resumed."

    def set_control_enabled(self, enabled: bool) -> str:
        with self._lock:
            self.control_enabled = enabled
        self._notify_status()
        state_str = "ENABLED" if enabled else "DISABLED (View-Only)"
        print(f"[VisionPipeline] [STATE] Desktop Control: {state_str}")
        return f"Desktop control {state_str}."

    def is_action_allowed(self) -> Tuple[bool, str]:
        """
        Safety Gate check before executing ANY desktop action.
        Returns (allowed: bool, reason: str).
        """
        if self.is_paused:
            return False, "Emergency stop is active. Desktop actions are currently blocked."
        if self.cancellation_event.is_set():
            return False, "Action cancelled by user interrupt."
        if not self.control_enabled:
            return False, "Desktop control is disabled (Observation view-only mode)."
        return True, "OK"

