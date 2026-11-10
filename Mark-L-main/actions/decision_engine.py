"""
actions/decision_engine.py — Fast, non-autoregressive decision & triage engine for MARK L.

Powered by `laya` with zero-overhead fallback. 
Adheres strictly to the architectural invariant: 100% additive, non-disruptive.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

# Attempt to import laya components safely
_LAYA_AVAILABLE = False
try:
    import laya
    import laya.lang as laya_lang
    _LAYA_AVAILABLE = True
except Exception as e:
    laya = None
    laya_lang = None


class DecisionEngine:
    """
    Local decision engine for MARK L.
    Handles intent triage, error classification, and proactive timing evaluation
    without requiring cloud LLM latency.
    """

    @classmethod
    def is_available(cls) -> bool:
        return _LAYA_AVAILABLE

    @classmethod
    def detect_language(cls, text: str) -> Dict[str, Any]:
        """
        Fast language and script detection using laya.lang.
        Returns script, language code, and english/multilingual flags.
        """
        if not text or not text.strip():
            return {"language": "en", "is_english": True}

        if _LAYA_AVAILABLE and hasattr(laya, "detect_language"):
            try:
                res = laya.detect_language(text)
                return res
            except Exception:
                pass

        # Fallback simple heuristic
        return {"language": "en", "is_english": True}

    @classmethod
    def triage_error(cls, error_output: str) -> Dict[str, Any]:
        """
        Calibrated error triage for dev_agent and code_helper.
        Classifies error output into actionable categories with confidence.
        """
        if not error_output or not error_output.strip():
            return {
                "error_type": "none",
                "action": "none",
                "confidence": 1.0,
                "missing_module": None,
            }

        low = error_output.lower()

        # Missing module / dependency
        pkg_match = re.search(r"No module named\s+['\"]?([a-zA-Z0-9_\-\.]+)['\"]?", error_output, re.IGNORECASE)
        if pkg_match or "modulenotfounderror" in low:
            pkg = pkg_match.group(1).replace("_", "-").split(".")[0] if pkg_match else None
            return {
                "error_type": "dependency_error",
                "action": "install_package",
                "confidence": 0.95,
                "missing_module": pkg,
            }

        # Syntax error
        if "syntaxerror" in low or "invalid syntax" in low or "indentationerror" in low:
            return {
                "error_type": "syntax_error",
                "action": "fix_syntax",
                "confidence": 0.95,
                "missing_module": None,
            }

        # Import error / circular dependency
        if "cannot import" in low or ("importerror" in low and "no module named" not in low):
            return {
                "error_type": "import_error",
                "action": "fix_imports",
                "confidence": 0.90,
                "missing_module": None,
            }

        # Runtime errors
        runtime_indicators = (
            "traceback", "nameerror", "typeerror", "attributeerror", "valueerror",
            "keyerror", "indexerror", "zerodivisionerror", "filenotfounderror", "permissionerror",
        )
        for ind in runtime_indicators:
            if ind in low:
                return {
                    "error_type": "runtime_error",
                    "action": f"fix_{ind}",
                    "confidence": 0.85,
                    "missing_module": None,
                }

        if "exception" in low or "error:" in low:
            return {
                "error_type": "runtime_error",
                "action": "fix_general_error",
                "confidence": 0.75,
                "missing_module": None,
            }

        return {
            "error_type": "none",
            "action": "none",
            "confidence": 0.50,
            "missing_module": None,
        }

    @classmethod
    def evaluate_proactive_check(
        cls,
        hour: int,
        silence_secs: float,
        min_silence_secs: int = 900,
        has_active_projects: bool = False,
        recent_turns_count: int = 0,
    ) -> bool:
        """
        Evaluates whether a proactive check-in is contextually appropriate.
        Suppresses unhelpful interruptions during late-night hours (1 AM - 6 AM)
        unless active projects or high engagement warrants it.
        """
        if silence_secs < min_silence_secs:
            return False

        # Sleep quiet hours (1:00 AM - 6:00 AM): only intervene if user was recently active
        if 1 <= hour < 6:
            # If user hasn't spoken in over 45 minutes late at night, they are likely asleep; don't speak
            if silence_secs > 2700:
                return False
            # If there are no active projects or recent conversations, stay quiet
            if not has_active_projects and recent_turns_count < 2:
                return False

        return True

    @classmethod
    def triage_app_launch(cls, app_name: str, command: Optional[str] = None) -> Dict[str, Any]:
        """
        Calibrated triage for app launches and terminal commands.
        Decomposes compound intents like 'open opencode in terminal' into executable CLI actions.
        """
        app_name = (app_name or "").strip()
        command = (command or "").strip()

        # If an explicit command is provided
        if command:
            term = app_name or "terminal"
            return {
                "is_terminal_command": True,
                "terminal_type": term,
                "command": command,
                "confidence": 0.99,
            }

        # Regex pattern matching for compound phrases
        # e.g., 'open opencode in terminal', 'run opencode in cmd', 'launch script in powershell'
        pattern1 = r'^(?:(?:open|run|launch|start)\s+)?(.+?)\s+(?:in|on|using)\s+(?:the\s+)?(terminal|cmd|powershell|command prompt|console)$'
        # e.g., 'terminal: opencode', 'terminal with git status', 'open terminal and run opencode'
        pattern2 = r'^(?:open\s+)?(terminal|cmd|powershell|command prompt|console)\s*(?::|\s+(?:and\s+run|with|to\s+run))\s*(.+)$'

        m1 = re.match(pattern1, app_name, re.IGNORECASE)
        if m1:
            cmd = m1.group(1).strip()
            term = m1.group(2).strip()
            return {
                "is_terminal_command": True,
                "terminal_type": term,
                "command": cmd,
                "confidence": 0.95,
            }

        m2 = re.match(pattern2, app_name, re.IGNORECASE)
        if m2:
            term = m2.group(1).strip()
            cmd = m2.group(2).strip()
            return {
                "is_terminal_command": True,
                "terminal_type": term,
                "command": cmd,
                "confidence": 0.95,
            }

        return {
            "is_terminal_command": False,
            "app_name": app_name,
            "confidence": 1.0,
        }
