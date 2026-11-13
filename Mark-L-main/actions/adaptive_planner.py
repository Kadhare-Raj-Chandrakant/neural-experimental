"""
actions/adaptive_planner.py — Stage 6: Contextual Intelligence, Dependency Awareness & Adaptive Replanning.

Implements:
1. Task-scoped DesktopRelationshipGraph (windows, files, roles, freshness, provenance).
2. DependencyGraph (DAG representation, granular downstream invalidation).
3. GoalInvarianceGate (anti-drift, non-destructive safety, side-effect prevention).
4. AdaptiveReplanner (bounded replanning, explicit reason logging, safe pause).
"""
from __future__ import annotations

import time
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from actions.task_tracker import SubGoal, StepStatus


class ContextEntity:
    """Represents an observed entity in the task-scoped desktop relationship graph."""

    def __init__(
        self,
        entity_id: str,
        entity_type: str, # "window" | "file" | "process" | "app"
        role: str,        # "drafting_workspace" | "source_research" | "artifact_destination" | "ambient"
        is_task_relevant: bool,
        metadata: Dict[str, Any],
        provenance: str,  # "live_win32_query" | "filesystem_stat" | "visual_grounding"
        timestamp: Optional[float] = None,
    ):
        self.entity_id = entity_id
        self.entity_type = entity_type
        self.role = role
        self.is_task_relevant = is_task_relevant
        self.metadata = metadata
        self.provenance = provenance
        self.timestamp = timestamp or time.time()

    def is_stale(self, max_age_seconds: float = 10.0) -> bool:
        return (time.time() - self.timestamp) > max_age_seconds

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "role": self.role,
            "is_task_relevant": self.is_task_relevant,
            "metadata": self.metadata,
            "provenance": self.provenance,
            "timestamp": self.timestamp,
        }


class DesktopRelationshipGraph:
    """
    Task-scoped contextual knowledge graph.
    Models relationships between applications, active windows, files, and the user's objective.
    Does NOT treat ambient or unrelated desktop windows as task-relevant.
    """

    def __init__(self, task_goal: str):
        self.task_goal = task_goal
        self.entities: Dict[str, ContextEntity] = {}

    def register_window(
        self,
        hwnd: int,
        title: str,
        role: str,
        is_task_relevant: bool,
        metadata: Optional[Dict[str, Any]] = None,
        provenance: str = "live_win32_query",
    ) -> ContextEntity:
        entity_id = f"window_{hwnd}"
        meta = metadata or {}
        meta.update({"hwnd": hwnd, "title": title})
        entity = ContextEntity(
            entity_id=entity_id,
            entity_type="window",
            role=role,
            is_task_relevant=is_task_relevant,
            metadata=meta,
            provenance=provenance,
        )
        self.entities[entity_id] = entity
        return entity

    def register_file(
        self,
        path: str,
        role: str,
        is_task_relevant: bool,
        metadata: Optional[Dict[str, Any]] = None,
        provenance: str = "filesystem_stat",
    ) -> ContextEntity:
        norm_path = str(Path(os.path.expanduser(os.path.expandvars(path))).resolve())
        entity_id = f"file_{norm_path}"
        meta = metadata or {}
        meta["path"] = norm_path
        entity = ContextEntity(
            entity_id=entity_id,
            entity_type="file",
            role=role,
            is_task_relevant=is_task_relevant,
            metadata=meta,
            provenance=provenance,
        )
        self.entities[entity_id] = entity
        return entity

    def get_task_relevant_entities(self) -> List[ContextEntity]:
        """Returns only fresh, task-scoped entities relevant to the active objective."""
        return [e for e in self.entities.values() if e.is_task_relevant and not e.is_stale()]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_goal": self.task_goal,
            "entities": {eid: e.to_dict() for eid, e in self.entities.items()},
        }


