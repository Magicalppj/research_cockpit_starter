from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import shutil
import unittest
import uuid
from datetime import date
from pathlib import Path
import os
import sys
from unittest.mock import patch
import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT_DIR
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.interaction_log import (
    InteractionLogError,
    recent_interactions,
    validate_interaction_append_target,
    validate_interaction_log,
)
from research_cockpit.model import (
    ValidationError,
    build_action_suggestions,
    build_agent_context,
    build_branch_comparison,
    build_decision_acceptance_checklist,
    build_decision_evidence_bundle,
    build_decision_evidence_summary,
    build_decision_trace,
    build_focus_context,
    build_experiment_matrix,
    build_link_rows,
    build_node_onboarding_context,
    build_option_subtree,
    build_option_workstream_context,
    build_option_workstream_rows,
    build_search_index,
    build_search_index_summary,
    build_context_metadata,
    build_suggestion_lifecycle_rows,
    build_suggestion_lifecycle_summary,
    derive_focus_path,
    graph_to_json,
    append_interaction_log,
    graph_view_id_from_title,
    load_agents,
    load_assignments,
    load_coordinator_state,
    load_graph_views,
    load_interaction_log,
    load_explicit_edges,
    load_runs,
    load_yaml,
    load_nodes,
    node_context,
    save_yaml,
    search_knowledge,
    unique_strings,
    upsert_graph_view,
    validate_cockpit,
)
from research_cockpit.graph_core import GraphTopology
from research_cockpit.lifecycle_guards import active_descendant_blockers
from research_cockpit.types import ResearchNode


def write_node(root: Path, data: dict) -> None:
    save_yaml(root / "graph" / "nodes" / f"{data['id']}.yaml", data)


class ModelValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_parent = ROOT_DIR / ".test_tmp"
        temp_parent.mkdir(exist_ok=True)
        self.root = temp_parent / f"model_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)
        write_node(
            self.root,
            {
                "id": "stage_text",
                "type": "stage",
                "title": "Text",
                "status": "active",
                "children": ["problem_text"],
            },
        )
        write_node(
            self.root,
            {
                "id": "problem_text",
                "type": "problem",
                "title": "Weak text",
                "status": "active",
                "parent": "stage_text",
                "children": ["option_t5"],
                "priority": "high",
            },
        )
        write_node(
            self.root,
            {
                "id": "option_t5",
                "type": "option",
                "title": "T5",
                "status": "active",
                "parent": "problem_text",
                "children": ["exp_t5", "decision_t5"],
            },
        )
        write_node(
            self.root,
            {
                "id": "exp_t5",
                "type": "experiment",
                "title": "T5 ablation",
                "status": "planned",
                "parent": "option_t5",
                "dataset": "dataset_v1",
                "backbone": "ltx",
            },
        )
        write_node(
            self.root,
            {
                "id": "decision_t5",
                "type": "decision",
                "title": "Use T5",
                "status": "proposed",
                "parent": "option_t5",
                "summary": "T5 is promising.",
            },
        )
        save_yaml(
            self.root / "current_state.yaml",
            {
                "current_stage": "stage_text",
                "current_problem": "problem_text",
                "current_option": "option_t5",
                "current_focus_path": ["stage_text", "problem_text", "option_t5"],
                "current_hypothesis": "T5 helps.",
                "open_risks": ["Need cache parity"],
                "next_actions": ["Run ablation"],
            },
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_load_yaml_rejects_unsafe_python_tags(self) -> None:
        unsafe = self.root / "unsafe.yaml"
        unsafe.write_text("value: !!python/object/apply:os.system ['echo unsafe']\n", encoding="utf-8")

        with self.assertRaises(yaml.YAMLError):
            load_yaml(unsafe)

    def test_load_yaml_safe_loader_fallback_rejects_unsafe_python_tags(self) -> None:
        import research_cockpit.storage as storage

        unsafe = self.root / "unsafe_fallback.yaml"
        unsafe.write_text("value: !!python/object/apply:os.system ['echo unsafe']\n", encoding="utf-8")

        with (
            patch.object(storage, "_SAFE_LOADER", yaml.SafeLoader),
            patch.object(storage, "_SAFE_DUMPER", yaml.SafeDumper),
        ):
            with self.assertRaises(yaml.YAMLError):
                load_yaml(unsafe)
            fallback = self.root / "fallback.yaml"
            save_yaml(fallback, {"name": "T5", "items": ["alpha", "beta"]})

            self.assertEqual(load_yaml(fallback), {"name": "T5", "items": ["alpha", "beta"]})

    def test_save_yaml_temp_file_is_not_truth_discoverable(self) -> None:
        target = self.root / "agents" / "agent_temp.yaml"
        save_yaml(target, {"agent_id": "agent_temp", "status": "idle"})
        visible_during_publish: list[str] = []
        original_replace = Path.replace

        def inspect_then_replace(source: Path, destination: Path) -> Path:
            visible_during_publish.extend(
                path.name for path in source.parent.glob("*.yaml")
            )
            return original_replace(source, destination)

        with patch.object(Path, "replace", new=inspect_then_replace):
            save_yaml(target, {"agent_id": "agent_temp", "status": "active"})

        self.assertEqual(visible_during_publish, [target.name])

    def test_valid_sample_cockpit_passes_validation(self) -> None:
        nodes = load_nodes(self.root)

        errors = validate_cockpit(self.root, nodes)

        self.assertEqual(errors, [])

    def test_invalid_status_reports_node_id(self) -> None:
        write_node(
            self.root,
            {
                "id": "bad_problem",
                "type": "problem",
                "title": "Bad",
                "status": "done",
            },
        )
        nodes = load_nodes(self.root)

        with self.assertRaises(ValidationError) as ctx:
            validate_cockpit(self.root, nodes, raise_on_error=True)

        self.assertIn("bad_problem", str(ctx.exception))
        self.assertIn("invalid status", str(ctx.exception))

    def test_run_records_are_loaded_and_validated(self) -> None:
        save_yaml(
            self.root / "runs" / "run_t5_smoke.yaml",
            {
                "run_id": "run_t5_smoke",
                "status": "running",
                "experiment_id": "exp_t5",
                "started_at": "2026-05-27T09:00:00Z",
                "launcher": "tmux",
                "command": "python train.py --smoke",
                "tmux_session": "t5-smoke",
                "pid": 1234,
                "log_root": "artifacts/exp_t5/run_t5_smoke/logs",
                "output_root": "artifacts/exp_t5/run_t5_smoke",
                "monitor_command": "tail -f artifacts/exp_t5/run_t5_smoke/logs/run.log",
                "stop_command": "tmux kill-session -t t5-smoke",
                "progress_file": "artifacts/exp_t5/run_t5_smoke/progress.json",
                "config_file": "configs/exp_t5_smoke.yaml",
            },
        )

        runs = load_runs(self.root)
        errors = validate_cockpit(self.root, load_nodes(self.root))

        self.assertEqual(errors, [])
        self.assertEqual(runs["run_t5_smoke"].experiment_id, "exp_t5")
        self.assertEqual(runs["run_t5_smoke"].launcher, "tmux")
        self.assertEqual(runs["run_t5_smoke"].command, "python train.py --smoke")
        self.assertEqual(runs["run_t5_smoke"].tmux_session, "t5-smoke")
        self.assertEqual(runs["run_t5_smoke"].pid, 1234)
        self.assertEqual(runs["run_t5_smoke"].progress_file, "artifacts/exp_t5/run_t5_smoke/progress.json")

    def test_run_records_accept_all_lifecycle_statuses(self) -> None:
        for status in ("queued", "running", "completed", "failed", "cancelled"):
            with self.subTest(status=status):
                save_yaml(
                    self.root / "runs" / "run_t5_lifecycle.yaml",
                    {
                        "run_id": "run_t5_lifecycle",
                        "status": status,
                        "experiment_id": "exp_t5",
                    },
                )

                errors = validate_cockpit(self.root, load_nodes(self.root))

                self.assertEqual(errors, [])

    def test_agent_assignment_and_coordinator_records_are_loaded_and_validated(self) -> None:
        save_yaml(
            self.root / "agents" / "agent_20260603_ab12cd_cache_probe.yaml",
            {
                "agent_id": "agent_20260603_ab12cd_cache_probe",
                "label": "cache_probe",
                "display_name": "Cache probe",
                "status": "active",
                "created_at": "2026-06-03T10:00:00Z",
                "last_seen_at": "2026-06-03T10:30:00Z",
                "active_assignment_ids": ["assign_20260603_ab12cd"],
            },
        )
        save_yaml(
            self.root / "assignments" / "assign_20260603_ab12cd.yaml",
            {
                "assignment_id": "assign_20260603_ab12cd",
                "agent_id": "agent_20260603_ab12cd_cache_probe",
                "status": "active",
                "root_node": "option_t5",
                "current_node": "exp_t5",
                "allowed_subtree": {"root": "option_t5", "policy": "descendants_only"},
                "objective": "Run T5 cache probe.",
                "next_actions": ["Review smoke metrics."],
                "worktree": {"branch": "agent/option_t5", "label": "agent_option_t5"},
                "created_at": "2026-06-03T10:00:00Z",
                "updated_at": "2026-06-03T10:30:00Z",
            },
        )
        save_yaml(
            self.root / "coordinator_state.yaml",
            {
                "selected_node": "problem_text",
                "selected_assignment": "assign_20260603_ab12cd",
                "global_next_actions": ["Compare assignment results."],
                "dashboard_filters": {"hide_statuses": ["parked"]},
            },
        )

        agents = load_agents(self.root)
        assignments = load_assignments(self.root)
        coordinator_state = load_coordinator_state(self.root)
        errors = validate_cockpit(self.root, load_nodes(self.root))

        self.assertEqual(errors, [])
        self.assertEqual(agents["agent_20260603_ab12cd_cache_probe"].label, "cache_probe")
        self.assertEqual(assignments["assign_20260603_ab12cd"].root_node, "option_t5")
        self.assertEqual(coordinator_state.selected_assignment, "assign_20260603_ab12cd")

    def test_validate_rejects_assignment_missing_agent(self) -> None:
        save_yaml(
            self.root / "assignments" / "assign_missing_agent.yaml",
            {
                "assignment_id": "assign_missing_agent",
                "agent_id": "missing_agent",
                "status": "active",
                "root_node": "option_t5",
                "current_node": "exp_t5",
                "allowed_subtree": {"root": "option_t5", "policy": "descendants_only"},
            },
        )

        errors = validate_cockpit(self.root, load_nodes(self.root))

        self.assertTrue(
            any("assign_missing_agent: agent_id references missing agent 'missing_agent'" in error for error in errors)
        )

    def test_validate_rejects_invalid_agent_and_assignment_statuses(self) -> None:
        save_yaml(
            self.root / "agents" / "agent_scope.yaml",
            {"agent_id": "agent_scope", "status": "unknown"},
        )
        save_yaml(
            self.root / "assignments" / "assign_scope.yaml",
            {
                "assignment_id": "assign_scope",
                "agent_id": "agent_scope",
                "status": "unknown",
                "root_node": "option_t5",
                "current_node": "exp_t5",
                "allowed_subtree": {"root": "option_t5", "policy": "descendants_only"},
            },
        )

        errors = validate_cockpit(self.root, load_nodes(self.root))

        self.assertTrue(any("agent_scope: invalid agent status 'unknown'" in error for error in errors))
        self.assertTrue(any("assign_scope: invalid assignment status 'unknown'" in error for error in errors))

    def test_validate_rejects_assignment_missing_root_and_current_nodes(self) -> None:
        save_yaml(self.root / "agents" / "agent_scope.yaml", {"agent_id": "agent_scope", "status": "active"})
        save_yaml(
            self.root / "assignments" / "assign_scope.yaml",
            {
                "assignment_id": "assign_scope",
                "agent_id": "agent_scope",
                "status": "active",
                "allowed_subtree": {"root": "option_t5", "policy": "descendants_only"},
            },
        )

        errors = validate_cockpit(self.root, load_nodes(self.root))

        self.assertTrue(any("assign_scope: root_node is required" in error for error in errors))
        self.assertTrue(any("assign_scope: current_node is required" in error for error in errors))

    def test_validate_rejects_assignment_missing_node_references(self) -> None:
        save_yaml(self.root / "agents" / "agent_scope.yaml", {"agent_id": "agent_scope", "status": "active"})
        save_yaml(
            self.root / "assignments" / "assign_scope.yaml",
            {
                "assignment_id": "assign_scope",
                "agent_id": "agent_scope",
                "status": "active",
                "root_node": "missing_root",
                "current_node": "missing_current",
                "allowed_subtree": {"root": "missing_root", "policy": "descendants_only"},
            },
        )

        errors = validate_cockpit(self.root, load_nodes(self.root))

        self.assertTrue(any("assign_scope: root_node references missing node 'missing_root'" in error for error in errors))
        self.assertTrue(any("assign_scope: current_node references missing node 'missing_current'" in error for error in errors))

    def test_validate_rejects_assignment_current_node_outside_allowed_subtree(self) -> None:
        save_yaml(
            self.root / "agents" / "agent_scope.yaml",
            {"agent_id": "agent_scope", "status": "active"},
        )
        save_yaml(
            self.root / "assignments" / "assign_scope.yaml",
            {
                "assignment_id": "assign_scope",
                "agent_id": "agent_scope",
                "status": "active",
                "root_node": "option_t5",
                "current_node": "problem_text",
                "allowed_subtree": {"root": "option_t5", "policy": "descendants_only"},
            },
        )

        errors = validate_cockpit(self.root, load_nodes(self.root))

        self.assertTrue(any("assign_scope: current_node 'problem_text' is outside allowed_subtree root 'option_t5'" in error for error in errors))

    def test_validate_rejects_assignment_allowed_subtree_root_mismatch(self) -> None:
        save_yaml(self.root / "agents" / "agent_scope.yaml", {"agent_id": "agent_scope", "status": "active"})
        save_yaml(
            self.root / "assignments" / "assign_scope.yaml",
            {
                "assignment_id": "assign_scope",
                "agent_id": "agent_scope",
                "status": "active",
                "root_node": "option_t5",
                "current_node": "exp_t5",
                "allowed_subtree": {"root": "problem_text", "policy": "descendants_only"},
            },
        )

        errors = validate_cockpit(self.root, load_nodes(self.root))

        self.assertTrue(
            any(
                "assign_scope: allowed_subtree.root 'problem_text' must match root_node 'option_t5'" in error
                for error in errors
            )
        )

    def test_validate_rejects_agent_active_assignment_ids_that_are_not_a_list(self) -> None:
        save_yaml(
            self.root / "agents" / "agent_scope.yaml",
            {
                "agent_id": "agent_scope",
                "status": "active",
                "active_assignment_ids": {"assign_scope": True},
            },
        )
        save_yaml(
            self.root / "assignments" / "assign_scope.yaml",
            {
                "assignment_id": "assign_scope",
                "agent_id": "agent_scope",
                "status": "active",
                "root_node": "option_t5",
                "current_node": "exp_t5",
                "allowed_subtree": {"root": "option_t5", "policy": "descendants_only"},
            },
        )

        errors = validate_cockpit(self.root, load_nodes(self.root))

        self.assertTrue(any("agent_scope: active_assignment_ids must be a list" in error for error in errors))

    def test_validate_rejects_agent_active_assignment_owned_by_another_agent(self) -> None:
        save_yaml(
            self.root / "agents" / "agent_owner.yaml",
            {"agent_id": "agent_owner", "status": "active"},
        )
        save_yaml(
            self.root / "agents" / "agent_scope.yaml",
            {
                "agent_id": "agent_scope",
                "status": "active",
                "active_assignment_ids": ["assign_scope"],
            },
        )
        save_yaml(
            self.root / "assignments" / "assign_scope.yaml",
            {
                "assignment_id": "assign_scope",
                "agent_id": "agent_owner",
                "status": "active",
                "root_node": "option_t5",
                "current_node": "exp_t5",
                "allowed_subtree": {"root": "option_t5", "policy": "descendants_only"},
            },
        )

        errors = validate_cockpit(self.root, load_nodes(self.root))

        self.assertTrue(
            any(
                "agent_scope: active_assignment_ids contains assignment 'assign_scope' owned by 'agent_owner'"
                in error
                for error in errors
            )
        )

    def test_validate_rejects_scalar_agent_assignment_and_coordinator_lists(self) -> None:
        save_yaml(
            self.root / "agents" / "agent_scope.yaml",
            {
                "agent_id": "agent_scope",
                "status": "active",
                "active_assignment_ids": 123,
            },
        )
        save_yaml(
            self.root / "assignments" / "assign_scope.yaml",
            {
                "assignment_id": "assign_scope",
                "agent_id": "agent_scope",
                "status": "active",
                "root_node": "option_t5",
                "current_node": "exp_t5",
                "allowed_subtree": {"root": "option_t5", "policy": "descendants_only"},
                "next_actions": 123,
            },
        )
        save_yaml(self.root / "coordinator_state.yaml", {"global_next_actions": 123})

        errors = validate_cockpit(self.root, load_nodes(self.root))

        self.assertTrue(any("agent_scope: active_assignment_ids must be a list" in error for error in errors))
        self.assertTrue(any("assign_scope: next_actions must be a list" in error for error in errors))
        self.assertTrue(any("coordinator_state.global_next_actions must be a list" in error for error in errors))

    def test_validate_rejects_assignment_allowed_subtree_without_root(self) -> None:
        save_yaml(self.root / "agents" / "agent_scope.yaml", {"agent_id": "agent_scope", "status": "active"})
        save_yaml(
            self.root / "assignments" / "assign_scope.yaml",
            {
                "assignment_id": "assign_scope",
                "agent_id": "agent_scope",
                "status": "active",
                "root_node": "option_t5",
                "current_node": "exp_t5",
                "allowed_subtree": {},
            },
        )

        errors = validate_cockpit(self.root, load_nodes(self.root))

        self.assertTrue(any("assign_scope: allowed_subtree.root is required" in error for error in errors))

    def test_validate_rejects_scalar_assignment_allowed_subtree_without_crashing(self) -> None:
        save_yaml(self.root / "agents" / "agent_scope.yaml", {"agent_id": "agent_scope", "status": "active"})
        save_yaml(
            self.root / "assignments" / "assign_scope.yaml",
            {
                "assignment_id": "assign_scope",
                "agent_id": "agent_scope",
                "status": "active",
                "root_node": "option_t5",
                "current_node": "exp_t5",
                "allowed_subtree": 123,
            },
        )

        errors = validate_cockpit(self.root, load_nodes(self.root))

        self.assertTrue(any("assign_scope: allowed_subtree must be a mapping" in error for error in errors))

    def test_validate_rejects_active_assignment_with_terminal_root(self) -> None:
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        option["status"] = "accepted"
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", option)
        save_yaml(self.root / "agents" / "agent_scope.yaml", {"agent_id": "agent_scope", "status": "active"})
        save_yaml(
            self.root / "assignments" / "assign_scope.yaml",
            {
                "assignment_id": "assign_scope",
                "agent_id": "agent_scope",
                "status": "active",
                "root_node": "option_t5",
                "current_node": "exp_t5",
                "allowed_subtree": {"root": "option_t5", "policy": "descendants_only"},
            },
        )

        errors = validate_cockpit(self.root, load_nodes(self.root))

        self.assertTrue(
            any("assign_scope: active assignment root_node 'option_t5' has terminal status 'accepted'" in error for error in errors)
        )

    def test_validate_rejects_coordinator_missing_references(self) -> None:
        save_yaml(
            self.root / "coordinator_state.yaml",
            {"selected_node": "missing_node", "selected_assignment": "missing_assignment"},
        )

        errors = validate_cockpit(self.root, load_nodes(self.root))

        self.assertTrue(
            any("coordinator_state.selected_node references missing node 'missing_node'" in error for error in errors)
        )
        self.assertTrue(
            any(
                "coordinator_state.selected_assignment references missing assignment 'missing_assignment'" in error
                for error in errors
            )
        )

    def test_validate_rejects_multiple_active_assignments_for_same_root(self) -> None:
        for agent_id, assignment_id in (("agent_a", "assign_a"), ("agent_b", "assign_b")):
            save_yaml(self.root / "agents" / f"{agent_id}.yaml", {"agent_id": agent_id, "status": "active"})
            save_yaml(
                self.root / "assignments" / f"{assignment_id}.yaml",
                {
                    "assignment_id": assignment_id,
                    "agent_id": agent_id,
                    "status": "active",
                    "root_node": "option_t5",
                    "current_node": "exp_t5",
                    "allowed_subtree": {"root": "option_t5", "policy": "descendants_only"},
                },
            )

        errors = validate_cockpit(self.root, load_nodes(self.root))

        self.assertTrue(any("multiple active assignments claim root_node 'option_t5'" in error for error in errors))

    def test_validate_rejects_duplicate_assignment_id(self) -> None:
        save_yaml(self.root / "agents" / "agent_scope.yaml", {"agent_id": "agent_scope", "status": "active"})
        for filename in ("assign_first.yaml", "assign_second.yaml"):
            save_yaml(
                self.root / "assignments" / filename,
                {
                    "assignment_id": "assign_duplicate",
                    "agent_id": "agent_scope",
                    "status": "active",
                    "root_node": "option_t5",
                    "current_node": "exp_t5",
                    "allowed_subtree": {"root": "option_t5", "policy": "descendants_only"},
                },
            )

        with self.assertRaises(ValidationError) as ctx:
            validate_cockpit(self.root, load_nodes(self.root), raise_on_error=True)

        self.assertIn("duplicate assignment id 'assign_duplicate'", str(ctx.exception))

    def test_validate_rejects_duplicate_agent_id(self) -> None:
        save_yaml(self.root / "agents" / "agent_first.yaml", {"agent_id": "agent_duplicate", "status": "active"})
        save_yaml(self.root / "agents" / "agent_second.yaml", {"agent_id": "agent_duplicate", "status": "active"})

        with self.assertRaises(ValidationError) as ctx:
            validate_cockpit(self.root, load_nodes(self.root), raise_on_error=True)

        self.assertIn("duplicate agent id 'agent_duplicate'", str(ctx.exception))

    def test_validate_rejects_run_missing_run_id(self) -> None:
        save_yaml(
            self.root / "runs" / "missing_id.yaml",
            {
                "status": "running",
                "experiment_id": "exp_t5",
            },
        )

        with self.assertRaises(ValidationError) as ctx:
            validate_cockpit(self.root, load_nodes(self.root), raise_on_error=True)

        self.assertIn("runs/missing_id.yaml", str(ctx.exception))
        self.assertIn("missing required field 'run_id'", str(ctx.exception))

    def test_validate_returns_run_load_errors_without_raise(self) -> None:
        save_yaml(
            self.root / "runs" / "missing_id.yaml",
            {
                "status": "running",
                "experiment_id": "exp_t5",
            },
        )

        errors = validate_cockpit(self.root, load_nodes(self.root))

        self.assertTrue(any("runs/missing_id.yaml: missing required field 'run_id'" in error for error in errors))

    def test_validate_rejects_run_invalid_status(self) -> None:
        save_yaml(
            self.root / "runs" / "run_t5_smoke.yaml",
            {
                "run_id": "run_t5_smoke",
                "status": "stalled",
                "experiment_id": "exp_t5",
            },
        )

        with self.assertRaises(ValidationError) as ctx:
            validate_cockpit(self.root, load_nodes(self.root), raise_on_error=True)

        self.assertIn("run_t5_smoke: invalid run status 'stalled'", str(ctx.exception))

    def test_validate_returns_run_validation_errors_without_raise(self) -> None:
        save_yaml(
            self.root / "runs" / "run_t5_smoke.yaml",
            {
                "run_id": "run_t5_smoke",
                "status": "stalled",
                "experiment_id": "exp_t5",
            },
        )

        errors = validate_cockpit(self.root, load_nodes(self.root))

        self.assertTrue(any("run_t5_smoke: invalid run status 'stalled'" in error for error in errors))

    def test_validate_rejects_run_missing_experiment(self) -> None:
        save_yaml(
            self.root / "runs" / "run_missing_exp.yaml",
            {
                "run_id": "run_missing_exp",
                "status": "running",
                "experiment_id": "missing_exp",
            },
        )

        with self.assertRaises(ValidationError) as ctx:
            validate_cockpit(self.root, load_nodes(self.root), raise_on_error=True)

        self.assertIn("run_missing_exp: experiment_id references missing node 'missing_exp'", str(ctx.exception))

    def test_validate_rejects_run_non_experiment_reference(self) -> None:
        save_yaml(
            self.root / "runs" / "run_bad_ref.yaml",
            {
                "run_id": "run_bad_ref",
                "status": "running",
                "experiment_id": "option_t5",
            },
        )

        with self.assertRaises(ValidationError) as ctx:
            validate_cockpit(self.root, load_nodes(self.root), raise_on_error=True)

        self.assertIn("run_bad_ref: experiment_id references 'option_t5' with type 'option'", str(ctx.exception))

    def test_unknown_parent_reports_reference(self) -> None:
        write_node(
            self.root,
            {
                "id": "orphan_option",
                "type": "option",
                "title": "Orphan",
                "status": "open",
                "parent": "missing_problem",
            },
        )
        nodes = load_nodes(self.root)

        with self.assertRaises(ValidationError) as ctx:
            validate_cockpit(self.root, nodes, raise_on_error=True)

        self.assertIn("orphan_option", str(ctx.exception))
        self.assertIn("missing_problem", str(ctx.exception))

    def test_unknown_current_focus_path_reports_reference(self) -> None:
        save_yaml(
            self.root / "current_state.yaml",
            {
                "current_stage": "stage_text",
                "current_focus_path": ["stage_text", "missing_focus"],
            },
        )
        nodes = load_nodes(self.root)

        with self.assertRaises(ValidationError) as ctx:
            validate_cockpit(self.root, nodes, raise_on_error=True)

        self.assertIn("missing_focus", str(ctx.exception))

    def test_graph_json_deduplicates_parent_child_edges(self) -> None:
        nodes = load_nodes(self.root)

        graph = graph_to_json(nodes, ["stage_text", "problem_text", "option_t5"])
        edge_pairs = {(edge["from"], edge["to"]) for edge in graph["edges"]}

        self.assertEqual(len(edge_pairs), len(graph["edges"]))
        self.assertIn(("stage_text", "problem_text"), edge_pairs)
        self.assertIn(("problem_text", "option_t5"), edge_pairs)

    def test_graph_json_can_omit_raw_node_payload(self) -> None:
        nodes = load_nodes(self.root)

        default_graph = graph_to_json(nodes, ["stage_text", "problem_text", "option_t5"])
        slim_graph = graph_to_json(nodes, ["stage_text", "problem_text", "option_t5"], include_raw=False)

        self.assertIn("raw", default_graph["nodes"][0])
        self.assertNotIn("raw", slim_graph["nodes"][0])
        self.assertEqual(default_graph["nodes"][0]["id"], slim_graph["nodes"][0]["id"])
        self.assertEqual(default_graph["nodes"][0]["label"], slim_graph["nodes"][0]["label"])
        self.assertEqual(default_graph["edges"], slim_graph["edges"])

    def test_derive_focus_path_follows_parent_chain(self) -> None:
        nodes = load_nodes(self.root)

        self.assertEqual(derive_focus_path(nodes, "stage_text"), ["stage_text"])
        self.assertEqual(derive_focus_path(nodes, "problem_text"), ["stage_text", "problem_text"])
        self.assertEqual(derive_focus_path(nodes, "option_t5"), ["stage_text", "problem_text", "option_t5"])
        self.assertEqual(derive_focus_path(nodes, "exp_t5"), ["stage_text", "problem_text", "option_t5", "exp_t5"])
        self.assertEqual(
            derive_focus_path(nodes, "decision_t5"),
            ["stage_text", "problem_text", "option_t5", "decision_t5"],
        )

    def test_graph_topology_precomputes_children_parents_and_paths(self) -> None:
        nodes = load_nodes(self.root)
        topology = GraphTopology.from_nodes(nodes)

        self.assertEqual(topology.parent_by_node["option_t5"], "problem_text")
        self.assertEqual(topology.children_by_parent["problem_text"], ["option_t5"])
        self.assertEqual(
            topology.path_by_node["exp_t5"],
            ["stage_text", "problem_text", "option_t5", "exp_t5"],
        )
        self.assertEqual(topology.child_ids("option_t5"), ["exp_t5", "decision_t5"])
        self.assertEqual(topology.derive_path("decision_t5"), ["stage_text", "problem_text", "option_t5", "decision_t5"])

    def test_graph_topology_handles_deep_reverse_loaded_parent_chain(self) -> None:
        depth = 1200
        nodes = {
            f"node_{index:04d}": ResearchNode(
                id=f"node_{index:04d}",
                type="experiment",
                title=f"Node {index}",
                parent=f"node_{index - 1:04d}" if index > 0 else None,
                children=[f"node_{index + 1:04d}"] if index < depth - 1 else [],
            )
            for index in reversed(range(depth))
        }

        topology = GraphTopology.from_nodes(nodes)

        self.assertEqual(len(topology.derive_path("node_1199")), depth)
        self.assertEqual(topology.derive_path("node_1199")[0], "node_0000")
        self.assertEqual(topology.derive_path("node_1199")[-1], "node_1199")

    def test_graph_topology_reports_parent_cycle(self) -> None:
        nodes = {
            "node_a": ResearchNode(id="node_a", type="option", title="A", parent="node_b"),
            "node_b": ResearchNode(id="node_b", type="option", title="B", parent="node_a"),
        }

        topology = GraphTopology.from_nodes(nodes)

        with self.assertRaises(ValueError) as ctx:
            topology.derive_path("node_a")

        self.assertIn("parent cycle", str(ctx.exception))

    def test_derive_focus_path_reports_missing_parent(self) -> None:
        write_node(
            self.root,
            {
                "id": "exp_orphan",
                "type": "experiment",
                "title": "Orphan run",
                "status": "planned",
                "parent": "missing_option",
            },
        )
        nodes = load_nodes(self.root)

        with self.assertRaises(ValueError) as ctx:
            derive_focus_path(nodes, "exp_orphan")

        self.assertIn("missing_option", str(ctx.exception))

    def test_graph_topology_preserves_missing_parent_errors_and_safe_paths(self) -> None:
        write_node(
            self.root,
            {
                "id": "exp_orphan",
                "type": "experiment",
                "title": "Orphan run",
                "status": "planned",
                "parent": "missing_option",
            },
        )
        nodes = load_nodes(self.root)
        topology = GraphTopology.from_nodes(nodes)

        with self.assertRaises(ValueError) as ctx:
            topology.derive_path("exp_orphan")

        self.assertIn("missing_option", str(ctx.exception))
        self.assertEqual(topology.safe_path("exp_orphan"), ["exp_orphan"])

    def test_lifecycle_guard_blocks_terminal_problem_with_active_child_option(self) -> None:
        nodes = load_nodes(self.root)

        blockers = active_descendant_blockers(nodes, "problem_text", "resolved")

        self.assertEqual(
            blockers,
            [
                {
                    "id": "option_t5",
                    "type": "option",
                    "status": "active",
                    "path": ["stage_text", "problem_text", "option_t5"],
                },
                {
                    "id": "exp_t5",
                    "type": "experiment",
                    "status": "planned",
                    "path": ["stage_text", "problem_text", "option_t5", "exp_t5"],
                },
            ],
        )

    def test_lifecycle_guard_blocks_nested_active_descendant_option(self) -> None:
        save_yaml(
            self.root / "graph" / "nodes" / "option_t5.yaml",
            {
                "id": "option_t5",
                "type": "option",
                "title": "T5",
                "status": "paused",
                "parent": "problem_text",
                "children": ["problem_child"],
            },
        )
        write_node(
            self.root,
            {
                "id": "problem_child",
                "type": "problem",
                "title": "Nested question",
                "status": "active",
                "parent": "option_t5",
                "children": ["option_child"],
            },
        )
        write_node(
            self.root,
            {
                "id": "option_child",
                "type": "option",
                "title": "Nested option",
                "status": "promising",
                "parent": "problem_child",
            },
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        nodes = load_nodes(self.root)

        blockers = active_descendant_blockers(nodes, "problem_text", "parked")

        self.assertEqual(
            blockers,
            [
                {
                    "id": "problem_child",
                    "type": "problem",
                    "status": "active",
                    "path": ["stage_text", "problem_text", "option_t5", "problem_child"],
                },
                {
                    "id": "option_child",
                    "type": "option",
                    "status": "promising",
                    "path": ["stage_text", "problem_text", "option_t5", "problem_child", "option_child"],
                },
            ],
        )

    def test_lifecycle_guard_allows_terminal_parent_without_active_downstream(self) -> None:
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        option["status"] = "accepted"
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", option)
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        nodes = load_nodes(self.root)

        blockers = active_descendant_blockers(nodes, "problem_text", "resolved")

        self.assertEqual(blockers, [])

    def test_lifecycle_guard_ignores_non_terminal_parent_transition(self) -> None:
        nodes = load_nodes(self.root)

        blockers = active_descendant_blockers(nodes, "problem_text", "active")

        self.assertEqual(blockers, [])

    def test_explicit_edges_are_loaded_validated_and_deduplicated(self) -> None:
        save_yaml(
            self.root / "graph" / "edges.yaml",
            {
                "edges": [
                    {
                        "source": "problem_text",
                        "target": "option_t5",
                        "type": "supports",
                        "label": "supports",
                        "strength": 0.8,
                    },
                    {
                        "source": "option_t5",
                        "target": "exp_t5",
                        "type": "validates",
                    },
                    {
                        "source": "option_t5",
                        "target": "exp_t5",
                        "type": "validates",
                    },
                ]
            },
        )
        nodes = load_nodes(self.root)
        explicit_edges = load_explicit_edges(self.root)

        errors = validate_cockpit(self.root, nodes)
        graph = graph_to_json(nodes, ["stage_text", "problem_text", "option_t5"], explicit_edges=explicit_edges)
        edge_pairs = [(edge["from"], edge["to"]) for edge in graph["edges"]]
        explicit_edge = next(
            edge for edge in graph["edges"] if edge["from"] == "problem_text" and edge["to"] == "option_t5"
        )

        self.assertEqual(errors, [])
        self.assertEqual(edge_pairs.count(("option_t5", "exp_t5")), 1)
        self.assertEqual(explicit_edge["type"], "supports")
        self.assertEqual(explicit_edge["label"], "supports")

    def test_unknown_explicit_edge_node_reports_error(self) -> None:
        save_yaml(
            self.root / "graph" / "edges.yaml",
            {"edges": [{"source": "missing_node", "target": "option_t5", "type": "supports"}]},
        )
        nodes = load_nodes(self.root)

        with self.assertRaises(ValidationError) as ctx:
            validate_cockpit(self.root, nodes, raise_on_error=True)

        self.assertIn("missing_node", str(ctx.exception))

    def test_graph_json_adds_focus_mode_metadata(self) -> None:
        write_node(
            self.root,
            {
                "id": "stage_other",
                "type": "stage",
                "title": "Other stage",
                "status": "done",
                "children": ["problem_other"],
            },
        )
        write_node(
            self.root,
            {
                "id": "problem_other",
                "type": "problem",
                "title": "Other problem",
                "status": "resolved",
                "parent": "stage_other",
                "children": ["option_old"],
            },
        )
        write_node(
            self.root,
            {
                "id": "option_old",
                "type": "option",
                "title": "Old option",
                "status": "rejected",
                "parent": "problem_other",
            },
        )
        current = load_yaml(self.root / "current_state.yaml")
        current["current_focus_node"] = "problem_text"
        current["focus_mode"] = {
            "default_depth": 2,
            "hide_statuses": ["rejected", "parked", "archived", "resolved"],
        }
        nodes = load_nodes(self.root)

        graph = graph_to_json(nodes, current["current_focus_path"], current)
        graph_nodes = {node["id"]: node for node in graph["nodes"]}

        self.assertEqual(graph["current_focus_node"], "problem_text")
        self.assertTrue(graph_nodes["problem_text"]["is_current_focus"])
        self.assertEqual(graph_nodes["problem_text"]["focus_role"], "current")
        self.assertEqual(graph_nodes["problem_text"]["focus_visible_depth"], 0)
        self.assertTrue(graph_nodes["stage_text"]["in_focus_path"])
        self.assertEqual(graph_nodes["stage_text"]["focus_role"], "parent")
        self.assertEqual(graph_nodes["option_t5"]["focus_role"], "child")
        self.assertEqual(graph_nodes["exp_t5"]["focus_visible_depth"], 2)
        self.assertTrue(graph_nodes["exp_t5"]["is_focus_visible"])
        self.assertFalse(graph_nodes["option_old"]["is_focus_visible"])
        self.assertTrue(graph_nodes["option_old"]["is_hidden_by_focus"])

    def test_graph_json_includes_baseline_lens_metadata(self) -> None:
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["baseline"] = {
            "option": "option_t5",
            "decision": "decision_t5",
            "artifacts": [],
            "reason": "Use T5 as the default baseline.",
        }
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        current = load_yaml(self.root / "current_state.yaml")
        current["current_focus_node"] = "exp_t5"
        current["current_focus_path"] = ["stage_text", "problem_text", "option_t5", "exp_t5"]
        nodes = load_nodes(self.root)

        graph = graph_to_json(nodes, current["current_focus_path"], current)
        graph_nodes = {node["id"]: node for node in graph["nodes"]}

        self.assertEqual(graph_nodes["problem_text"]["effective_baseline_option_id"], "option_t5")
        self.assertEqual(graph_nodes["problem_text"]["baseline_source_id"], "problem_text")
        self.assertEqual(graph_nodes["problem_text"]["baseline_source_kind"], "explicit")
        self.assertEqual(graph_nodes["exp_t5"]["effective_baseline_option_id"], "option_t5")
        self.assertEqual(graph_nodes["exp_t5"]["baseline_source_kind"], "inherited")
        self.assertTrue(graph_nodes["problem_text"]["is_baseline_source"])
        self.assertTrue(graph_nodes["option_t5"]["is_effective_baseline_option"])
        self.assertTrue(graph_nodes["option_t5"]["is_current_effective_baseline_option"])

    def test_graph_json_adds_interaction_facets(self) -> None:
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["blockers"] = ["Need annotation policy"]
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        option["agent_workstream"] = {
            "owner": "agent_t5",
            "session_id": "session_t5",
            "status": "claimed",
            "objective": "Evaluate T5 path",
        }
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", option)
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["findings"] = [
            {
                "id": "exp_t5_finding_001",
                "statement": "T5 helps.",
                "confidence": "medium",
                "outcome": "positive",
            }
        ]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        current = load_yaml(self.root / "current_state.yaml")
        current["current_focus_node"] = "problem_text"
        nodes = load_nodes(self.root)

        graph = graph_to_json(nodes, current["current_focus_path"], current)
        graph_nodes = {node["id"]: node for node in graph["nodes"]}

        self.assertEqual(graph_nodes["problem_text"]["stage_id"], "stage_text")
        self.assertEqual(graph_nodes["option_t5"]["problem_id"], "problem_text")
        self.assertEqual(graph_nodes["exp_t5"]["option_workstream_id"], "option_t5")
        self.assertEqual(graph_nodes["exp_t5"]["agent_owner"], "agent_t5")
        self.assertEqual(graph_nodes["exp_t5"]["agent_session_id"], "session_t5")
        self.assertTrue(graph_nodes["problem_text"]["has_blockers"])
        self.assertTrue(graph_nodes["exp_t5"]["has_evidence"])
        self.assertFalse(graph_nodes["decision_t5"]["has_evidence"])
        self.assertTrue(graph_nodes["option_t5"]["in_current_branch"])
        self.assertIn("stage_text", graph["available_filters"]["stages"])
        self.assertIn("option_t5", graph["available_filters"]["workstreams"])
        self.assertIn("agent_t5", graph["available_filters"]["agents"])

    def test_model_reexports_unique_strings_for_compatibility(self) -> None:
        self.assertEqual(unique_strings(["a", "b", "a", "", None]), ["a", "b"])

    def test_interaction_log_appends_events(self) -> None:
        event = append_interaction_log(
            self.root,
            kind="set_focus",
            actor="researcher",
            node_id="problem_text",
            command="research-cockpit \set_focus.py --focus-node problem_text",
            before={"current_focus_node": "option_t5"},
            after={"current_focus_node": "problem_text"},
        )

        log = load_interaction_log(self.root)

        self.assertEqual(event["kind"], "set_focus")
        self.assertEqual(log["events"][0]["node_id"], "problem_text")
        self.assertEqual(log["events"][0]["before"]["current_focus_node"], "option_t5")

    def test_interaction_segment_backend_appends_without_rewriting_legacy_yaml(self) -> None:
        first = append_interaction_log(self.root, kind="first", node_id="problem_text")
        second = append_interaction_log(self.root, kind="second", node_id="option_t5")

        event_dir = self.root / "graph" / "interaction_events"
        manifest = json.loads((event_dir / "manifest.json").read_text(encoding="utf-8"))
        segments = sorted(event_dir.glob("events-*.jsonl"))

        self.assertFalse((self.root / "graph" / "interaction_log.yaml").exists())
        self.assertEqual(manifest["active_format"], "jsonl_v1")
        self.assertEqual(manifest["legacy_mode"], "prefix")
        self.assertEqual(len(segments), 1)
        self.assertEqual(len(segments[0].read_text(encoding="utf-8").splitlines()), 2)
        self.assertEqual([event["id"] for event in recent_interactions(self.root, limit=2)], [second["id"], first["id"]])
        self.assertEqual(validate_interaction_log(self.root), [])

    def test_interaction_segment_validation_rejects_truncated_json_line(self) -> None:
        append_interaction_log(self.root, kind="first", node_id="problem_text")
        segment = next((self.root / "graph" / "interaction_events").glob("events-*.jsonl"))
        with segment.open("ab") as stream:
            stream.write(b'{"id":"truncated"')
            stream.flush()
            os.fsync(stream.fileno())

        errors = validate_interaction_log(self.root)

        self.assertEqual(len(errors), 1)
        self.assertIn("JSON parse error", errors[0])
        self.assertIn(segment.name, errors[0])

    def test_concurrent_public_interaction_appends_do_not_lose_events(self) -> None:
        def append(index: int) -> str:
            return append_interaction_log(self.root, kind="parallel", extra={"index": index})["id"]

        with ThreadPoolExecutor(max_workers=8) as executor:
            event_ids = list(executor.map(append, range(32)))

        events = load_interaction_log(self.root, strict=True)["events"]
        self.assertEqual(len(events), 32)
        self.assertEqual(len(set(event_ids)), 32)
        self.assertEqual({event["index"] for event in events}, set(range(32)))

    def test_prefix_backend_detects_same_size_legacy_rewrite_with_preserved_mtime(self) -> None:
        legacy_path = self.root / "graph" / "interaction_log.yaml"
        save_yaml(legacy_path, {"events": [{"id": "legacy_a", "kind": "a"}]})
        append_interaction_log(self.root, kind="activate")
        original_stat = legacy_path.stat()
        original = legacy_path.read_text(encoding="utf-8")
        changed = original.replace("legacy_a", "legacy_b")
        self.assertEqual(len(changed), len(original))
        legacy_path.write_text(changed, encoding="utf-8")
        os.utime(legacy_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

        errors = validate_interaction_append_target(self.root)

        self.assertEqual(len(errors), 1)
        self.assertIn("changed after", errors[0])

    def test_invalid_active_manifest_fails_closed_instead_of_falling_back_to_legacy(self) -> None:
        legacy_path = self.root / "graph" / "interaction_log.yaml"
        save_yaml(legacy_path, {"events": [{"id": "legacy", "kind": "legacy"}]})
        append_interaction_log(self.root, kind="jsonl")
        manifest_path = self.root / "graph" / "interaction_events" / "manifest.json"
        manifest_path.write_text("{", encoding="utf-8")

        payload = load_interaction_log(self.root)

        self.assertEqual(payload["events"], [])
        self.assertEqual(payload["backend"], "invalid")
        self.assertTrue(any("manifest.json" in warning for warning in payload["warnings"]))
        with self.assertRaises(InteractionLogError):
            load_interaction_log(self.root, strict=True)

    def test_mutation_preflight_rejects_modified_sealed_segment(self) -> None:
        from research_cockpit import interaction_log

        with patch.object(interaction_log, "SEGMENT_MAX_BYTES", 1):
            append_interaction_log(self.root, kind="first")
            append_interaction_log(self.root, kind="second")
        event_dir = self.root / "graph" / "interaction_events"
        first_segment = sorted(event_dir.glob("events-*.jsonl"))[0]
        first_segment.write_bytes(first_segment.read_bytes() + b" ")

        errors = validate_interaction_append_target(self.root)

        self.assertEqual(len(errors), 1)
        self.assertIn("sealed segment changed", errors[0])
    def test_load_graph_views_handles_missing_and_invalid_data(self) -> None:
        self.assertEqual(load_graph_views(self.root), [])

        save_yaml(self.root / "graph" / "graph_views.yaml", {"version": 1, "views": "bad"})
        self.assertEqual(load_graph_views(self.root), [])

        save_yaml(
            self.root / "graph" / "graph_views.yaml",
            {
                "version": 1,
                "views": [
                    "bad",
                    {
                        "id": "Bad View!",
                        "title": "Branch blockers",
                        "scope": "unknown",
                        "filters": {
                            "node_types": ["problem", "", "problem"],
                            "statuses": "active",
                            "collapsed_branch_roots": ["option_t5", "", "option_t5"],
                            "revealed_child_roots": "option_old",
                            "only_blocking": "yes",
                        },
                        "saved_focus_node_id": "problem_text",
                        "saved_focus_path": ["stage_text", None, "problem_text"],
                    },
                ],
            },
        )

        views = load_graph_views(self.root)

        self.assertEqual(len(views), 1)
        self.assertEqual(views[0]["id"], "bad_view")
        self.assertEqual(views[0]["scope"], "focus_depth_2")
        self.assertEqual(views[0]["filters"]["node_types"], ["problem"])
        self.assertEqual(views[0]["filters"]["statuses"], ["active"])
        self.assertEqual(views[0]["filters"]["collapsed_branch_roots"], ["option_t5"])
        self.assertEqual(views[0]["filters"]["revealed_child_roots"], ["option_old"])
        self.assertTrue(views[0]["filters"]["only_blocking"])
        self.assertFalse(views[0]["filters"]["only_next_actions"])
        self.assertNotIn("show_baseline_lens", views[0]["filters"])
        self.assertEqual(views[0]["saved_focus_path"], ["stage_text", "problem_text"])

    def test_upsert_graph_view_preserves_created_at_and_logs_event(self) -> None:
        first = upsert_graph_view(
            self.root,
            {
                "title": "Current Branch Blockers",
                "scope": "current_branch",
                "filters": {
                    "node_types": ["problem", "option"],
                    "statuses": ["active", "open"],
                    "collapsed_branch_roots": ["option_t5"],
                    "revealed_child_roots": ["option_old"],
                    "only_blocking": True,
                },
                "saved_focus_node_id": "problem_text",
                "saved_focus_path": ["stage_text", "problem_text"],
            },
        )

        second = upsert_graph_view(
            self.root,
            {
                "id": first["id"],
                "title": "Current Branch Blockers",
                "scope": "global",
                "filters": {"node_types": ["stage"], "only_blocking": False},
            },
        )
        views = load_graph_views(self.root)
        log = load_interaction_log(self.root)

        self.assertEqual(first["id"], "current_branch_blockers")
        self.assertEqual(len(views), 1)
        self.assertEqual(second["created_at"], first["created_at"])
        self.assertEqual(views[0]["scope"], "global")
        self.assertEqual(first["filters"]["collapsed_branch_roots"], ["option_t5"])
        self.assertEqual(first["filters"]["revealed_child_roots"], ["option_old"])
        self.assertEqual(log["events"][-1]["kind"], "save_graph_view")
        self.assertEqual(log["events"][-1]["view_id"], first["id"])
        self.assertIn("filters", log["events"][-1])

    def test_graph_view_id_from_title_falls_back_for_non_ascii_title(self) -> None:
        self.assertEqual(graph_view_id_from_title("Current Branch Blockers"), "current_branch_blockers")
        self.assertTrue(graph_view_id_from_title("当前分支", "2026-04-28T00:00:00Z").startswith("graph_view_"))

    def test_agent_context_resolves_focus_node_details(self) -> None:
        nodes = load_nodes(self.root)

        context = build_agent_context(self.root, nodes)

        self.assertEqual(context["current_stage"], "stage_text")
        self.assertEqual(context["current_stage_title"], "Text")
        self.assertEqual(context["linked_nodes"][0]["title"], "Text")

    def test_context_packs_include_saved_graph_views(self) -> None:
        upsert_graph_view(
            self.root,
            {
                "title": "Current Branch",
                "scope": "current_branch",
                "filters": {"node_types": ["problem"]},
            },
        )
        nodes = load_nodes(self.root)

        context = build_agent_context(self.root, nodes)
        focus_context = build_focus_context(self.root, nodes)

        self.assertEqual(context["saved_graph_views"][0]["id"], "current_branch")
        self.assertEqual(focus_context["saved_graph_views"][0]["scope"], "current_branch")

    def test_link_rows_parse_node_links_and_resource_paths(self) -> None:
        note_path = self.root / "notes" / "problems" / "problem_text.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text("# Problem note\n", encoding="utf-8")
        config_path = self.root / "configs" / "exp_t5.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("ok: true\n", encoding="utf-8")

        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["links"] = {
            "notes": "notes/problems/problem_text.md",
            "external": "https://example.com/problem",
        }
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)

        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["config_path"] = "configs/exp_t5.yaml"
        experiment["run_id"] = "run-123"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        write_node(
            self.root,
            {
                "id": "artifact_fig",
                "type": "artifact",
                "title": "Figure",
                "status": "active",
                "path": "figures/missing.png",
            },
        )
        nodes = load_nodes(self.root)

        rows = build_link_rows(self.root, nodes)
        by_kind = {(row["node_id"], row["kind"], row["label"]): row for row in rows}
        context = node_context(nodes["problem_text"])

        self.assertTrue(by_kind[("problem_text", "link", "notes")]["exists"])
        self.assertIsNone(by_kind[("problem_text", "link", "external")]["exists"])
        self.assertTrue(by_kind[("exp_t5", "config_path", "config_path")]["exists"])
        self.assertIsNone(by_kind[("exp_t5", "run_id", "run_id")]["exists"])
        self.assertFalse(by_kind[("artifact_fig", "path", "path")]["exists"])
        self.assertEqual(context["links"][0]["target"], "notes/problems/problem_text.md")

    def test_link_rows_cache_repeated_target_resolution(self) -> None:
        resource_path = self.root / "resources" / "shared.txt"
        resource_path.parent.mkdir(parents=True, exist_ok=True)
        resource_path.write_text("shared resource\n", encoding="utf-8")
        for node_id in ("stage_text", "problem_text", "option_t5", "exp_t5"):
            data = load_yaml(self.root / "graph" / "nodes" / f"{node_id}.yaml")
            data["links"] = {"shared": "resources/shared.txt"}
            save_yaml(self.root / "graph" / "nodes" / f"{node_id}.yaml", data)
        nodes = load_nodes(self.root)

        import research_cockpit.resources as resources

        with patch("research_cockpit.resources._target_resolution", wraps=resources._target_resolution) as resolver:
            rows = build_link_rows(self.root, nodes)

        shared_rows = [row for row in rows if row.get("target") == "resources/shared.txt"]
        shared_calls = [
            call for call in resolver.call_args_list
            if call.args[1:] == ("link", "resources/shared.txt", nodes)
        ]
        self.assertEqual(len(shared_rows), 4)
        self.assertTrue(all(row["exists"] for row in shared_rows))
        self.assertEqual(len(shared_calls), 1)

    def test_search_index_links_notes_and_indexes_unlinked_notes(self) -> None:
        linked_note = self.root / "notes" / "problems" / "problem_text.md"
        linked_note.parent.mkdir(parents=True, exist_ok=True)
        linked_note.write_text("# Problem Note\nSemantic ribbon note for T5 branch.\n", encoding="utf-8")
        unlinked_note = self.root / "notes" / "misc" / "free.md"
        unlinked_note.parent.mkdir(parents=True, exist_ok=True)
        unlinked_note.write_text("# Free Note\nUnlinked latent edit observation.\n", encoding="utf-8")

        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["links"] = {"notes": "notes/problems/problem_text.md"}
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        nodes = load_nodes(self.root)
        current = load_yaml(self.root / "current_state.yaml")

        index = build_search_index(self.root, nodes, current)
        by_entry = {entry["entry_id"]: entry for entry in index}

        self.assertEqual(by_entry["note:notes/problems/problem_text.md"]["node_id"], "problem_text")
        self.assertEqual(by_entry["note:notes/misc/free.md"]["node_id"], None)
        self.assertEqual(by_entry["note:notes/problems/problem_text.md"]["title"], "Problem Note")

    def test_search_index_includes_local_linked_text_resources(self) -> None:
        resource_path = self.root / "resources" / "problem_context.txt"
        resource_path.parent.mkdir(parents=True, exist_ok=True)
        resource_path.write_text("Resource needle for CLAP cache.\n", encoding="utf-8")
        config_path = self.root / "configs" / "exp_t5.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("cache: resource needle\n", encoding="utf-8")
        note_path = self.root / "notes" / "problems" / "problem_text.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text("# Linked note\nDo not duplicate as resource.\n", encoding="utf-8")

        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["links"] = {
            "context": "resources/problem_context.txt",
            "notes": "notes/problems/problem_text.md",
        }
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["config_path"] = "configs/exp_t5.yaml"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        nodes = load_nodes(self.root)
        current = load_yaml(self.root / "current_state.yaml")

        index = build_search_index(self.root, nodes, current)
        resource_entries = [entry for entry in index if entry["source"] == "resource" and not entry.get("skip_reason")]
        resource_paths = {entry["path"] for entry in resource_entries}
        results = search_knowledge(index, "resource needle", sources={"resource"}, focus_only=True)

        self.assertIn("resources/problem_context.txt", resource_paths)
        self.assertIn("configs/exp_t5.yaml", resource_paths)
        self.assertNotIn("notes/problems/problem_text.md", resource_paths)
        self.assertTrue(all(item["source"] == "resource" for item in results))
        self.assertTrue(all(item["is_focus_related"] for item in results))

    def test_search_index_can_skip_resource_text_reads(self) -> None:
        resource_path = self.root / "resources" / "problem_context.txt"
        resource_path.parent.mkdir(parents=True, exist_ok=True)
        resource_path.write_text("Resource needle should not be read.\n", encoding="utf-8")
        note_path = self.root / "notes" / "problems" / "problem_text.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text("# Problem Note\nNote needle stays searchable.\n", encoding="utf-8")

        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["links"] = {
            "context": "resources/problem_context.txt",
            "notes": "notes/problems/problem_text.md",
        }
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        nodes = load_nodes(self.root)
        current = load_yaml(self.root / "current_state.yaml")

        with patch("research_cockpit.search_index._read_resource_text", side_effect=AssertionError("resource read")):
            index = build_search_index(self.root, nodes, current, include_resource_text=False)
        entry = next(item for item in index if item.get("path") == "resources/problem_context.txt")
        summary = build_search_index_summary(index)
        resource_results = search_knowledge(index, "Resource needle", sources={"resource"})
        note_results = search_knowledge(index, "Note needle", sources={"note"})

        self.assertEqual(entry["skip_reason"], "resource_search_disabled")
        self.assertEqual(entry["bytes_read"], 0)
        self.assertEqual(summary["resource_count"], 0)
        self.assertGreaterEqual(summary["resource_skipped_count"], 1)
        self.assertEqual(summary["resource_search_disabled_count"], 1)
        self.assertEqual(resource_results, [])
        self.assertEqual(note_results[0]["entry_id"], "note:notes/problems/problem_text.md")

    def test_search_index_caches_repeated_resource_text_reads(self) -> None:
        resource_path = self.root / "resources" / "shared_context.txt"
        resource_path.parent.mkdir(parents=True, exist_ok=True)
        resource_path.write_text("Shared resource needle.\n", encoding="utf-8")
        for node_id in ("stage_text", "problem_text", "option_t5", "exp_t5"):
            data = load_yaml(self.root / "graph" / "nodes" / f"{node_id}.yaml")
            data["links"] = {"shared": "resources/shared_context.txt"}
            save_yaml(self.root / "graph" / "nodes" / f"{node_id}.yaml", data)
        nodes = load_nodes(self.root)
        current = load_yaml(self.root / "current_state.yaml")

        import research_cockpit.search_index as search_index_module

        with patch(
            "research_cockpit.search_index._read_resource_text",
            wraps=search_index_module._read_resource_text,
        ) as reader:
            index = build_search_index(self.root, nodes, current)
        shared_entries = [entry for entry in index if entry.get("path") == "resources/shared_context.txt"]
        shared_reads = [call for call in reader.call_args_list if call.args[0] == resource_path.resolve()]
        results = search_knowledge(index, "Shared resource needle", sources={"resource"})

        self.assertEqual(len(shared_entries), 4)
        self.assertEqual(len(shared_reads), 1)
        self.assertGreaterEqual(len(results), 1)

    def test_search_index_summary_deduplicates_resource_bytes_read_by_path(self) -> None:
        index = [
            {"source": "resource", "path": "resources/shared.txt", "bytes_read": 12},
            {"source": "resource", "path": "resources/shared.txt", "bytes_read": 12},
            {"source": "resource", "path": "resources/other.txt", "bytes_read": 7},
        ]

        summary = build_search_index_summary(index)

        self.assertEqual(summary["resource_count"], 3)
        self.assertEqual(summary["resource_unique_count"], 2)
        self.assertEqual(summary["resource_bytes_read"], 19)

    def test_search_index_tracks_skipped_resources_without_searching_them(self) -> None:
        png_path = self.root / "figures" / "plot.png"
        png_path.parent.mkdir(parents=True, exist_ok=True)
        png_path.write_bytes(b"\x89PNG\r\n")
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["links"] = {
            "external": "https://example.com/report.txt",
            "missing": "resources/missing.txt",
            "plot": "figures/plot.png",
            "absolute": str((self.root / "outside.txt").resolve()),
        }
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["run_id"] = "run-123"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        nodes = load_nodes(self.root)
        current = load_yaml(self.root / "current_state.yaml")

        index = build_search_index(self.root, nodes, current)
        skipped = {
            entry["resource_label"]: entry["skip_reason"]
            for entry in index
            if entry["source"] == "resource" and entry.get("skip_reason")
        }
        results = search_knowledge(index, "example missing plot run-123", sources={"resource"})

        self.assertEqual(skipped["external"], "external")
        self.assertEqual(skipped["missing"], "missing")
        self.assertEqual(skipped["plot"], "unsupported_extension")
        self.assertEqual(skipped["absolute"], "absolute_path")
        self.assertEqual(skipped["run_id"], "run_id")
        self.assertEqual(results, [])

    def test_search_index_truncates_large_resources_and_summarizes_resource_counts(self) -> None:
        big_path = self.root / "resources" / "big.txt"
        big_path.parent.mkdir(parents=True, exist_ok=True)
        big_path.write_text("early needle\n" + ("x" * (140 * 1024)), encoding="utf-8")
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["links"] = {"big": "resources/big.txt"}
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        nodes = load_nodes(self.root)
        current = load_yaml(self.root / "current_state.yaml")

        index = build_search_index(self.root, nodes, current)
        entry = next(item for item in index if item.get("path") == "resources/big.txt")
        summary = build_search_index_summary(index)
        results = search_knowledge(index, "early needle", sources={"resource"})

        self.assertTrue(entry["truncated"])
        self.assertEqual(entry["bytes_read"], 128 * 1024)
        self.assertGreaterEqual(summary["resource_count"], 1)
        self.assertEqual(summary["resource_truncated_count"], 1)
        self.assertGreaterEqual(summary["resource_bytes_read"], 128 * 1024)
        self.assertGreaterEqual(summary["focus_resource_count"], 1)
        self.assertEqual(results[0]["source"], "resource")

    def test_search_knowledge_matches_node_fields_case_insensitively_and_focus_only(self) -> None:
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["blockers"] = ["Need CLAP cache parity"]
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["findings"] = [{"statement": "CLAP cache improves event following.", "confidence": "medium"}]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        unlinked_note = self.root / "notes" / "archive" / "old.md"
        unlinked_note.parent.mkdir(parents=True, exist_ok=True)
        unlinked_note.write_text("# Old\nCLAP cache unrelated archive.\n", encoding="utf-8")

        nodes = load_nodes(self.root)
        current = load_yaml(self.root / "current_state.yaml")
        index = build_search_index(self.root, nodes, current)

        results = search_knowledge(index, "clap CACHE")
        focus_results = search_knowledge(index, "clap cache", focus_only=True)
        problem_result = next(item for item in results if item["node_id"] == "problem_text")

        self.assertGreater(problem_result["score"], 0)
        self.assertIn("CLAP cache", problem_result["snippet"])
        self.assertTrue(all(item["is_focus_related"] for item in focus_results))
        self.assertNotIn("note:notes/archive/old.md", {item["entry_id"] for item in focus_results})

    def test_search_index_summary_counts_sources_without_full_text(self) -> None:
        note_path = self.root / "notes" / "problems" / "problem_text.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text("# Problem Note\nFocus note.\n", encoding="utf-8")
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["links"] = {"notes": "notes/problems/problem_text.md"}
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        nodes = load_nodes(self.root)
        current = load_yaml(self.root / "current_state.yaml")

        index = build_search_index(self.root, nodes, current)
        summary = build_search_index_summary(index)

        self.assertEqual(summary["note_count"], 1)
        self.assertEqual(summary["node_count"], len(nodes))
        self.assertEqual(summary["unlinked_note_count"], 0)
        self.assertGreater(summary["focus_entry_count"], 0)
        self.assertNotIn("text", summary["focus_entries"][0])

    def test_experiment_matrix_contains_experiment_rows(self) -> None:
        nodes = load_nodes(self.root)

        rows = build_experiment_matrix(nodes)

        self.assertEqual(rows[0]["id"], "exp_t5")
        self.assertEqual(rows[0]["parent"], "option_t5")

    def test_action_suggestions_cover_focus_blockers_experiments_decisions_and_missing_resources(self) -> None:
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["blockers"] = ["Need cache parity"]
        problem["next_actions"] = ["Run ablation"]
        problem["links"] = {
            "notes": "notes/problems/problem_text.md",
            "external": "https://example.com/problem",
        }
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)

        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)

        nodes = load_nodes(self.root)
        current = load_yaml(self.root / "current_state.yaml")
        link_rows = build_link_rows(self.root, nodes)

        suggestions = build_action_suggestions(self.root, nodes, current, link_rows)
        by_kind = {suggestion["kind"]: suggestion for suggestion in suggestions}

        self.assertEqual(suggestions[0]["kind"], "focus_next_action")
        self.assertIn("resolve_blocker", by_kind)
        self.assertIn("record_finding", by_kind)
        self.assertIn("review_decision", by_kind)
        self.assertIn("fix_resource", by_kind)
        self.assertEqual(by_kind["record_finding"]["source_node_id"], "exp_t5")
        self.assertIn("research-cockpit work record", by_kind["record_finding"]["suggested_command"])
        self.assertNotIn("research-cockpit record-finding", by_kind["record_finding"]["suggested_command"])
        self.assertEqual(by_kind["fix_resource"]["source_node_id"], "problem_text")
        self.assertNotIn("https://example.com/problem", by_kind["fix_resource"]["action"])

    def test_action_suggestions_include_run_experiment_and_dedupe_actions(self) -> None:
        current = load_yaml(self.root / "current_state.yaml")
        current["next_actions"] = ["Run ablation", "Run ablation"]
        save_yaml(self.root / "current_state.yaml", current)
        nodes = load_nodes(self.root)

        suggestions = build_action_suggestions(self.root, nodes, current)
        run_suggestions = [item for item in suggestions if item["kind"] == "run_experiment"]
        focus_actions = [
            item for item in suggestions
            if item["kind"] == "focus_next_action" and item["action"] == "Run ablation"
        ]

        self.assertEqual(len(run_suggestions), 1)
        self.assertEqual(run_suggestions[0]["source_node_id"], "exp_t5")
        self.assertEqual(len(focus_actions), 1)

    def test_action_suggestions_mark_current_and_node_queue_state(self) -> None:
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["next_actions"] = ["Node queued action"]
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        current = load_yaml(self.root / "current_state.yaml")
        current["current_focus_node"] = "problem_text"
        current["next_actions"] = ["Current queued action"]
        save_yaml(self.root / "current_state.yaml", current)
        nodes = load_nodes(self.root)

        suggestions = build_action_suggestions(self.root, nodes, current)
        by_action = {suggestion["action"]: suggestion for suggestion in suggestions}

        self.assertTrue(by_action["Current queued action"]["queued_in_current"])
        self.assertFalse(by_action["Current queued action"]["queued_in_node"])
        self.assertFalse(by_action["Node queued action"]["queued_in_current"])
        self.assertTrue(by_action["Node queued action"]["queued_in_node"])
        self.assertEqual([item["id"] for item in suggestions], [f"next_action_{index:03d}" for index in range(1, len(suggestions) + 1)])

    def test_action_suggestions_generate_public_commands(self) -> None:
        previous = os.environ.pop("RESEARCH_COCKPIT_PYTHON", None)
        try:
            nodes = load_nodes(self.root)
            current = load_yaml(self.root / "current_state.yaml")

            suggestions = build_action_suggestions(self.root, nodes, current)
            commands = [item["suggested_command"] for item in suggestions if item.get("suggested_command")]

            self.assertTrue(commands)
            self.assertTrue(all(command.startswith("research-cockpit ") for command in commands))
            self.assertFalse(any("D:\\Tools" in command for command in commands))
            self.assertFalse(any("miniconda" in command.lower() for command in commands))
        finally:
            if previous is not None:
                os.environ["RESEARCH_COCKPIT_PYTHON"] = previous

    def test_context_metadata_contains_freshness_fields(self) -> None:
        current = load_yaml(self.root / "current_state.yaml")

        metadata = build_context_metadata(self.root, current)

        self.assertEqual(metadata["schema_version"], "agent_context_v1")
        self.assertIn("generated_at", metadata)
        self.assertIn("source_git_commit", metadata)
        self.assertIsInstance(metadata["worktree_dirty"], bool)
        self.assertEqual(metadata["current_state_updated_at"], current.get("updated_at"))

    def test_action_suggestions_use_stable_keys_and_filter_lifecycle_states(self) -> None:
        nodes = load_nodes(self.root)
        current = load_yaml(self.root / "current_state.yaml")
        first = build_action_suggestions(self.root, nodes, current)
        run_key = next(item["key"] for item in first if item["kind"] == "run_experiment")

        current["next_actions"] = ["New urgent action"] + current["next_actions"]
        second = build_action_suggestions(self.root, nodes, current)
        self.assertEqual(run_key, next(item["key"] for item in second if item["kind"] == "run_experiment"))

        current["suggestion_lifecycle"] = {
            run_key: {
                "state": "dismissed",
                "reason": "Waiting for new cache.",
                "updated_at": "2026-04-28",
                "action": "Run planned experiment: T5 ablation",
                "kind": "run_experiment",
                "source_node_id": "exp_t5",
            }
        }
        active = build_action_suggestions(self.root, nodes, current)
        all_suggestions = build_action_suggestions(self.root, nodes, current, include_inactive=True)
        inactive = next(item for item in all_suggestions if item["key"] == run_key)

        self.assertNotIn(run_key, {item["key"] for item in active})
        self.assertEqual(inactive["lifecycle_state"], "dismissed")
        self.assertEqual(inactive["lifecycle_reason"], "Waiting for new cache.")

    def test_suggestion_lifecycle_validation_and_summary_handle_orphans(self) -> None:
        nodes = load_nodes(self.root)
        current = load_yaml(self.root / "current_state.yaml")
        current_suggestions = build_action_suggestions(self.root, nodes, current, include_inactive=True)
        active_key = current_suggestions[0]["key"]
        current["suggestion_lifecycle"] = {
            active_key: {
                "state": "dismissed",
                "reason": "Still relevant but hidden.",
                "updated_at": "2026-04-27",
                "action": current_suggestions[0]["action"],
                "kind": current_suggestions[0]["kind"],
                "source_node_id": current_suggestions[0]["source_node_id"],
            },
            "orphan_suggestion": {
                "state": "completed",
                "reason": "Old suggestion resolved.",
                "updated_at": "2026-04-20",
                "action": "Old action",
                "kind": "run_experiment",
                "source_node_id": "exp_old",
            },
            "bad_date_suggestion": {
                "state": "dismissed",
                "reason": "Bad date should not compute age.",
                "updated_at": "not-a-date",
                "action": "Old action with bad date",
                "kind": "record_finding",
                "source_node_id": "exp_old",
            }
        }

        errors = validate_cockpit(self.root, nodes, current)
        suggestions = build_action_suggestions(self.root, nodes, current, include_inactive=True)
        summary = build_suggestion_lifecycle_summary(current, suggestions)
        rows = build_suggestion_lifecycle_rows(current, suggestions, today=date(2026, 4, 28))
        by_key = {row["key"]: row for row in rows}

        self.assertEqual(errors, [])
        self.assertEqual(summary["orphan"], 2)
        self.assertGreater(summary["active"], 0)
        self.assertTrue(by_key[active_key]["active_match"])
        self.assertFalse(by_key[active_key]["orphan"])
        self.assertFalse(by_key["orphan_suggestion"]["active_match"])
        self.assertTrue(by_key["orphan_suggestion"]["orphan"])
        self.assertEqual(by_key["orphan_suggestion"]["age_days"], 8)
        self.assertIsNone(by_key["bad_date_suggestion"]["age_days"])

        current["suggestion_lifecycle"] = {"bad": {"state": "ignored"}}
        with self.assertRaises(ValidationError) as invalid_state:
            validate_cockpit(self.root, nodes, current, raise_on_error=True)
        self.assertIn("suggestion_lifecycle", str(invalid_state.exception))
        self.assertIn("ignored", str(invalid_state.exception))

        current["suggestion_lifecycle"] = ["bad"]
        with self.assertRaises(ValidationError) as invalid_shape:
            validate_cockpit(self.root, nodes, current, raise_on_error=True)
        self.assertIn("suggestion_lifecycle", str(invalid_shape.exception))

    def test_experiment_findings_enter_context_and_matrix(self) -> None:
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["findings"] = [
            {
                "id": "exp_t5_finding_001",
                "statement": "T5 improves replace following.",
                "confidence": "medium",
                "evidence": ["exp_t5"],
                "outcome": "positive",
                "metrics": ["replace_following"],
                "linked_artifacts": [],
                "created_at": "2026-04-27",
            }
        ]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        nodes = load_nodes(self.root)

        context = node_context(nodes["exp_t5"])
        rows = build_experiment_matrix(nodes)

        self.assertEqual(context["findings"][0]["statement"], "T5 improves replace following.")
        self.assertEqual(rows[0]["findings_count"], 1)
        self.assertEqual(rows[0]["latest_finding"], "T5 improves replace following.")

    def test_invalid_finding_fields_report_validation_error(self) -> None:
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["findings"] = [
            {
                "id": "bad_finding",
                "statement": "Bad finding.",
                "confidence": "certain",
                "outcome": "great",
                "linked_artifacts": ["missing_artifact"],
            }
        ]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        nodes = load_nodes(self.root)

        with self.assertRaises(ValidationError) as ctx:
            validate_cockpit(self.root, nodes, raise_on_error=True)

        message = str(ctx.exception)
        self.assertIn("findings[1].confidence", message)
        self.assertIn("findings[1].outcome", message)
        self.assertIn("missing_artifact", message)

    def test_option_workstream_fields_enter_node_context_and_validate(self) -> None:
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        option["agent_workstream"] = {
            "owner": "agent_t5",
            "status": "in_progress",
            "objective": "Evaluate T5 branch.",
            "report_to_problem": "problem_text",
            "started_at": "2026-04-28",
            "updated_at": "2026-04-28",
        }
        option["workstream_report"] = {
            "reporting_agent": "agent_t5",
            "recommendation": "continue",
            "summary": "Need one more ablation.",
            "evidence_summary": "No findings yet.",
            "experiment_count": 1,
            "finding_count": 0,
            "reported_at": "2026-04-28",
        }
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", option)
        nodes = load_nodes(self.root)

        errors = validate_cockpit(self.root, nodes)
        context = node_context(nodes["option_t5"])

        self.assertEqual(errors, [])
        self.assertEqual(context["agent_workstream"]["owner"], "agent_t5")
        self.assertEqual(context["workstream_report"]["recommendation"], "continue")

    def test_experiment_assignment_fields_enter_node_context_and_validate(self) -> None:
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment.update({
            "priority": "high",
            "order": "p2.2",
            "owner": "agent_t5",
            "ready_for_agent": True,
            "depends_on": ["problem_text"],
            "blocked_by": ["problem_text"],
            "handoff_context": "Run T5 gate and record one finding.",
        })
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        nodes = load_nodes(self.root)

        errors = validate_cockpit(self.root, nodes)
        context = node_context(nodes["exp_t5"])

        self.assertEqual(errors, [])
        self.assertEqual(context["owner"], "agent_t5")
        self.assertIs(context["ready_for_agent"], True)
        self.assertEqual(context["depends_on"], ["problem_text"])
        self.assertEqual(context["blocked_by"], ["problem_text"])
        self.assertEqual(context["handoff_context"], "Run T5 gate and record one finding.")

    def test_experiment_assignment_fields_validate_type_and_refs(self) -> None:
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["ready_for_agent"] = True
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["ready_for_agent"] = "yes"
        experiment["depends_on"] = ["missing_dependency"]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        nodes = load_nodes(self.root)

        with self.assertRaises(ValidationError) as ctx:
            validate_cockpit(self.root, nodes, raise_on_error=True)

        message = str(ctx.exception)
        self.assertIn("assignment fields are only supported on experiment nodes", message)
        self.assertIn("ready_for_agent must be a boolean", message)
        self.assertIn("depends_on references missing node 'missing_dependency'", message)

    def test_workstream_fields_are_only_valid_on_options(self) -> None:
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["agent_workstream"] = {"owner": "agent_bad", "status": "claimed"}
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        nodes = load_nodes(self.root)

        with self.assertRaises(ValidationError) as ctx:
            validate_cockpit(self.root, nodes, raise_on_error=True)

        self.assertIn("agent_workstream is only supported on option nodes", str(ctx.exception))

    def test_workstream_status_and_report_recommendation_validate(self) -> None:
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        option["agent_workstream"] = {"owner": "agent_t5", "status": "invalid"}
        option["workstream_report"] = {"reporting_agent": "agent_t5", "recommendation": "maybe"}
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", option)
        nodes = load_nodes(self.root)

        with self.assertRaises(ValidationError) as ctx:
            validate_cockpit(self.root, nodes, raise_on_error=True)

        message = str(ctx.exception)
        self.assertIn("agent_workstream.status invalid", message)
        self.assertIn("workstream_report.recommendation invalid", message)

    def test_option_subtree_resolves_nested_problem_option_experiment(self) -> None:
        write_node(
            self.root,
            {
                "id": "problem_sub",
                "type": "problem",
                "title": "Sub problem",
                "status": "active",
                "parent": "option_t5",
                "children": ["option_sub"],
            },
        )
        write_node(
            self.root,
            {
                "id": "option_sub",
                "type": "option",
                "title": "Sub option",
                "status": "open",
                "parent": "problem_sub",
                "children": ["exp_sub"],
            },
        )
        write_node(
            self.root,
            {
                "id": "exp_sub",
                "type": "experiment",
                "title": "Sub experiment",
                "status": "done",
                "parent": "option_sub",
                "result_summary": "Sub branch improved.",
            },
        )
        nodes = load_nodes(self.root)

        subtree = build_option_subtree(nodes, "option_t5")

        self.assertEqual(subtree["root_option_id"], "option_t5")
        self.assertIn("problem_sub", subtree["problem_ids"])
        self.assertIn("option_sub", subtree["option_ids"])
        self.assertIn("exp_sub", subtree["experiment_ids"])

    def test_option_workstream_rows_use_supplied_graph_topology(self) -> None:
        nodes = load_nodes(self.root)
        topology = GraphTopology.from_nodes(nodes)

        with patch("research_cockpit.option_workstreams.child_ids", side_effect=AssertionError("duplicate child scan")):
            rows = build_option_workstream_rows(nodes, topology=topology)

        self.assertEqual(rows[0]["option_id"], "option_t5")
        self.assertEqual(rows[0]["experiment_count"], 1)

    def test_option_workstream_helpers_handle_deep_topology_without_recursion(self) -> None:
        depth = 1200
        nodes: dict[str, ResearchNode] = {}
        for index in range(depth):
            node_id = f"node_{index:04d}"
            parent_id = f"node_{index - 1:04d}" if index > 0 else None
            child_id = f"node_{index + 1:04d}" if index < depth - 1 else None
            nodes[node_id] = ResearchNode(
                id=node_id,
                type="option" if index % 2 == 0 else "problem",
                title=f"Node {index}",
                status="open",
                parent=parent_id,
                children=[child_id] if child_id else [],
            )
        nodes["exp_deep"] = ResearchNode(
            id="exp_deep",
            type="experiment",
            title="Deep experiment",
            status="done",
            parent=f"node_{depth - 1:04d}",
        )
        nodes[f"node_{depth - 1:04d}"].children.append("exp_deep")
        topology = GraphTopology.from_nodes(dict(reversed(list(nodes.items()))))

        subtree = build_option_subtree(nodes, "node_0000", topology=topology)
        rows = build_option_workstream_rows(nodes, topology=topology)

        self.assertEqual(subtree["node_ids"][0], "node_0000")
        self.assertIn("exp_deep", subtree["experiment_ids"])
        self.assertEqual(rows[0]["option_id"], "node_0000")
        self.assertEqual(rows[0]["experiment_count"], 1)

    def test_option_workstream_context_summarizes_recursive_evidence(self) -> None:
        write_node(
            self.root,
            {
                "id": "problem_sub",
                "type": "problem",
                "title": "Sub problem",
                "status": "active",
                "parent": "option_t5",
                "children": ["option_sub"],
                "next_actions": ["Try sub option"],
            },
        )
        write_node(
            self.root,
            {
                "id": "option_sub",
                "type": "option",
                "title": "Sub option",
                "status": "open",
                "parent": "problem_sub",
                "children": ["exp_sub"],
            },
        )
        write_node(
            self.root,
            {
                "id": "exp_sub",
                "type": "experiment",
                "title": "Sub experiment",
                "status": "done",
                "parent": "option_sub",
                "findings": [
                    {
                        "id": "exp_sub_finding_001",
                        "statement": "Sub branch helps.",
                        "confidence": "medium",
                        "outcome": "positive",
                    }
                ],
            },
        )
        save_yaml(
            self.root / "runs" / "run_sub.yaml",
            {
                "run_id": "run_sub",
                "status": "running",
                "experiment_id": "exp_sub",
                "started_at": "2026-05-27T01:00:00Z",
                "progress_file": "artifacts/exp_sub/run_sub/progress.json",
            },
        )
        nodes = load_nodes(self.root)
        current = load_yaml(self.root / "current_state.yaml")

        context = build_option_workstream_context(self.root, nodes, current, "option_t5")
        rows = build_option_workstream_rows(nodes)

        self.assertEqual(context["option"]["id"], "option_t5")
        self.assertEqual(context["upstream_problem"]["id"], "problem_text")
        self.assertIn("exp_sub", [item["id"] for item in context["experiments"]])
        self.assertEqual(context["evidence_summary"]["findings_count"], 1)
        self.assertEqual(context["run_summaries_by_experiment"]["exp_sub"]["active_run_ids"], ["run_sub"])
        self.assertIn("Try sub option", context["open_next_actions"])
        self.assertEqual(rows[0]["option_id"], "option_t5")

    def test_node_onboarding_context_for_option_includes_workstream_and_rooted_commands(self) -> None:
        nodes = load_nodes(self.root)
        current = load_yaml(self.root / "current_state.yaml")

        context = build_node_onboarding_context(self.root, nodes, current, "option_t5")

        self.assertEqual(context["node"]["id"], "option_t5")
        self.assertEqual([item["id"] for item in context["parent_chain"]], ["stage_text", "problem_text", "option_t5"])
        self.assertEqual(context["type_context"]["kind"], "option")
        self.assertEqual(context["type_context"]["workstream"]["option"]["id"], "option_t5")
        self.assertIn("evidence_summary", context["type_context"]["workstream"])
        hierarchy = context["type_context"]["workstream"]["hierarchy_policy"]
        self.assertEqual(hierarchy["workstream_file_hint"]["problem.parent"], "option_t5")
        self.assertIn("coord assign", hierarchy["recommended_command"])
        self.assertIn("create_child_workstream", context["type_context"]["workstream"]["suggested_commands"])
        for command in context["type_context"]["workstream"]["suggested_commands"].values():
            self.assertIn("--root", command)
            self.assertIn(str(self.root), command)
        self.assertIn("--root", context["command_drafts"]["claim_assignment"])
        self.assertIn(str(self.root), context["command_drafts"]["claim_assignment"])
        self.assertNotIn("python scripts", context["command_drafts"]["claim_assignment"])
        self.assertNotIn(".py", context["command_drafts"]["claim_assignment"])

    def test_node_onboarding_context_for_experiment_points_to_missing_evidence_and_record_command(self) -> None:
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["metrics"] = ["accuracy", "latency"]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        save_yaml(
            self.root / "runs" / "run_t5_active.yaml",
            {
                "run_id": "run_t5_active",
                "status": "running",
                "experiment_id": "exp_t5",
                "started_at": "2000-01-01T00:00:00Z",
                "command": "python train.py --full",
                "progress_file": "artifacts/exp_t5/run_t5_active/progress.json",
                "monitor_command": "tail -f artifacts/exp_t5/run_t5_active/logs/run.log",
                "stop_command": "tmux kill-session -t t5",
            },
        )
        save_yaml(
            self.root / "runs" / "run_t5_done.yaml",
            {
                "run_id": "run_t5_done",
                "status": "completed",
                "experiment_id": "exp_t5",
                "started_at": "2026-05-26T01:00:00Z",
                "finished_at": "2026-05-26T02:00:00Z",
            },
        )
        nodes = load_nodes(self.root)
        current = load_yaml(self.root / "current_state.yaml")

        context = build_node_onboarding_context(self.root, nodes, current, "exp_t5")
        compact = build_node_onboarding_context(self.root, nodes, current, "exp_t5", compact=True)

        self.assertEqual(context["type_context"]["kind"], "experiment")
        self.assertEqual(context["type_context"]["parent_option"]["id"], "option_t5")
        self.assertEqual(context["type_context"]["metrics"], ["accuracy", "latency"])
        self.assertTrue(context["type_context"]["missing_evidence"])
        self.assertEqual(context["type_context"]["runs"]["summary"]["total_count"], 2)
        self.assertEqual(context["type_context"]["runs"]["current"][0]["run_id"], "run_t5_active")
        self.assertTrue(context["type_context"]["runs"]["current"][0]["possibly_stale"])
        self.assertEqual(compact["run_summary"]["total_count"], 2)
        self.assertEqual(compact["run_summary"]["active_run_ids"], ["run_t5_active"])
        self.assertEqual(compact["run_summary"]["recent_run_ids"], ["run_t5_done", "run_t5_active"])
        self.assertNotIn("python train.py", str(compact["run_summary"]))
        hierarchy = context["type_context"]["hierarchy_policy"]
        self.assertEqual(hierarchy["workstream_file_hint"]["problem.parent"], "option_t5")
        self.assertEqual(hierarchy["source_experiment_id"], "exp_t5")
        self.assertIn("create_child_workstream", context["type_context"]["suggested_commands"])
        self.assertIn("start", context["type_context"]["suggested_commands"])
        self.assertIn("record_evidence", context["type_context"]["suggested_commands"])
        self.assertIn("close_assignment", context["type_context"]["suggested_commands"])
        self.assertIn("work record", context["command_drafts"]["record_evidence"])
        self.assertIn("--root", context["command_drafts"]["record_evidence"])

    def test_node_onboarding_context_for_decision_includes_acceptance_repairs(self) -> None:
        decision = load_yaml(self.root / "graph" / "nodes" / "decision_t5.yaml")
        decision["supporting_experiments"] = ["exp_t5"]
        save_yaml(self.root / "graph" / "nodes" / "decision_t5.yaml", decision)
        nodes = load_nodes(self.root)
        current = load_yaml(self.root / "current_state.yaml")

        context = build_node_onboarding_context(self.root, nodes, current, "decision_t5")

        self.assertEqual(context["type_context"]["kind"], "decision")
        self.assertFalse(context["type_context"]["acceptance"]["ready"])
        failed_ids = {item["id"] for item in context["type_context"]["acceptance"]["blocking_failures"]}
        self.assertIn("supporting_evidence", failed_ids)
        repair_commands = " ".join(item.get("command", "") for item in context["type_context"]["repair_hints"])
        self.assertIn("work record", repair_commands)
        self.assertIn("coord decide", repair_commands)
        self.assertIn("--root", repair_commands)

    def test_branch_comparison_summarizes_problem_options(self) -> None:
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        option["pros"] = ["Uses strong text prior"]
        option["cons"] = ["Needs cache"]
        option["evidence_strength"] = "medium"
        save_yaml(self.root / "graph" / "nodes" / "option_t5.yaml", option)

        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["result_summary"] = "Improved edit following."
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)

        current = load_yaml(self.root / "current_state.yaml")
        current["current_option"] = "option_t5"
        nodes = load_nodes(self.root)

        rows = build_branch_comparison(nodes, current=current)

        self.assertEqual(rows[0]["id"], "option_t5")
        self.assertEqual(rows[0]["evidence_strength"], "medium")
        self.assertEqual(rows[0]["experiment_count"], 1)
        self.assertEqual(rows[0]["latest_result"], "Improved edit following.")
        self.assertTrue(rows[0]["is_current_best"])

    def test_branch_comparison_counts_nested_option_experiments(self) -> None:
        write_node(
            self.root,
            {
                "id": "problem_sub",
                "type": "problem",
                "title": "Sub problem",
                "status": "active",
                "parent": "option_t5",
            },
        )
        write_node(
            self.root,
            {
                "id": "option_sub",
                "type": "option",
                "title": "Sub option",
                "status": "open",
                "parent": "problem_sub",
            },
        )
        write_node(
            self.root,
            {
                "id": "exp_sub",
                "type": "experiment",
                "title": "Sub experiment",
                "status": "done",
                "parent": "option_sub",
                "result_summary": "Nested evidence.",
            },
        )
        nodes = load_nodes(self.root)

        rows = build_branch_comparison(nodes, current=load_yaml(self.root / "current_state.yaml"))

        self.assertEqual(rows[0]["experiment_count"], 2)
        self.assertEqual(rows[0]["latest_result"], "Nested evidence.")

    def test_decision_trace_resolves_context_and_evidence(self) -> None:
        write_node(
            self.root,
            {
                "id": "option_alt",
                "type": "option",
                "title": "Alternative option",
                "status": "open",
                "parent": "problem_text",
            },
        )
        decision = load_yaml(self.root / "graph" / "nodes" / "decision_t5.yaml")
        decision["supporting_experiments"] = ["exp_t5"]
        decision["alternatives_considered"] = ["option_alt"]
        decision["consequences"] = ["Prioritize T5 branch"]
        save_yaml(self.root / "graph" / "nodes" / "decision_t5.yaml", decision)
        nodes = load_nodes(self.root)

        trace = build_decision_trace(nodes, "decision_t5")

        self.assertEqual(trace["decision"]["id"], "decision_t5")
        self.assertEqual(trace["stage"]["id"], "stage_text")
        self.assertEqual(trace["problem"]["id"], "problem_text")
        self.assertEqual(trace["option"]["id"], "option_t5")
        self.assertEqual(trace["supporting_experiments"][0]["id"], "exp_t5")
        self.assertEqual(trace["alternatives_considered"][0]["id"], "option_alt")
        self.assertEqual(trace["consequences"], ["Prioritize T5 branch"])

    def test_decision_evidence_summary_counts_experiments_findings_and_outcomes(self) -> None:
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        experiment["outcome"] = "positive"
        experiment["findings"] = [
            {
                "id": "exp_t5_finding_001",
                "statement": "T5 improves replace following.",
                "confidence": "medium",
                "outcome": "positive",
            }
        ]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        nodes = load_nodes(self.root)

        summary = build_decision_evidence_summary(nodes, "decision_t5")

        self.assertEqual(summary["experiment_count"], 1)
        self.assertEqual(summary["findings_count"], 1)
        self.assertEqual(summary["outcome_counts"]["positive"], 1)
        self.assertEqual(summary["latest_finding"], "T5 improves replace following.")

    def test_decision_evidence_bundle_collects_structured_experiment_evidence(self) -> None:
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        experiment["result_summary"] = "Improves replace following."
        experiment["outcome"] = "positive"
        experiment["findings"] = [
            {
                "id": "exp_t5_finding_001",
                "statement": "T5 improves replace following.",
                "confidence": "strong",
                "outcome": "positive",
            }
        ]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        nodes = load_nodes(self.root)

        bundle = build_decision_evidence_bundle(nodes, "option_t5")

        self.assertEqual(bundle["supporting_experiments"], ["exp_t5"])
        self.assertEqual(bundle["evidence_strength"], "strong")
        self.assertEqual(bundle["findings_count"], 1)
        self.assertEqual(bundle["outcome_counts"]["positive"], 1)
        self.assertEqual(bundle["latest_finding"], "T5 improves replace following.")
        self.assertIn("1 experiment", bundle["evidence_summary"])

    def test_decision_evidence_bundle_collects_nested_option_evidence(self) -> None:
        write_node(
            self.root,
            {
                "id": "problem_sub",
                "type": "problem",
                "title": "Sub problem",
                "status": "active",
                "parent": "option_t5",
            },
        )
        write_node(
            self.root,
            {
                "id": "option_sub",
                "type": "option",
                "title": "Sub option",
                "status": "open",
                "parent": "problem_sub",
            },
        )
        write_node(
            self.root,
            {
                "id": "exp_sub",
                "type": "experiment",
                "title": "Sub experiment",
                "status": "done",
                "parent": "option_sub",
                "findings": [
                    {
                        "id": "exp_sub_finding_001",
                        "statement": "Nested branch helps.",
                        "confidence": "strong",
                        "outcome": "positive",
                    }
                ],
            },
        )
        nodes = load_nodes(self.root)

        bundle = build_decision_evidence_bundle(nodes, "option_t5")

        self.assertEqual(bundle["supporting_experiments"], ["exp_sub"])
        self.assertEqual(bundle["evidence_strength"], "strong")

    def test_decision_evidence_bundle_keeps_manual_planned_experiment_and_none_without_evidence(self) -> None:
        nodes = load_nodes(self.root)

        automatic = build_decision_evidence_bundle(nodes, "option_t5")
        manual = build_decision_evidence_bundle(nodes, "option_t5", supporting_experiments=["exp_t5"])

        self.assertEqual(automatic["supporting_experiments"], [])
        self.assertEqual(automatic["evidence_strength"], "none")
        self.assertEqual(manual["supporting_experiments"], ["exp_t5"])
        self.assertEqual(manual["evidence_strength"], "none")

    def test_decision_acceptance_checklist_ready_for_complete_decision(self) -> None:
        write_node(
            self.root,
            {
                "id": "option_alt",
                "type": "option",
                "title": "Alternative option",
                "status": "open",
                "parent": "problem_text",
            },
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["status"] = "done"
        experiment["result_summary"] = "Improves replace following."
        experiment["outcome"] = "positive"
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        decision = load_yaml(self.root / "graph" / "nodes" / "decision_t5.yaml")
        decision.update({
            "supporting_experiments": ["exp_t5"],
            "evidence_strength": "medium",
            "evidence_summary": "1 experiment; outcome positive",
            "alternatives_considered": ["option_alt"],
            "consequences": ["Prioritize T5 branch."],
            "next_required_actions": ["Run CLAP ablation."],
        })
        save_yaml(self.root / "graph" / "nodes" / "decision_t5.yaml", decision)
        nodes = load_nodes(self.root)

        checklist = build_decision_acceptance_checklist(nodes, "decision_t5")

        self.assertTrue(checklist["ready"])
        self.assertEqual(checklist["blocking_failures"], [])
        self.assertEqual(checklist["warnings"], [])

    def test_decision_acceptance_checklist_reports_missing_required_fields(self) -> None:
        nodes = load_nodes(self.root)

        checklist = build_decision_acceptance_checklist(nodes, "decision_t5")

        failed_ids = {item["id"] for item in checklist["blocking_failures"]}
        self.assertFalse(checklist["ready"])
        self.assertIn("supporting_experiments", failed_ids)
        self.assertIn("supporting_evidence", failed_ids)
        self.assertIn("evidence_strength", failed_ids)
        self.assertIn("evidence_summary", failed_ids)
        self.assertIn("alternatives_considered", failed_ids)
        self.assertIn("consequences", failed_ids)
        self.assertIn("next_required_actions", failed_ids)

    def test_decision_acceptance_checklist_warns_for_weak_evidence(self) -> None:
        write_node(
            self.root,
            {
                "id": "option_alt",
                "type": "option",
                "title": "Alternative option",
                "status": "open",
                "parent": "problem_text",
            },
        )
        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["result_summary"] = "Directional improvement only."
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        decision = load_yaml(self.root / "graph" / "nodes" / "decision_t5.yaml")
        decision.update({
            "supporting_experiments": ["exp_t5"],
            "evidence_strength": "weak",
            "evidence_summary": "Weak directional evidence.",
            "alternatives_considered": ["option_alt"],
            "consequences": ["Proceed cautiously."],
            "next_required_actions": ["Run confirmation experiment."],
        })
        save_yaml(self.root / "graph" / "nodes" / "decision_t5.yaml", decision)
        nodes = load_nodes(self.root)

        checklist = build_decision_acceptance_checklist(nodes, "decision_t5")

        self.assertTrue(checklist["ready"])
        self.assertEqual([item["id"] for item in checklist["warnings"]], ["weak_evidence"])

    def test_decision_acceptance_checklist_reports_bad_parent_and_refs(self) -> None:
        decision = load_yaml(self.root / "graph" / "nodes" / "decision_t5.yaml")
        decision["parent"] = "problem_text"
        decision["supporting_experiments"] = ["missing_exp"]
        save_yaml(self.root / "graph" / "nodes" / "decision_t5.yaml", decision)
        nodes = load_nodes(self.root)

        checklist = build_decision_acceptance_checklist(nodes, "decision_t5")

        failed_ids = {item["id"] for item in checklist["blocking_failures"]}
        self.assertIn("decision_parent", failed_ids)
        self.assertIn("supporting_experiments", failed_ids)

    def test_review_decision_suggestion_uses_evidence_update_command(self) -> None:
        nodes = load_nodes(self.root)
        current = load_yaml(self.root / "current_state.yaml")

        suggestions = build_action_suggestions(self.root, nodes, current)
        review = [item for item in suggestions if item["kind"] == "review_decision"][0]

        self.assertIn("research-cockpit coord decide", review["suggested_command"])
        self.assertIn("--file <coord_decide.yaml>", review["suggested_command"])

    def test_v2_statuses_and_current_focus_node_pass_validation(self) -> None:
        write_node(
            self.root,
            {
                "id": "artifact_cache",
                "type": "artifact",
                "title": "Feature cache",
                "status": "draft",
                "summary": "FLAN-T5 feature cache.",
            },
        )
        write_node(
            self.root,
            {
                "id": "exp_cancelled",
                "type": "experiment",
                "title": "Cancelled run",
                "status": "cancelled",
                "summary": "Superseded before launch.",
            },
        )
        current = load_yaml(self.root / "current_state.yaml")
        current["current_focus_node"] = "problem_text"
        current["focus_mode"] = {
            "default_depth": 2,
            "hide_statuses": ["rejected", "parked", "archived"],
            "show_resolved": False,
            "show_rejected": False,
            "show_parked": False,
        }
        save_yaml(self.root / "current_state.yaml", current)
        nodes = load_nodes(self.root)

        errors = validate_cockpit(self.root, nodes)

        self.assertEqual(errors, [])

    def test_unknown_current_focus_node_reports_reference(self) -> None:
        current = load_yaml(self.root / "current_state.yaml")
        current["current_focus_node"] = "missing_focus"
        save_yaml(self.root / "current_state.yaml", current)
        nodes = load_nodes(self.root)

        with self.assertRaises(ValidationError) as ctx:
            validate_cockpit(self.root, nodes, raise_on_error=True)

        self.assertIn("current_focus_node", str(ctx.exception))
        self.assertIn("missing_focus", str(ctx.exception))

    def test_invalid_focus_mode_hide_status_reports_error(self) -> None:
        current = load_yaml(self.root / "current_state.yaml")
        current["focus_mode"] = {"hide_statuses": ["unknown_status"]}
        save_yaml(self.root / "current_state.yaml", current)
        nodes = load_nodes(self.root)

        with self.assertRaises(ValidationError) as ctx:
            validate_cockpit(self.root, nodes, raise_on_error=True)

        self.assertIn("focus_mode.hide_statuses", str(ctx.exception))
        self.assertIn("unknown_status", str(ctx.exception))

    def test_focus_context_pack_contains_local_context(self) -> None:
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["current_best_option"] = "option_t5"
        problem["blockers"] = ["Need feature cache"]
        problem["next_actions"] = ["Run focused ablation"]
        problem["agent_context"] = {
            "include": True,
            "role": "focus",
            "key_files": ["ltx_trainer/modules/semantic_ribbon_vnext.py"],
            "next_action_hint": "Implement the focused feature cache.",
        }
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)

        experiment = load_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml")
        experiment["linked_artifacts"] = ["artifact_cache"]
        save_yaml(self.root / "graph" / "nodes" / "exp_t5.yaml", experiment)
        write_node(
            self.root,
            {
                "id": "artifact_cache",
                "type": "artifact",
                "title": "Feature cache",
                "status": "active",
                "summary": "FLAN-T5 feature cache.",
            },
        )

        current = load_yaml(self.root / "current_state.yaml")
        current["current_focus_node"] = "problem_text"
        save_yaml(self.root / "current_state.yaml", current)
        nodes = load_nodes(self.root)

        context = build_focus_context(self.root, nodes)

        self.assertEqual(context["focus_node"]["id"], "problem_text")
        self.assertEqual(context["current_best_option"], "option_t5")
        self.assertEqual(context["blockers"], ["Need feature cache"])
        self.assertIn("Run ablation", context["next_actions"])
        self.assertIn("Run focused ablation", context["next_actions"])
        self.assertEqual(context["local_neighbors"]["parents"][0]["id"], "stage_text")
        self.assertEqual(context["local_neighbors"]["children"][0]["id"], "option_t5")
        self.assertEqual(context["local_neighbors"]["experiments"][0]["id"], "exp_t5")
        self.assertEqual(context["local_neighbors"]["decisions"][0]["id"], "decision_t5")
        self.assertEqual(context["local_neighbors"]["artifacts"][0]["id"], "artifact_cache")
        self.assertEqual(context["knowledge_index"][0]["node_id"], "problem_text")


if __name__ == "__main__":
    unittest.main()
