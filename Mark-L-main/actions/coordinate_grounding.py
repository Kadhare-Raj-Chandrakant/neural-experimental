"""
actions/coordinate_grounding.py — Centralized Coordinate Transformation, Action Validation & Visual Verification.

Resolves normalized visual coordinates from AI models (0-1000 or 0.0-1.0) into verified
physical display pixels across single and multi-monitor layouts, with strict boundary validation,
edge-case clamping (width - 1, height - 1), and post-action visual verification.
"""

from __future__ import annotations

import io
import platform
from typing import Any, Dict, List, Optional, Tuple

try:
    import pyautogui
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import mss
    _MSS = True
except ImportError:
    _MSS = False

try:
    import PIL.Image
    import numpy as np
    _IMAGING = True
except ImportError:
    _IMAGING = False


def init_dpi_awareness() -> bool:
    """Initializes per-monitor DPI awareness on Windows for exact pixel alignment."""
    if platform.system() == "Windows":
        try:
            import ctypes
            # PROCESS_PER_MONITOR_DPI_AWARE = 2
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return True
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
                return True
            except Exception:
                pass
    return False


def attach_interactive_desktop() -> bool:
    """Ensures the calling thread is attached to the active user desktop (Default)."""
    if platform.system() == "Windows":
        try:
            import ctypes
            u32 = ctypes.windll.user32
            h_desk = u32.OpenDesktopW("Default", 0, False, 0x01FF)
            if h_desk:
                u32.SetThreadDesktop(h_desk)
                return True
        except Exception as e:
            print(f"[CoordinateGrounding] attach_interactive_desktop error: {e}")
    return False


# Auto-initialize DPI awareness and interactive desktop attachment
init_dpi_awareness()
attach_interactive_desktop()


class CoordinateTransformer:
    """
    Centralized coordinate transformation and monitor boundary validation.
    Ensures AI-generated coordinates always map to physical screen coordinates safely.
    """

    @classmethod
    def get_monitors(cls) -> List[Dict[str, int]]:
        """
        Returns a list of all physical display geometries.
        Monitor 0 is the combined virtual desktop; indices 1..N are physical monitors.
        """
        if _MSS:
            try:
                with mss.mss() as sct:
                    return list(sct.monitors)
            except Exception as e:
                print(f"[CoordinateTransformer] [WARN] MSS monitor query error: {e}")

        # Fallback to PyAutoGUI screen size if MSS fails
        if _PYAUTOGUI:
            w, h = pyautogui.size()
            return [{"left": 0, "top": 0, "width": w, "height": h}, {"left": 0, "top": 0, "width": w, "height": h}]

        return [{"left": 0, "top": 0, "width": 1920, "height": 1080}, {"left": 0, "top": 0, "width": 1920, "height": 1080}]

    @classmethod
    def get_primary_monitor(cls) -> Dict[str, int]:
        """Returns the primary monitor bounding box."""
        monitors = cls.get_monitors()
        if len(monitors) > 1:
            return monitors[1]
        return monitors[0]

    @classmethod
    def is_within_bounds(cls, x: int, y: int) -> bool:
        """Validates that a physical coordinate (x, y) falls inside an actual physical monitor."""
        monitors = cls.get_monitors()
        physical = monitors[1:] if len(monitors) > 1 else monitors
        for m in physical:
            left = m["left"]
            top = m["top"]
            right = left + m["width"]
            bottom = top + m["height"]
            if left <= x < right and top <= y < bottom:
                return True
        return False

    @classmethod
    def transform(
        cls,
        x: Any,
        y: Any,
        monitor_index: int = 1,
        force_normalized: Optional[bool] = None,
    ) -> Tuple[Optional[int], Optional[int], str]:
        """
        Transforms input coordinates (normalized or pixel) into validated physical desktop coordinates.

        Supports:
        - Float normalized: 0.0 <= x <= 1.0 (e.g. 0.5, 0.5 -> center)
        - Gemini 0-1000 scale: 0 <= x <= 1000 (e.g. 500, 500 -> center)
        - Raw physical pixels: exact pixel coordinates (when force_normalized=False)

        Uses (width - 1) and (height - 1) mapping so boundary coordinates (1000, 1000)
        and (1.0, 1.0) cleanly resolve to the rightmost/bottom-most valid display pixel.
        """
        if x is None or y is None:
            return None, None, "Coordinate values cannot be None"

        try:
            fx = float(x)
            fy = float(y)
        except (ValueError, TypeError):
            return None, None, f"Invalid coordinate format: ({x}, {y})"

        monitors = cls.get_monitors()
        if 1 <= monitor_index < len(monitors):
            target_mon = monitors[monitor_index]
        else:
            target_mon = cls.get_primary_monitor()

        m_left = target_mon["left"]
        m_top = target_mon["top"]
        m_width = target_mon["width"]
        m_height = target_mon["height"]

        # Maximum valid pixel offsets within this monitor
        max_dx = max(0, m_width - 1)
        max_dy = max(0, m_height - 1)

        # Case 1: Float normalized [0.0, 1.0]
        if (isinstance(x, float) or isinstance(y, float)) and (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0) and (fx < 1.0 or fy < 1.0 or force_normalized is True):
            px = int(round(m_left + fx * max_dx))
            py = int(round(m_top + fy * max_dy))
            px = max(m_left, min(px, m_left + max_dx))
            py = max(m_top, min(py, m_top + max_dy))
        # Case 2: Gemini / Standard Vision normalized [0, 1000] scale
        elif force_normalized is True or (force_normalized is None and (0 <= fx <= 1000 and 0 <= fy <= 1000 and (m_width > 1000 or m_height > 1000))):
            px = int(round(m_left + (fx / 1000.0) * max_dx))
            py = int(round(m_top + (fy / 1000.0) * max_dy))
            px = max(m_left, min(px, m_left + max_dx))
            py = max(m_top, min(py, m_top + max_dy))
        # Case 3: Raw physical pixel coordinates (when force_normalized is False or outside 0-1000)
        else:
            px = int(round(fx))
            py = int(round(fy))

        # Boundary validation across all monitors
        if not cls.is_within_bounds(px, py):
            return None, None, f"Transformed coordinate ({px}, {py}) falls outside all display boundaries"

        return px, py, "OK"