class DependencyGraph:
    """
    Directed Acyclic Graph (DAG) for dependency-aware task execution.
    Preserves independent progress when a step is adapted or invalidated.
    """

    def __init__(self, sub_goals: Optional[List[SubGoal]] = None):
        self.nodes: Dict[int, SubGoal] = {}
        if sub_goals:
            for sg in sub_goals:
                self.nodes[sg.step_id] = sg

    def add_node(self, subgoal: SubGoal) -> None:
        self.nodes[subgoal.step_id] = subgoal

    def get_node(self, step_id: int) -> Optional[SubGoal]:
        return self.nodes.get(step_id)

    def get_all_nodes(self) -> List[SubGoal]:
        # Return sorted by step_id
        return sorted(self.nodes.values(), key=lambda n: n.step_id)

    def get_ready_nodes(self) -> List[SubGoal]:
        """
        Returns nodes that are ready for execution:
        Status is PENDING and all declared dependencies are COMPLETED.
        """
        ready = []
        for sg in self.get_all_nodes():
            if sg.status != StepStatus.PENDING:
                continue

            deps_satisfied = True
            for dep_id in sg.depends_on:
                dep_node = self.nodes.get(dep_id)
                if not dep_node or dep_node.status != StepStatus.COMPLETED:
                    deps_satisfied = False
                    break

            if deps_satisfied:
                ready.append(sg)

        return ready

    def get_downstream_dependents(self, step_id: int) -> Set[int]:
        """Transitively finds all node IDs that directly or indirectly depend on step_id."""
        dependents = set()
        queue = [step_id]
        while queue:
            curr = queue.pop(0)
            for sg in self.nodes.values():
                if curr in sg.depends_on and sg.step_id not in dependents:
                    dependents.add(sg.step_id)
                    queue.append(sg.step_id)
        return dependents

    def invalidate_downstream(self, failed_step_id: int, reason: str) -> List[int]:
        """
        Invalidates ONLY downstream dependents whose assumptions are affected.
        Preserves all completed independent steps!
        Returns list of invalidated node IDs.
        """
        affected_ids = self.get_downstream_dependents(failed_step_id)
        invalidated = []
        for sid in affected_ids:
            node = self.nodes[sid]
            if node.status in (StepStatus.COMPLETED, StepStatus.IN_PROGRESS):
                node.status = StepStatus.PENDING
                node.invalidation_reason = f"Upstream step {failed_step_id} adapted: {reason}"
                invalidated.append(sid)

        return invalidated


class GoalInvarianceGate:
    """
    Enforces the fundamental Stage 6 safety rule:
    'JARVIS may change its plan to accomplish the user's objective.
     It may never quietly change the objective to accommodate its plan.'

    Evaluates:
    - Direct Objective Alignment: Does action advance the user's goal?
    - Side Effect Detection: Does it introduce unrequested operations?
    - Destructive Action Prevention: Rejects file deletion, overwriting, killing unrelated apps.
    - Destination Invariance: Preserves requested deliverable paths and outputs.
    """

    DESTRUCTIVE_ACTIONS = {"delete", "delete_file", "rmdir", "remove", "unlink", "kill", "format"}

    @classmethod
    def evaluate_action_intent(
        cls,
        original_goal: str,
        action_intent: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, str]:
        """
        Evaluates a proposed action or adaptation.
        Returns: (is_allowed: bool, decision: "ALLOW" | "BLOCK" | "REQUIRE_USER_CONFIRMATION", reason: str)
        """
        action = action_intent.get("action", "").lower().strip()
        goal_lower = original_goal.lower()

        # 1. Destructive Action Check
        if any(d in action for d in cls.DESTRUCTIVE_ACTIONS):
            # Check if user explicitly asked for deletion in original goal
            if not any(kw in goal_lower for kw in ("delete", "remove", "clean up", "purge", "trash")):
                return (
                    False,
                    "BLOCK",
                    f"Goal Invariance Gate BLOCKED destructive action '{action}': "
                    f"Action destroys data not requested by user goal ('{original_goal}')."
                )

        # 2. File Overwrite / Collision Check
        target_path = action_intent.get("path") or action_intent.get("target_file")
        if target_path:
            p = Path(os.path.expanduser(os.path.expandvars(str(target_path)))).resolve()
            # If attempting to write to existing file without user overwrite request
            if p.exists() and action_intent.get("overwrite") and not any(kw in goal_lower for kw in ("overwrite", "replace", "update")):
                return (
                    False,
                    "REQUIRE_USER_CONFIRMATION",
                    f"Goal Invariance Gate flagged file overwrite on '{p}': "
                    f"File exists and original goal did not request replacement. User confirmation required."
                )

        # 3. Unrequested Side Effect Check (closing unrelated windows or processes)
        if action in ("close_app", "terminate_process"):
            app_to_close = action_intent.get("app_name", "").lower()
            if app_to_close and app_to_close not in goal_lower:
                return (
                    False,
                    "BLOCK",
                    f"Goal Invariance Gate BLOCKED closing unrequested application '{app_to_close}'."
                )

        # 4. Scope Alteration / Objective Alteration Check
        # Check if deliverable path is being changed outside user intent
        requested_destination = action_intent.get("destination")
        if requested_destination and context:
            expected_dest = context.get("expected_destination")
            if expected_dest and str(Path(requested_destination).resolve()) != str(Path(expected_dest).resolve()):
                return (
                    False,
                    "REQUIRE_USER_CONFIRMATION",
                    f"Goal Invariance Gate flagged altered destination: "
                    f"Expected '{expected_dest}', proposed '{requested_destination}'. Confirmation required."
                )

        return True, "ALLOW", "Action preserves original user objective and is non-destructive."


