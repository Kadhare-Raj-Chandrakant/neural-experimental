"""
memory/mem0_manager.py — Intelligent Persistent Long-Term Memory using Mem0.

Provides semantic memory extraction, vector search, and long-term recall
so JARVIS never forgets user context, project details, or past messages.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()
MEM0_DIR = BASE_DIR / "memory" / "mem0_store"
MEM0_DIR.mkdir(parents=True, exist_ok=True)

# Set environment variables before any mem0 imports
os.environ["MEM0_DIR"] = str(MEM0_DIR.resolve())
os.environ["MEM0_TELEMETRY"] = "false"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _get_api_key() -> str:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("gemini_api_key", "")
    except Exception:
        return ""


def _get_active_mode() -> str:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("mode", "live")
    except Exception:
        return "live"


class Mem0Manager:
    """
    Singleton wrapper around Mem0 Memory.
    Performs background memory indexing and semantic search without blocking audio loops.
    """

    _instance: Optional["Mem0Manager"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._memory = None
        self._init_failed = False
        self._init_lock = threading.Lock()
        self._user_id = "default_user"

    @classmethod
    def get_instance(cls) -> "Mem0Manager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _ensure_initialized(self) -> bool:
        if self._memory is not None:
            return True
        if self._init_failed:
            return False

        with self._init_lock:
            if self._memory is not None:
                return True
            try:
                from mem0 import Memory

                api_key = _get_api_key()
                mode = _get_active_mode()
                qdrant_path = str((MEM0_DIR / "qdrant").resolve())

                # Set Google API key environment variables if available
                if api_key:
                    os.environ["GOOGLE_API_KEY"] = api_key
                    os.environ["GEMINI_API_KEY"] = api_key

                # Build provider config based on mode
                if mode == "offline" or not api_key:
                    # Offline Ollama configuration
                    config = {
                        "vector_store": {
                            "provider": "qdrant",
                            "config": {
                                "path": qdrant_path,
                                "on_disk": True,
                            },
                        },
                        "llm": {
                            "provider": "ollama",
                            "config": {
                                "model": "qwen2.5:7b",
                                "base_url": "http://localhost:11434",
                            },
                        },
                        "embedder": {
                            "provider": "ollama",
                            "config": {
                                "model": "qwen2.5:7b",
                                "base_url": "http://localhost:11434",
                            },
                        },
                    }
                else:
                    # Online Gemini configuration
                    config = {
                        "vector_store": {
                            "provider": "qdrant",
                            "config": {
                                "path": qdrant_path,
                                "on_disk": True,
                                "embedding_model_dims": 1536,
                            },
                        },
                        "llm": {
                            "provider": "gemini",
                            "config": {
                                "model": "gemini-2.5-flash",
                                "api_key": api_key,
                            },
                        },
                        "embedder": {
                            "provider": "gemini",
                            "config": {
                                "model": "models/gemini-embedding-001",
                                "embedding_dims": 1536,
                                "api_key": api_key,
                            },
                        },
                    }

                self._memory = Memory.from_config(config)
                print("[Mem0] Memory layer online.")
                return True
            except Exception as e:
                print(f"[Mem0] Initialization note: {e}")
                self._init_failed = True
                return False

    def add(self, text: str, metadata: Optional[Dict[str, Any]] = None, user_id: Optional[str] = None) -> None:
        """
        Record a memory or turn asynchronously in a background thread.
        Never blocks the caller.
        """
        text = (text or "").strip()
        if not text:
            return

        uid = user_id or self._user_id

        def _worker():
            try:
                if self._ensure_initialized() and self._memory:
                    self._memory.add(text, user_id=uid, metadata=metadata or {})
                    print(f"[Mem0] Memory stored: {text[:60]}...")
            except Exception as e:
                print(f"[Mem0] Failed to store memory: {e}")

        threading.Thread(target=_worker, daemon=True).start()

    def search(self, query: str, user_id: Optional[str] = None, limit: int = 5) -> List[str]:
        """
        Search memory semantically for relevant facts, tasks, and context.
        Returns a list of matching memory strings.
        """
        query = (query or "").strip()
        if not query:
            return []

        uid = user_id or self._user_id

        try:
            if self._ensure_initialized() and self._memory:
                results = self._memory.search(query, filters={"user_id": uid}, top_k=limit)
                memories: List[str] = []
                raw_items = results.get("results", []) if isinstance(results, dict) else results
                for item in raw_items:
                    if isinstance(item, dict):
                        mem_text = item.get("memory") or item.get("text") or str(item)
                        if mem_text:
                            memories.append(mem_text)
                    elif hasattr(item, "memory"):
                        memories.append(item.memory)
                    elif isinstance(item, str):
                        memories.append(item)
                return memories
        except Exception as e:
            print(f"[Mem0] Search error: {e}")

        return []

    def get_all(self, user_id: Optional[str] = None, limit: int = 20) -> List[str]:
        """Retrieve recent persistent memories."""
        uid = user_id or self._user_id
        try:
            if self._ensure_initialized() and self._memory:
                results = self._memory.get_all(filters={"user_id": uid}, top_k=limit)
                memories: List[str] = []
                raw_items = results.get("results", []) if isinstance(results, dict) else results
                for item in raw_items:
                    if isinstance(item, dict):
                        m = item.get("memory") or item.get("text")
                        if m:
                            memories.append(m)
                    elif hasattr(item, "memory"):
                        memories.append(item.memory)
                return memories
        except Exception as e:
            print(f"[Mem0] get_all error: {e}")
        return []

    def format_memories_for_prompt(self, query: Optional[str] = None, user_id: Optional[str] = None) -> str:
        """
        Returns a formatted block of relevant long-term memories for injection into system prompts.
        """
        memories = self.search(query, user_id=user_id, limit=5) if query else self.get_all(user_id=user_id, limit=8)
        if not memories:
            return ""

        lines = ["[LONG-TERM RECALLED CONTEXT (Mem0)]"]
        for m in memories:
            lines.append(f"  - {m}")
        lines.append("")
        return "\n".join(lines)


# Public helper functions
def get_mem0() -> Mem0Manager:
    return Mem0Manager.get_instance()


def add_memory(text: str, user_id: str = "default_user") -> None:
    get_mem0().add(text, user_id=user_id)


def search_memory(query: str, user_id: str = "default_user", limit: int = 5) -> List[str]:
    return get_mem0().search(query, user_id=user_id, limit=limit)
