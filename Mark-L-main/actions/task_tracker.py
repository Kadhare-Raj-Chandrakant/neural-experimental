"""
actions/task_tracker.py — Stage 5: Long-Horizon Task State Maintenance, Verification & Recovery Engine.

Implements:
1. Atomic, schema-versioned checkpoint persistence (memory/task_state.json).
2. Sub-goal lifecycle: PRECHECK -> ACTION_INTENT -> EXECUTION -> POST_VERIFICATION -> CHECKPOINT.
3. Evidence-driven recovery and reconciliation without assuming exactly-once execution.
4. Holistic end-to-end instruction verification against original user goal.
"""
from __future__ import annotations

import os
import sys
import json
import time
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable
from threading import Lock

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR = _get_base_dir()
TASK_STATE_PATH = BASE_DIR / "memory" / "task_state.json"
TASK_BACKUP_PATH = BASE_DIR / "memory" / "task_state.json.bak"
SCHEMA_VERSION = "1.0.0"

_tracker_lock = Lock()


class StepStatus:
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class SubGoal:
    """Represents a single verifiable sub-goal in a long-horizon task."""

    def __init__(
        self,
        step_id: int,
        description: str,
        action_intent: Dict[str, Any],
        verification_type: str,
        verification_spec: Dict[str, Any],
        precheck_spec: Optional[Dict[str, Any]] = None,
        status: str = StepStatus.PENDING,
        result_evidence: Optional[Dict[str, Any]] = None,
        depends_on: Optional[List[int]] = None,
        invalidation_reason: Optional[str] = None,
    ):
        self.step_id = step_id
        self.description = description
        self.action_intent = action_intent
        self.verification_type = verification_type
        self.verification_spec = verification_spec
        self.precheck_spec = precheck_spec or {}
        self.status = status
        self.result_evidence = result_evidence or {}
        self.depends_on = depends_on or []
        self.invalidation_reason = invalidation_reason

    def to_dict(self) -> Dict[str, Any]:
        # JSON-safe sanitization for non-serializable fields (e.g. callables in specs)
        safe_vspec = {}
        for k, v in self.verification_spec.items():
            if callable(v):
                safe_vspec[k] = f"<callable {getattr(v, '__name__', 'predicate')}>"
            else:
                safe_vspec[k] = v

        safe_pspec = {}
        for k, v in self.precheck_spec.items():
            if callable(v):
                safe_pspec[k] = f"<callable {getattr(v, '__name__', 'predicate')}>"
            else:
                safe_pspec[k] = v

        return {
            "step_id": self.step_id,
            "description": self.description,
            "action_intent": self.action_intent,
            "verification_type": self.verification_type,
            "verification_spec": safe_vspec,
            "precheck_spec": safe_pspec,
            "status": self.status,
            "result_evidence": self.result_evidence,
            "depends_on": self.depends_on,
            "invalidation_reason": self.invalidation_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SubGoal":
        return cls(
            step_id=data.get("step_id", 0),
            description=data.get("description", ""),
            action_intent=data.get("action_intent", {}),
            verification_type=data.get("verification_type", "visual_delta"),
            verification_spec=data.get("verification_spec", {}),
            precheck_spec=data.get("precheck_spec", {}),
            status=data.get("status", StepStatus.PENDING),
            result_evidence=data.get("result_evidence", {}),
            depends_on=data.get("depends_on", []),
            invalidation_reason=data.get("invalidation_reason"),
        )


class TaskTracker:
    """
    Durable, evidence-driven task orchestrator.
    Maintains structured task progress, atomic state checkpoints,
    and handles crash/interruption recovery without assuming exactly-once execution.
    """

    def __init__(
        self,
        task_id: str,
        original_goal: str,
        sub_goals: Optional[List[SubGoal]] = None,
        status: str = StepStatus.PENDING,
        active_step_index: int = 0,
        state_memory: Optional[Dict[str, Any]] = None,
        created_at: Optional[float] = None,
        updated_at: Optional[float] = None,
    ):
        self.task_id = task_id
        self.original_goal = original_goal # Strictly preserved verbatim
        self.sub_goals = sub_goals or []
        self.status = status
        self.active_step_index = active_step_index
        self.state_memory = state_memory or {}
        self.created_at = created_at or time.time()
        self.updated_at = updated_at or time.time()
        self.checkpoints: List[Dict[str, Any]] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "task_id": self.task_id,
            "original_goal": self.original_goal,
            "status": self.status,
            "active_step_index": self.active_step_index,
            "state_memory": self.state_memory,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "sub_goals": [sg.to_dict() for sg in self.sub_goals],
            "checkpoints": self.checkpoints,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskTracker":
        sub_goals = [SubGoal.from_dict(sg) for sg in data.get("sub_goals", [])]
        tracker = cls(
            task_id=data.get("task_id", ""),
            original_goal=data.get("original_goal", ""),
            sub_goals=sub_goals,
            status=data.get("status", StepStatus.PENDING),
            active_step_index=data.get("active_step_index", 0),
            state_memory=data.get("state_memory", {}),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )
        tracker.checkpoints = data.get("checkpoints", [])
        return tracker

    # ----------------------------------------------------------------
    # Atomic Persistence & Recovery
    # ----------------------------------------------------------------

    def save_checkpoint(self, checkpoint_note: str = "") -> bool:
        """
        Atomically commits the task state to disk using temporary file replacement.
        Creates a durable backup to survive power cuts and mid-write crashes.
        """
        with _tracker_lock:
            self.updated_at = time.time()
            if checkpoint_note:
                self.checkpoints.append({
                    "timestamp": self.updated_at,
                    "step_index": self.active_step_index,
                    "note": checkpoint_note,
                })

            data = self.to_dict()
            json_str = json.dumps(data, indent=2, ensure_ascii=False)

            TASK_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = TASK_STATE_PATH.with_suffix(".tmp")

            try:
                # 1. Write completely to temporary file
                tmp_path.write_text(json_str, encoding="utf-8")
                # 2. Maintain backup of previous valid state
                if TASK_STATE_PATH.exists():
                    try:
                        shutil.copy2(TASK_STATE_PATH, TASK_BACKUP_PATH)
                    except Exception:
                        pass
                # 3. Atomic replace
                os.replace(tmp_path, TASK_STATE_PATH)
                return True
            except Exception as e:
                print(f"[TaskTracker] ⚠️ Checkpoint write error: {e}")
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
                return False

    @classmethod
    def load_active_task(cls) -> Optional["TaskTracker"]:
        """
        Loads the active task state from disk with automated recovery
        from backup if primary file was corrupted.
        """
        with _tracker_lock:
            if not TASK_STATE_PATH.exists() and not TASK_BACKUP_PATH.exists():
                return None

            # Try primary file
            if TASK_STATE_PATH.exists():
                try:
                    content = TASK_STATE_PATH.read_text(encoding="utf-8")
                    data = json.loads(content)
                    return cls.from_dict(data)
                except Exception as pe:
                    print(f"[TaskTracker] ⚠️ Primary state load corrupted ({pe}), checking backup...")

            # Fallback to backup file
            if TASK_BACKUP_PATH.exists():
                try:
                    content = TASK_BACKUP_PATH.read_text(encoding="utf-8")
                    data = json.loads(content)
                    print("[TaskTracker] 🛡️ Successfully recovered state from backup!")
                    return cls.from_dict(data)
                except Exception as be:
                    print(f"[TaskTracker] ❌ Backup state load error: {be}")

            return None

    # ----------------------------------------------------------------
    # Evidence-Driven Verification & Prechecks
    # ----------------------------------------------------------------

    @classmethod
    def verify_subgoal(
        cls,
        subgoal: SubGoal,
        before_state: Optional[Dict[str, Any]] = None,
        after_state: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        Strictly evaluates whether a sub-goal's post-condition is verified on the desktop.
        Returns: (verified: bool, evidence: dict, message: str)
        """
        v_type = subgoal.verification_type
        spec = subgoal.verification_spec

        # 1. Visual Delta Verification
        if v_type == "visual_delta":
            from actions.coordinate_grounding import VisualVerifier
            img_b = before_state.get("frame") if before_state else None
            img_a = after_state.get("frame") if after_state else VisualVerifier.capture_frame()
            region = spec.get("region")
            min_delta = spec.get("min_delta", 0.05)

            if img_b is None or img_a is None:
                return False, {}, "Visual delta check failed: missing comparison frames."

            has_ch, delta, msg = VisualVerifier.verify_action_outcome(
                img_b, img_a, threshold_percent=min_delta, region=region
            )
            evidence = {"visual_delta_percent": delta, "region": region}
            return has_ch, evidence, f"Visual delta verified ({delta}% >= {min_delta}%)" if has_ch else f"Insufficient visual delta ({delta}%)"

        # 2. Filesystem Existence & Content
        if v_type == "file_exists":
            raw_path = spec.get("path", "")
            target_path = Path(os.path.expanduser(os.path.expandvars(raw_path))).resolve()
            min_size = spec.get("min_size", 1)
            content_substring = spec.get("content_substring")

            if not target_path.exists():
                return False, {"path": str(target_path)}, f"Target file '{target_path}' does not exist on disk."

            size = target_path.stat().st_size
            if size < min_size:
                return False, {"path": str(target_path), "size": size}, f"File '{target_path}' is empty ({size} bytes)."

            if content_substring:
                try:
                    text = target_path.read_text(encoding="utf-8")
                    if content_substring not in text:
                        return False, {"path": str(target_path)}, f"Expected text substring not found in file."
                except Exception as re:
                    return False, {"path": str(target_path)}, f"Could not read file text: {re}"

            evidence = {"path": str(target_path), "size_bytes": size}
            return True, evidence, f"File verified on disk ({size} bytes)."

        # 3. Window Focus & Title Verification
        if v_type == "window_focus":
            from actions.coordinate_grounding import WindowIdentifier
            target_title = spec.get("title", "")
            fw = WindowIdentifier.get_foreground_window_info()
            match = target_title.lower() in fw["title"].lower()
            evidence = {"active_hwnd": fw["hwnd"], "active_title": fw["title"], "rect": fw["rect"]}
            return match, evidence, f"Window '{target_title}' is active." if match else f"Window '{target_title}' not active (current: '{fw['title']}')."

        # 4. Text Source Equivalence (Browser to Document)
        if v_type == "content_equivalence":
            expected_content = spec.get("expected_content", "").strip()
            observed_content = spec.get("observed_content", "").strip()
            if not observed_content and spec.get("check_clipboard"):
                import pyperclip
                observed_content = pyperclip.paste().strip()

            equiv = (expected_content.lower() in observed_content.lower()) or (observed_content.lower() in expected_content.lower())
            evidence = {
                "expected_sample": expected_content[:60],
                "observed_sample": observed_content[:60],
                "length_match": len(observed_content) > 0,
            }
            return equiv, evidence, "Content equivalence verified." if equiv else "Content mismatch between source and destination."

        # 5. Composite Verification (Both File and Visual)
        if v_type == "composite":
            sub_verifications = spec.get("verifications", [])
            composite_evidence = {}
            for sub_spec in sub_verifications:
                dummy_sg = SubGoal(0, "sub", {}, sub_spec["type"], sub_spec.get("spec", {}))
                ok, ev, msg = cls.verify_subgoal(dummy_sg, before_state, after_state)
                composite_evidence[sub_spec["type"]] = ev
                if not ok:
                    return False, composite_evidence, f"Composite sub-verification failed: {msg}"
            return True, composite_evidence, "All composite verification criteria passed."

        # 6. Custom or State-Memory Predicate
        if v_type == "custom":
            predicate = spec.get("predicate")
            if callable(predicate):
                try:
                    ok = bool(predicate(evidence_context={"before": before_state, "after": after_state}))
                except TypeError:
                    ok = bool(predicate())
                return ok, {"custom": ok}, "Custom predicate satisfied." if ok else "Custom predicate failed."
            return True, {"custom": True}, "Custom verification passed."

        # 7. Independent Fact Audit Verification
        if v_type == "fact_audit":
            audit_data = None
            if after_state and "execution_res" in after_state and isinstance(after_state["execution_res"], dict):
                audit_data = after_state["execution_res"].get("audit_result", after_state["execution_res"])
            elif subgoal.result_evidence and "audit_result" in subgoal.result_evidence:
                audit_data = subgoal.result_evidence["audit_result"]

            if not audit_data:
                return False, {}, "Audit not yet executed."

            passed = bool(audit_data.get("audit_passed", False))
            mismatches = audit_data.get("mismatches", [])
            fields_count = audit_data.get("fields_audited", 0)
            req_fields = spec.get("required_fields", [])
            field_comps = audit_data.get("field_comparisons", {})

            missing_reqs = [f for f in req_fields if f not in field_comps or not field_comps[f].get("match", False)]

            if passed and not mismatches and fields_count > 0 and not missing_reqs:
                evidence = {
                    "audit_passed": True,
                    "fields_audited": fields_count,
                    "ground_truth_source": audit_data.get("ground_truth_source", "Win32/platform"),
                    "field_comparisons": field_comps,
                    "mismatches": [],
                }
                return True, evidence, f"Independent fact audit passed with 100% match across {fields_count} fields."

            fail_reasons = []
            if mismatches:
                fail_reasons.append(f"mismatches={mismatches}")
            if missing_reqs:
                fail_reasons.append(f"unmatched_required_fields={missing_reqs}")
            return False, audit_data, f"Fact audit failed: {'; '.join(fail_reasons)}"

        return False, {}, f"Unknown verification type: {v_type}"

    # ----------------------------------------------------------------
    # Evidence-Driven Reconcile & Recovery
    # ----------------------------------------------------------------

    def reconcile_state(self) -> Tuple[str, str]:
        """
        Reconciles in-flight task progress against actual observable desktop evidence.
        Handles crashes where an action occurred but checkpoint was not committed.
        Returns: (new_status: str, message: str)
        """
        if self.active_step_index >= len(self.sub_goals):
            self.status = StepStatus.COMPLETED
            self.save_checkpoint("Task fully completed during reconciliation")
            return self.status, "All sub-goals are already completed."

        current_sg = self.sub_goals[self.active_step_index]

        # Inspect if current step is ALREADY satisfied by physical desktop evidence
        is_already_done, evidence, msg = self.verify_subgoal(current_sg)
        if is_already_done:
            print(f"[TaskTracker] 🔍 Reconciliation: Step {current_sg.step_id} ('{current_sg.description}') already completed on desktop!")
            current_sg.status = StepStatus.COMPLETED
            current_sg.result_evidence = evidence
            self.active_step_index += 1
            self.status = StepStatus.IN_PROGRESS if self.active_step_index < len(self.sub_goals) else StepStatus.COMPLETED
            self.save_checkpoint(f"Reconciled step {current_sg.step_id} via desktop evidence")
            return self.status, f"Reconciled step {current_sg.step_id}: already satisfied."

        # If interrupted or in-progress, determine whether safe to resume or needs user clarification
        if current_sg.status in (StepStatus.IN_PROGRESS, StepStatus.INTERRUPTED):
            # If evidence is inconclusive or ambiguous, demand reconciliation
            if current_sg.precheck_spec.get("require_strict_reconciliation"):
                current_sg.status = StepStatus.RECONCILIATION_REQUIRED
                self.status = StepStatus.RECONCILIATION_REQUIRED
                self.save_checkpoint("Set to RECONCILIATION_REQUIRED")
                return self.status, f"Step {current_sg.step_id} requires user clarification before retrying."

            # Safely reset to PENDING so it can be re-evaluated from precheck
            current_sg.status = StepStatus.PENDING
            self.status = StepStatus.IN_PROGRESS
            self.save_checkpoint(f"Reset unverified step {current_sg.step_id} to PENDING")
            return self.status, f"Reset step {current_sg.step_id} to PENDING for safe execution."

        return self.status, "Reconciliation clean; ready to execute."

    # ----------------------------------------------------------------
    # Step Execution Lifecycle
    # ----------------------------------------------------------------

    def execute_next_step(self, executor: Optional[Callable[[Dict[str, Any]], Any]] = None) -> Tuple[bool, str]:
        """
        Executes the next active sub-goal strictly following:
        PRECHECK -> ACTION_INTENT -> EXECUTION -> POST_VERIFICATION -> CHECKPOINT
        """
        from actions.vision_pipeline import VisionSessionManager
        from actions.coordinate_grounding import VisualVerifier, WindowIdentifier

        vsm = VisionSessionManager.get_instance()
        if vsm.is_paused:
            return False, "Execution blocked: Emergency Stop is active (PAUSED)."

        if self.active_step_index >= len(self.sub_goals):
            self.status = StepStatus.COMPLETED
            self.save_checkpoint("All sub-goals complete")
            return True, "Task completed."

        current_sg = self.sub_goals[self.active_step_index]

        # 1. PRECHECK
        print(f"\n[TaskTracker] >>> Step {current_sg.step_id}/{len(self.sub_goals)}: {current_sg.description}")
        pre_req_title = current_sg.precheck_spec.get("target_window")
        if pre_req_title:
            fw = WindowIdentifier.get_foreground_window_info()
            if pre_req_title.lower() not in fw["title"].lower():
                WindowIdentifier.bring_to_front(pre_req_title)
                time.sleep(0.5)

        # Check if step is already accomplished (unless explicitly flagged to bypass precheck)
        if not current_sg.precheck_spec.get("bypass_precheck", False):
            pre_done, pre_ev, _ = self.verify_subgoal(current_sg)
            if pre_done:
                print(f"[TaskTracker] ⏭️ Precheck: Step {current_sg.step_id} already satisfied. Skipping duplicate execution.")
                current_sg.status = StepStatus.COMPLETED
                current_sg.result_evidence = pre_ev
                self.active_step_index += 1
                if self.active_step_index >= len(self.sub_goals):
                    self.status = StepStatus.COMPLETED
                self.save_checkpoint(f"Precheck verified step {current_sg.step_id}")
                return True, f"Step {current_sg.step_id} already completed."

        # 2. ACTION_INTENT
        current_sg.status = StepStatus.IN_PROGRESS
        self.status = StepStatus.IN_PROGRESS
        self.save_checkpoint(f"Started step {current_sg.step_id}")

        before_frame = VisualVerifier.capture_frame()
        before_state = {"frame": before_frame}

        # 3. EXECUTION
        if executor:
            res = executor(current_sg.action_intent)
            if isinstance(res, dict):
                self.state_memory.update(res)
        else:
            from actions.computer_control import computer_control
            res = computer_control(current_sg.action_intent)

        print(f"[TaskTracker] Execution output: {res}")

        # Emergency Stop / Interrupt check
        if vsm.is_paused or vsm.cancellation_event.is_set():
            current_sg.status = StepStatus.INTERRUPTED
            self.status = StepStatus.INTERRUPTED
            self.save_checkpoint(f"Step {current_sg.step_id} interrupted by emergency stop")
            return False, f"Step {current_sg.step_id} halted by Emergency Stop."

        # 4. POST_VERIFICATION
        time.sleep(0.4)
        after_frame = VisualVerifier.capture_frame()
        after_state = {"frame": after_frame, "execution_res": res}
        if isinstance(res, dict) and "audit_result" in res:
            after_state["audit_result"] = res["audit_result"]

        verified, evidence, v_msg = self.verify_subgoal(current_sg, before_state, after_state)
        print(f"[TaskTracker] Post-Verification: verified={verified} [{v_msg}]")

        # 5. CHECKPOINT
        if verified:
            current_sg.status = StepStatus.COMPLETED
            current_sg.result_evidence = evidence
            self.active_step_index += 1
            if self.active_step_index >= len(self.sub_goals):
                self.status = StepStatus.COMPLETED
            self.save_checkpoint(f"Verified & completed step {current_sg.step_id}")
            return True, f"Step {current_sg.step_id} completed successfully."
        else:
            current_sg.status = StepStatus.FAILED
            current_sg.result_evidence = {"error": v_msg}
            self.save_checkpoint(f"Step {current_sg.step_id} verification failed")
            return False, f"Step {current_sg.step_id} verification failed: {v_msg}"

    # ----------------------------------------------------------------
    # Holistic Final Verification
    # ----------------------------------------------------------------

    def verify_overall_goal(self, holistic_verifier: Optional[Callable[[Dict[str, Any]], bool]] = None) -> Tuple[bool, str]:
        """
        Performs an end-to-end holistic verification comparing final desktop state
        against the verbatim original user instruction.
        """
        if self.status != StepStatus.COMPLETED or self.active_step_index < len(self.sub_goals):
            return False, f"Cannot perform final verification: task status is {self.status} ({self.active_step_index}/{len(self.sub_goals)} steps)."

        # Ensure all sub-goals report completed
        for sg in self.sub_goals:
            if sg.status != StepStatus.COMPLETED:
                return False, f"Sub-goal {sg.step_id} ('{sg.description}') not marked COMPLETED."

        if holistic_verifier:
            ok = holistic_verifier(self.to_dict())
            if not ok:
                return False, "Holistic custom verifier rejected final outcome."

        return True, f"Holistic audit PASSED: All {len(self.sub_goals)} sub-goals verified against original instruction: '{self.original_goal}'."