class AdaptiveReplanner:
    """
    Bounded adaptive planner.
    Intercepts environmental discrepancies and formulates safe,
    dependency-aware adaptations validated by GoalInvarianceGate.
    """

    def __init__(self, max_replans: int = 3):
        self.max_replans = max_replans
        self.replan_count = 0
        self.replan_history: List[Dict[str, Any]] = []

    def can_replan(self) -> bool:
        return self.replan_count < self.max_replans

    def adapt_for_file_collision(
        self,
        dep_graph: DependencyGraph,
        colliding_step_id: int,
        colliding_file: Path,
        original_goal: str,
    ) -> Tuple[bool, Optional[Path], str]:
        """
        Safely adapts a sub-goal when target file already exists.
        Finds a non-colliding `_v2` / `_v3` alternative path, verifies it does not exist,
        validates with GoalInvarianceGate, and updates the DAG.
        """
        if not self.can_replan():
            return False, None, f"Max replan limit ({self.max_replans}) reached. Safe pause required."

        # Find clean alternative name
        stem = colliding_file.stem
        suffix = colliding_file.suffix
        parent = colliding_file.parent

        counter = 2
        alt_path = parent / f"{stem}_v{counter}{suffix}"
        while alt_path.exists():
            counter += 1
            alt_path = parent / f"{stem}_v{counter}{suffix}"

        # Validate with GoalInvarianceGate
        proposed_intent = {
            "action": "save_file",
            "path": str(alt_path),
            "destination": str(alt_path),
            "collision_avoidance": True
        }
        allowed, decision, reason = GoalInvarianceGate.evaluate_action_intent(
            original_goal, proposed_intent
        )

        if not allowed:
            return False, None, f"Adaptive replan rejected by GoalInvarianceGate: {reason}"

        # Apply adaptation to DAG node
        node = dep_graph.get_node(colliding_step_id)
        if not node:
            return False, None, f"Step ID {colliding_step_id} not found in dependency graph."

        old_path = node.action_intent.get("path")
        node.action_intent["path"] = str(alt_path)
        if "path" in node.verification_spec:
            node.verification_spec["path"] = str(alt_path)

        self.replan_count += 1
        replan_record = {
            "replan_index": self.replan_count,
            "timestamp": time.time(),
            "step_id": colliding_step_id,
            "trigger": f"File collision on '{colliding_file}'",
            "adaptation": f"Rerouted to non-colliding path '{alt_path}'",
            "downstream_invalidated": dep_graph.invalidate_downstream(colliding_step_id, "File destination adapted"),
        }
        self.replan_history.append(replan_record)

        print(f"[AdaptiveReplanner] 🔄 Adapted Step {colliding_step_id}: '{old_path}' → '{alt_path}' (Replan #{self.replan_count})")
        return True, alt_path, f"Adapted to non-colliding destination '{alt_path.name}'"

    def adapt_for_missing_prerequisite(
        self,
        dep_graph: DependencyGraph,
        failing_step_id: int,
        missing_dir: Path,
        original_goal: str,
    ) -> Tuple[bool, Optional[SubGoal], str]:
        """
        Safely injects a prerequisite adaptation (e.g. creating a missing directory)
        into the DAG without modifying unrelated independent nodes.
        """
        if not self.can_replan():
            return False, None, f"Max replan limit ({self.max_replans}) reached. Safe pause required."

        proposed_intent = {
            "action": "create_directory",
            "path": str(missing_dir),
            "directory": str(missing_dir),
        }
        allowed, decision, reason = GoalInvarianceGate.evaluate_action_intent(
            original_goal, proposed_intent
        )
        if not allowed:
            return False, None, f"Prerequisite adaptation rejected by GoalInvarianceGate: {reason}"

        # Create new adaptation sub-goal with ID between existing nodes
        new_step_id = failing_step_id * 100 + 1
        adaptation_node = SubGoal(
            step_id=new_step_id,
            description=f"Auto-create missing prerequisite directory '{missing_dir.name}'",
            action_intent=proposed_intent,
            verification_type="file_exists",
            verification_spec={"path": str(missing_dir), "min_size": 0},
            depends_on=[],
        )

        dep_graph.add_node(adaptation_node)

        # Make failing step depend on this new adaptation node
        target_node = dep_graph.get_node(failing_step_id)
        if target_node:
            if new_step_id not in target_node.depends_on:
                target_node.depends_on.append(new_step_id)

        self.replan_count += 1
        replan_record = {
            "replan_index": self.replan_count,
            "timestamp": time.time(),
            "step_id": failing_step_id,
            "trigger": f"Missing directory '{missing_dir}'",
            "adaptation": f"Injected adaptation node {new_step_id} to create directory",
            "downstream_invalidated": dep_graph.invalidate_downstream(failing_step_id, "Prerequisite directory injected"),
        }
        self.replan_history.append(replan_record)

        print(f"[AdaptiveReplanner] ➕ Injected Adaptation Step {new_step_id} to create '{missing_dir}' (Replan #{self.replan_count})")
        return True, adaptation_node, f"Injected step {new_step_id} to create missing directory."