class ActionValidator:
    """
    Safeguards compound actions against runaway loops, invalid steps, or unvalidated chains.
    """

    MAX_COMPOUND_STEPS = 25
    ALLOWED_ACTIONS = {
        "click", "double_click", "right_click", "move", "drag",
        "type", "smart_type", "hotkey", "press", "scroll",
        "wait", "clear_field", "focus_window", "verify_outcome"
    }

    @classmethod
    def validate_compound_chain(cls, steps: List[Dict[str, Any]]) -> Tuple[bool, str]:
        """Validates that a proposed compound action chain meets all safety limits."""
        if not isinstance(steps, list):
            return False, "Steps must be a list of action dictionaries"

        if len(steps) == 0:
            return False, "Compound action step list cannot be empty"

        if len(steps) > cls.MAX_COMPOUND_STEPS:
            return False, f"Compound action exceeds safety limit of {cls.MAX_COMPOUND_STEPS} steps (got {len(steps)})"

        for idx, step in enumerate(steps, 1):
            if not isinstance(step, dict):
                return False, f"Step #{idx} must be a dictionary"

            act = step.get("action", "").lower().strip()
            if not act:
                return False, f"Step #{idx} missing required 'action' field"

            if act not in cls.ALLOWED_ACTIONS:
                return False, f"Step #{idx} specifies unsupported action: '{act}'"

            # Validate wait times
            if act == "wait":
                secs = float(step.get("seconds", 0.5))
                if secs > 3.0:
                    return False, f"Step #{idx} wait duration exceeds maximum safety limit of 3.0s (got {secs}s)"

            # Validate coordinate fields if present
            if act in ("click", "double_click", "right_click", "move"):
                x = step.get("x")
                y = step.get("y")
                if x is not None and y is not None:
                    px, py, msg = CoordinateTransformer.transform(x, y, force_normalized=step.get("normalized", False))
                    if px is None:
                        return False, f"Step #{idx} invalid coordinate: {msg}"

        return True, "OK"


class VisualVerifier:
    """
    Post-action visual verification engine.
    Computes visual differential metrics (percentage change) between before and after frames,
    and supports element confirmation via screen_find.
    """

    @classmethod
    def capture_frame(cls) -> Optional[Any]:
        """Captures a fast in-memory screenshot as a PIL Image."""
        if not _IMAGING:
            return None
        try:
            if _MSS:
                with mss.mss() as sct:
                    monitors = sct.monitors
                    target = monitors[1] if len(monitors) > 1 else monitors[0]
                    shot = sct.grab(target)
                    return PIL.Image.frombytes("RGB", shot.size, shot.rgb)
            elif _PYAUTOGUI:
                return pyautogui.screenshot()
        except Exception as e:
            print(f"[VisualVerifier] Capture error: {e}")
        return None

    @classmethod
    def compute_difference_percentage(
        cls,
        img_before: Any,
        img_after: Any,
        region: Optional[Tuple[int, int, int, int]] = None,
    ) -> float:
        """
        Computes the percentage of changed pixels between two PIL images.
        Optional region: (x, y, width, height).
        Returns a percentage value between 0.0% and 100.0%.
        """
        if not _IMAGING or img_before is None or img_after is None:
            return 0.0

        try:
            # Crop to region if specified
            if region:
                rx, ry, rw, rh = region
                box = (rx, ry, rx + rw, ry + rh)
                img_before = img_before.crop(box)
                img_after = img_after.crop(box)

            # Resize to thumbnail for ultra-fast NumPy diff calculation
            b_thumb = img_before.resize((320, 180), PIL.Image.BILINEAR)
            a_thumb = img_after.resize((320, 180), PIL.Image.BILINEAR)

            arr1 = np.asarray(b_thumb, dtype=np.int16)
            arr2 = np.asarray(a_thumb, dtype=np.int16)

            diff = np.abs(arr1 - arr2)
            # Count pixels where any color channel changed significantly (> 15 / 255)
            changed_pixels = np.any(diff > 15, axis=-1)
            percent = (np.count_nonzero(changed_pixels) / changed_pixels.size) * 100.0
            return round(float(percent), 2)
        except Exception as e:
            print(f"[VisualVerifier] Difference calculation error: {e}")
            return 0.0

    @classmethod
    def verify_action_outcome(
        cls,
        img_before: Any,
        img_after: Any,
        threshold_percent: float = 0.05,
        region: Optional[Tuple[int, int, int, int]] = None,
    ) -> Tuple[bool, float, str]:
        """
        Evaluates whether a visible change took place on screen following an action.
        Returns (has_changed: bool, delta_percent: float, message: str).
        """
        delta = cls.compute_difference_percentage(img_before, img_after, region=region)
        has_changed = delta >= threshold_percent
        msg = f"{delta}% screen delta detected" if has_changed else f"No significant screen change ({delta}% delta)"
        return has_changed, delta, msg
