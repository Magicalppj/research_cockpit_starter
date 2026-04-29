from __future__ import annotations

import shutil
import unittest
import uuid
from datetime import date
from pathlib import Path
import os
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT_DIR
sys.path.insert(0, str(ROOT_DIR / "src"))

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
    load_graph_views,
    load_interaction_log,
    load_explicit_edges,
    load_yaml,
    load_nodes,
    node_context,
    python_command,
    save_yaml,
    search_knowledge,
    upsert_graph_view,
    validate_cockpit,
)


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

    def test_graph_json_adds_interaction_facets(self) -> None:
        problem = load_yaml(self.root / "graph" / "nodes" / "problem_text.yaml")
        problem["blockers"] = ["Need annotation policy"]
        save_yaml(self.root / "graph" / "nodes" / "problem_text.yaml", problem)
        option = load_yaml(self.root / "graph" / "nodes" / "option_t5.yaml")
        option["agent_workstream"] = {
            "owner": "agent_t5",
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
        self.assertTrue(graph_nodes["problem_text"]["has_blockers"])
        self.assertTrue(graph_nodes["exp_t5"]["has_evidence"])
        self.assertFalse(graph_nodes["decision_t5"]["has_evidence"])
        self.assertTrue(graph_nodes["option_t5"]["in_current_branch"])
        self.assertIn("stage_text", graph["available_filters"]["stages"])
        self.assertIn("option_t5", graph["available_filters"]["workstreams"])

    def test_interaction_log_appends_events(self) -> None:
        event = append_interaction_log(
            self.root,
            kind="set_focus",
            actor="researcher",
            node_id="problem_text",
            command="python scripts\\set_focus.py --focus-node problem_text",
            before={"current_focus_node": "option_t5"},
            after={"current_focus_node": "problem_text"},
        )

        log = load_interaction_log(self.root)

        self.assertEqual(event["kind"], "set_focus")
        self.assertEqual(log["events"][0]["node_id"], "problem_text")
        self.assertEqual(log["events"][0]["before"]["current_focus_node"], "option_t5")

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
        self.assertTrue(views[0]["filters"]["only_blocking"])
        self.assertFalse(views[0]["filters"]["only_next_actions"])
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
        self.assertIn("scripts\\record_finding.py", by_kind["record_finding"]["suggested_command"])
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

            self.assertEqual(python_command(), "python")
            self.assertTrue(commands)
            self.assertTrue(all(command.startswith("python scripts\\") for command in commands))
            self.assertFalse(any("D:\\Tools" in command for command in commands))
            self.assertFalse(any("miniconda" in command.lower() for command in commands))
        finally:
            if previous is not None:
                os.environ["RESEARCH_COCKPIT_PYTHON"] = previous

    def test_python_command_allows_environment_override(self) -> None:
        previous = os.environ.get("RESEARCH_COCKPIT_PYTHON")
        os.environ["RESEARCH_COCKPIT_PYTHON"] = "uv run python"
        try:
            self.assertEqual(python_command(), "uv run python")
        finally:
            if previous is None:
                os.environ.pop("RESEARCH_COCKPIT_PYTHON", None)
            else:
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
        nodes = load_nodes(self.root)
        current = load_yaml(self.root / "current_state.yaml")

        context = build_option_workstream_context(self.root, nodes, current, "option_t5")
        rows = build_option_workstream_rows(nodes)

        self.assertEqual(context["option"]["id"], "option_t5")
        self.assertEqual(context["upstream_problem"]["id"], "problem_text")
        self.assertIn("exp_sub", [item["id"] for item in context["experiments"]])
        self.assertEqual(context["evidence_summary"]["findings_count"], 1)
        self.assertIn("Try sub option", context["open_next_actions"])
        self.assertEqual(rows[0]["option_id"], "option_t5")

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

        self.assertIn("scripts\\update_decision_evidence.py", review["suggested_command"])
        self.assertIn("--id decision_t5", review["suggested_command"])

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
