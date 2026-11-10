"""
core/offline_runner.py — 100% Offline Assistant Engine for MARK L.

Powered by:
- Ollama local LLM (Qwen, Llama, Gemma, etc.)
- Ollama Phonon speech model (with local SAPI/Edge fallback)
- Local tool dispatch for OS, files, browser, and system control
- Zero cloud API fees, zero data leaving the machine.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from core.llm_client import (
    call_llm,
    ensure_ollama_running,
    get_llm_settings,
    warmup_model,
)
from core.tts import create_tts_player
from memory.memory_manager import format_memory_for_prompt, load_memory

# Action tools
from actions.browser_control import browser_control
from actions.computer_control import computer_control
from actions.computer_settings import computer_settings
from actions.desktop import desktop_control
from actions.dev_agent import dev_agent
from actions.file_controller import file_controller
from actions.open_app import open_app
from actions.reminder import reminder
from actions.system_monitor import get_system_status
from actions.weather_report import weather_action
from actions.youtube_video import youtube_video


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH = BASE_DIR / "core" / "prompt.txt"


def _format_tools_for_ollama(gemini_tools: list[dict]) -> list[dict]:
    """Convert Gemini function declarations to Ollama function tool format."""
    ollama_tools = []
    for t in gemini_tools:
        ollama_tools.append({
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": {
                    "type": "object",
                    "properties": t.get("parameters", {}).get("properties", {}),
                    "required": t.get("parameters", {}).get("required", []),
                },
            },
        })
    return ollama_tools


class JarvisOffline:
    """
    Offline local engine for MARK L.
    Communicates via Ollama + Phonon TTS without requiring internet.
    """

    def __init__(self, ui):
        self.ui = ui
        self.ui.on_text_command = self._on_text_command
        self.ui.on_interrupt = self.interrupt

        # Load config
        self._cfg = self._load_config()
        tts_cfg = {
            "tts_engine": self._cfg.get("tts_engine", "phonon"),
            "tts_voice": self._cfg.get("tts_voice", "Zoey"),
        }
        self.tts = create_tts_player(tts_cfg)

        self._messages: List[Dict[str, Any]] = []
        self._loop = None
        self._is_speaking = False
        self._lock = threading.Lock()
        self._tools = []

    def _load_config(self) -> dict:
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _load_system_prompt(self) -> str:
        prompt_txt = ""
        try:
            prompt_txt = PROMPT_PATH.read_text(encoding="utf-8")
        except Exception:
            prompt_txt = "You are JARVIS, an efficient and witty offline AI assistant."

        memory = load_memory()
        mem_str = format_memory_for_prompt(memory)
        try:
            from memory.mem0_manager import get_mem0
            mem0_ctx = get_mem0().format_memories_for_prompt(user_id=self._asst_name)
            if mem0_ctx:
                mem_str = f"{mem_str}\n{mem0_ctx}" if mem_str else mem0_ctx
        except Exception:
            pass
        now_str = datetime.now().strftime("%A, %B %d, %Y — %I:%M %p")

        parts = [
            f"[OFFLINE LOCAL MODE]\nYou are running fully offline via local Ollama.\n",
            f"[CURRENT TIME]\nRight now it is: {now_str}\n",
        ]
        if mem_str:
            parts.append(mem_str)
        parts.append(prompt_txt)
        return "\n".join(parts)

    def set_speaking(self, value: bool):
        with self._lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def interrupt(self):
        self.tts.stop()
        self.set_speaking(False)
        self.ui.write_log("SYS: Interrupted — listening...")

    def speak(self, text: str):
        if not text or not text.strip():
            return
        self.ui.write_log(f"JARVIS: {text}")

        def _do_speak():
            self.set_speaking(True)
            try:
                self.tts.speak(text)
            finally:
                self.set_speaking(False)

        threading.Thread(target=_do_speak, daemon=True).start()

    def _on_text_command(self, text: str):
        if not text or not text.strip():
            return
        self.ui.write_log(f"You: {text}")
        threading.Thread(target=self._process_turn, args=(text,), daemon=True).start()

    def _execute_tool(self, name: str, args: dict) -> str:
        self.ui.set_state("THINKING")
        print(f"[JARVIS-Offline] 🔧 {name} {args}")

        try:
            if name == "open_app":
                return open_app(parameters=args, response=None, player=self.ui) or f"Opened {args.get('app_name')}."
            elif name == "computer_settings":
                return computer_settings(parameters=args, response=None, player=self.ui) or "Done."
            elif name == "computer_control":
                return computer_control(parameters=args, player=self.ui) or "Done."
            elif name == "browser_control":
                return browser_control(parameters=args, player=self.ui) or "Done."
            elif name == "file_controller":
                return file_controller(parameters=args, player=self.ui) or "Done."
            elif name == "system_status":
                return str(get_system_status())
            elif name == "desktop_control":
                return desktop_control(parameters=args, player=self.ui) or "Done."
            elif name == "weather_report":
                return weather_action(parameters=args, player=self.ui) or "Weather delivered."
            elif name == "reminder":
                return reminder(parameters=args, response=None, player=self.ui) or "Reminder set."
            elif name == "youtube_video":
                return youtube_video(parameters=args, response=None, player=self.ui) or "Done."
            elif name == "search_memory":
                q = args.get("query", "")
                try:
                    from memory.mem0_manager import search_memory
                    results = search_memory(q, user_id=self._asst_name)
                    return ("Recalled memories:\n" + "\n".join(f"- {m}" for m in results)) if results else "No specific memory found."
                except Exception as e:
                    return f"Memory search error: {e}"
            elif name == "watch_screen":
                action = args.get("action", "").lower().strip()
                if action == "start":
                    return "In local offline mode, continuous live streaming is limited by local LLM models; please use snapshot vision when needed."
                return "Screen watching stopped."
            elif name == "dev_agent":
                return dev_agent(parameters=args, player=self.ui, speak=self.speak) or "Done."
            else:
                return f"Action '{name}' completed."
        except Exception as e:
            traceback.print_exc()
            return f"Error executing {name}: {e}"
        finally:
            if not self.ui.muted:
                self.ui.set_state("LISTENING")

    def _process_turn(self, user_text: str):
        self.ui.set_state("THINKING")
        self._messages.append({"role": "user", "content": user_text})
        try:
            from memory.mem0_manager import add_memory
            add_memory(f"User: {user_text}", user_id=self._asst_name)
        except Exception:
            pass

        try:
            # Call Ollama
            resp = call_llm(messages=self._messages, tools=self._tools)
            content = resp.get("content", "")
            tool_calls = resp.get("tool_calls", [])

            if tool_calls:
                # Add assistant turn with tool calls
                self._messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                })

                for tc in tool_calls:
                    fn = tc.get("function", {})
                    fn_name = fn.get("name", "")
                    fn_args = fn.get("arguments", {})
                    if isinstance(fn_args, str):
                        try:
                            fn_args = json.loads(fn_args)
                        except Exception:
                            fn_args = {}

                    res_str = self._execute_tool(fn_name, fn_args)
                    self._messages.append({
                        "role": "tool",
                        "content": str(res_str),
                    })

                # Follow-up generation after tool execution
                followup = call_llm(messages=self._messages, tools=self._tools)
                final_text = followup.get("content", "")
                if final_text:
                    self._messages.append({"role": "assistant", "content": final_text})
                    self.speak(final_text)
                    try:
                        from memory.mem0_manager import add_memory
                        add_memory(f"{self._asst_name}: {final_text}", user_id=self._asst_name)
                    except Exception:
                        pass
            else:
                if content:
                    self._messages.append({"role": "assistant", "content": content})
                    self.speak(content)
                    try:
                        from memory.mem0_manager import add_memory
                        add_memory(f"{self._asst_name}: {content}", user_id=self._asst_name)
                    except Exception:
                        pass

        except Exception as e:
            err_msg = f"Offline reasoning error: {e}"
            print(f"[JARVIS-Offline] ⚠️ {err_msg}")
            self.ui.write_log(f"ERR: {err_msg}")
            self.speak("Sir, I encountered an issue processing your request offline.")
        finally:
            if not self.ui.muted:
                self.ui.set_state("LISTENING")

    def run(self, reason: str = "normal"):
        """Start offline assistant."""
        self.ui.set_state("THINKING")
        self.ui.write_log("SYS: Initializing Offline Engine (Ollama + Phonon)...")

        # Import main TOOL_DECLARATIONS dynamically
        try:
            from main import TOOL_DECLARATIONS
            self._tools = _format_tools_for_ollama(TOOL_DECLARATIONS)
        except Exception:
            self._tools = []

        # Ensure Ollama is running
        if not ensure_ollama_running(timeout=10):
            self.ui.write_log("ERR: Ollama is not running. Please start Ollama.")
            self.speak("Sir, please start Ollama on your system.")
            self.ui.set_state("SLEEPING")
            return

        url, model = get_llm_settings()
        self.ui.write_log(f"SYS: Connected to local model '{model}' at {url}.")

        # Seed system prompt
        sys_prompt = self._load_system_prompt()
        self._messages = [{"role": "system", "content": sys_prompt}]

        warmup_model(sys_prompt)

        self.ui.set_state("LISTENING")
        if reason == "no_internet":
            self.ui.write_log("SYS: No internet detected — Offline Engine engaged.")
            self.speak(
                f"Hello sir, it's me again. It looks like there is no internet on this machine right now, "
                f"so I have automatically switched to our offline systems with {model}."
            )
        else:
            self.ui.write_log("SYS: JARVIS Offline Engine Ready.")
            self.speak(f"Offline systems online, sir. Running {model} with Phonon voice.")

        # Start optional offline microphone listener
        self._start_mic_listener()

    def _start_mic_listener(self):
        """Background thread for offline voice input via faster-whisper."""
        def _mic_worker():
            try:
                from core.stt import WhisperSTT
                import sounddevice as sd
                import numpy as np

                self.ui.write_log("SYS: Loading offline speech recognition...")
                # Use tiny model for fast low-latency offline transcription
                stt = WhisperSTT(model_name="tiny")
                self.ui.write_log("SYS: Offline microphone listening.")

                SAMPLE_RATE = 16000
                CHUNK_SIZE = 1024
                SILENCE_THRESHOLD = 0.015
                SILENCE_CHUNKS_TRIGGER = 18

                audio_buffer = []
                silent_chunks = 0
                recording = False

                def audio_callback(indata, frames, time_info, status):
                    nonlocal audio_buffer, silent_chunks, recording
                    if self._is_speaking or self.ui.muted:
                        return

                    audio_data = indata[:, 0]
                    energy = np.sqrt(np.mean(audio_data ** 2))

                    if energy > SILENCE_THRESHOLD:
                        recording = True
                        silent_chunks = 0
                        audio_buffer.append(audio_data.copy())
                    elif recording:
                        audio_buffer.append(audio_data.copy())
                        silent_chunks += 1

                with sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="float32",
                    blocksize=CHUNK_SIZE,
                    callback=audio_callback
                ):
                    while True:
                        time.sleep(0.1)
                        if recording and silent_chunks >= SILENCE_CHUNKS_TRIGGER:
                            if audio_buffer:
                                recorded_arr = np.concatenate(audio_buffer)
                                audio_buffer = []
                                recording = False
                                silent_chunks = 0

                                if len(recorded_arr) > SAMPLE_RATE * 0.5:
                                    try:
                                        text = stt.transcribe(recorded_arr)
                                        if text and len(text.strip()) > 1:
                                            self._on_text_command(text)
                                    except Exception as e:
                                        print(f"[STT] Offline transcribe error: {e}")
            except Exception as e:
                print(f"[STT] Offline mic disabled: {e}")
                self.ui.write_log("SYS: Offline mic listening paused (HUD text input active).")

        threading.Thread(target=_mic_worker, daemon=True).start()

