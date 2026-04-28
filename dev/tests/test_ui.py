from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

ROOT_DIR = Path(__file__).resolve().parents[2]
SKILL_ROOT = ROOT_DIR / "skills" / "research-cockpit"
sys.path.insert(0, str(SKILL_ROOT))

from ui.app import build_pyvis_html
from ui.view_helpers import (
    build_accept_decision_command,
    build_apply_suggestion_command,
    build_check_decision_acceptance_command,
    build_cleanup_suggestion_lifecycle_command,
    build_create_note_command,
    build_promote_decision_command,
    build_record_finding_command,
    build_set_focus_command,
    build_update_suggestion_state_command,
    default_detail_node_id,
    default_selected_statuses,
    edge_style_for_type,
    format_comparison_rows,
    format_decision_checklist,
    format_finding_rows,
    format_resource_rows,
    format_action_suggestion_rows,
    format_evidence_summary,
    format_suggestion_lifecycle_rows,
    filter_action_suggestions,
    filter_graph_for_view,
    filter_search_results,
    filter_node_ids,
    format_node_option,
    format_resource_index_rows,
    format_search_result_rows,
    ordered_tab_labels,
)


class UiRenderingTests(unittest.TestCase):
    def test_app_multiselects_use_explicit_keys(self) -> None:
        source = (SKILL_ROOT / "ui" / "app.py").read_text(encoding="utf-8")
        start = 0
        calls = []
        while True:
            index = source.find(".multiselect(", start)
            if index == -1:
                break
            cursor = index + len(".multiselect(")
            depth = 1
            while cursor < len(source) and depth:
                char = source[cursor]
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                cursor += 1
            calls.append(source[index:cursor])
            start = cursor

        self.assertGreater(len(calls), 0)
        for call in calls:
            self.assertIn("key=", call)

    def test_pyvis_html_generation_supports_chinese_without_file_encoding(self) -> None:
        graph = {
            "nodes": [
                {
                    "id": "stage_text_encoder",
                    "label": "文本编码阶段",
                    "title": "中文摘要",
                    "type": "stage",
                    "status": "active",
                    "color": "#FFE9A8",
                    "shape": "box",
                    "is_focus": True,
                }
            ],
            "edges": [],
        }

        html = build_pyvis_html(graph, {"stage"}, {"active"})

        self.assertIn("<html>", html)
        self.assertIn("\\u6587\\u672c\\u7f16\\u7801\\u9636\\u6bb5", html)
        self.assertIn("charset=\"utf-8\"", html)
        self.assertIsInstance(html.encode("utf-8"), bytes)

    def test_focus_view_filters_hidden_nodes(self) -> None:
        graph = {
            "nodes": [
                {
                    "id": "problem_text",
                    "label": "Weak text",
                    "title": "Focus problem",
                    "type": "problem",
                    "status": "active",
                    "color": "#FFE9A8",
                    "shape": "diamond",
                    "is_focus_visible": True,
                    "focus_visible_depth": 0,
                },
                {
                    "id": "option_t5",
                    "label": "T5",
                    "title": "Active option",
                    "type": "option",
                    "status": "active",
                    "color": "#FFE9A8",
                    "shape": "box",
                    "is_focus_visible": True,
                    "focus_visible_depth": 1,
                },
                {
                    "id": "option_old",
                    "label": "Old",
                    "title": "Rejected unrelated branch",
                    "type": "option",
                    "status": "rejected",
                    "color": "#F3D0D0",
                    "shape": "box",
                    "is_focus_visible": False,
                    "focus_visible_depth": None,
                },
            ],
            "edges": [
                {"from": "problem_text", "to": "option_t5", "relation": "child"},
                {"from": "option_old", "to": "problem_text", "relation": "child"},
            ],
            "current_focus_node": "problem_text",
        }

        filtered = filter_graph_for_view(graph, "focus_depth_2", {"problem", "option"}, {"active", "rejected"})

        self.assertEqual({node["id"] for node in filtered["nodes"]}, {"problem_text", "option_t5"})
        self.assertEqual(filtered["edges"], [{"from": "problem_text", "to": "option_t5", "relation": "child"}])

    def test_pyvis_html_focuses_current_node(self) -> None:
        graph = {
            "nodes": [
                {
                    "id": "problem_text",
                    "label": "Weak text",
                    "title": "Focus problem",
                    "type": "problem",
                    "status": "active",
                    "color": "#FFE9A8",
                    "shape": "diamond",
                    "is_current_focus": True,
                }
            ],
            "edges": [],
            "current_focus_node": "problem_text",
        }

        html = build_pyvis_html(graph, {"problem"}, {"active"}, focus_node_id="problem_text")

        self.assertIn('network.focus("problem_text"', html)
        self.assertIn("scale: 1.25", html)

    def test_default_detail_and_tab_order_prioritize_research_graph(self) -> None:
        graph = {"current_focus_node": "problem_text"}
        text = {"research_graph": "研究图谱", "dashboard": "总览", "experiment_matrix": "实验矩阵"}

        self.assertEqual(default_detail_node_id(graph, ["stage_text", "problem_text"]), "problem_text")
        self.assertEqual(ordered_tab_labels(text)[0], "研究图谱")

    def test_default_status_filter_uses_focus_mode_hide_statuses(self) -> None:
        graph = {"focus_mode": {"hide_statuses": ["rejected", "parked", "archived"]}}

        defaults = default_selected_statuses(graph, ["active", "archived", "parked", "rejected"])

        self.assertEqual(defaults, ["active"])

    def test_build_set_focus_command_includes_focus_node(self) -> None:
        current = {
            "current_stage": "stage_text",
            "current_problem": "problem_text",
            "current_option": "option_t5",
            "current_focus_path": ["stage_text", "problem_text", "option_t5"],
        }

        command = build_set_focus_command(current, "exp_t5")

        self.assertIn("scripts\\set_focus.py", command)
        self.assertIn("--focus-node exp_t5", command)
        self.assertNotIn("--path", command)

    def test_node_select_label_includes_title_and_id(self) -> None:
        nodes = {
            "problem_text": SimpleNamespace(
                id="problem_text",
                title="Weak text control",
                type="problem",
                status="active",
            )
        }

        label = format_node_option(nodes, "problem_text")

        self.assertIn("Weak text control", label)
        self.assertIn("problem_text", label)
        self.assertIn("problem/active", label)

    def test_tab_order_includes_comparison_and_trace_views(self) -> None:
        text = {
            "research_graph": "研究图谱",
            "dashboard": "总览",
            "branch_comparison": "方案比较",
            "decision_trace": "决策追踪",
            "action_guidance": "行动建议",
            "search": "搜索",
            "resources": "资源",
            "experiment_matrix": "实验矩阵",
            "decisions": "决策",
            "agent_context": "Agent 上下文",
            "data_health": "数据健康",
        }

        labels = ordered_tab_labels(text)

        self.assertEqual(labels[0], "研究图谱")
        self.assertIn("方案比较", labels)
        self.assertIn("决策追踪", labels)
        self.assertIn("行动建议", labels)
        self.assertIn("搜索", labels)
        self.assertIn("资源", labels)

    def test_format_comparison_rows_keeps_tables_readable(self) -> None:
        rows = [
            {
                "id": "option_t5",
                "title": "T5",
                "pros": ["strong text prior", "simple"],
                "cons": [],
                "is_current_best": True,
            }
        ]

        formatted = format_comparison_rows(rows)

        self.assertEqual(formatted[0]["pros"], "strong text prior; simple")
        self.assertEqual(formatted[0]["cons"], "")
        self.assertEqual(formatted[0]["is_current_best"], "yes")

    def test_edge_style_for_type_maps_explicit_edge_types(self) -> None:
        self.assertEqual(edge_style_for_type("supports")["color"], "#16A34A")
        self.assertEqual(edge_style_for_type("contradicts")["color"], "#DC2626")
        self.assertEqual(edge_style_for_type("unknown")["color"], "#888888")

    def test_workflow_command_templates_are_copyable(self) -> None:
        previous = os.environ.pop("RESEARCH_COCKPIT_PYTHON", None)
        self.addCleanup(
            lambda: (
                os.environ.__setitem__("RESEARCH_COCKPIT_PYTHON", previous)
                if previous is not None
                else os.environ.pop("RESEARCH_COCKPIT_PYTHON", None)
            )
        )
        finding_command = build_record_finding_command("exp_t5")
        decision_command = build_promote_decision_command("option_t5")
        check_command = build_check_decision_acceptance_command("decision_t5")
        accept_command = build_accept_decision_command("decision_t5")
        note_command = build_create_note_command("problem_text")
        commands = [finding_command, decision_command, check_command, accept_command, note_command]

        self.assertTrue(all(command.startswith("python scripts\\") for command in commands))
        self.assertFalse(any("D:\\Tools" in command for command in commands))
        self.assertFalse(any("miniconda" in command.lower() for command in commands))
        self.assertIn("scripts\\record_finding.py", finding_command)
        self.assertIn("--experiment exp_t5", finding_command)
        self.assertIn("--confidence medium", finding_command)
        self.assertIn("scripts\\promote_decision.py", decision_command)
        self.assertIn("--option option_t5", decision_command)
        self.assertIn("--status proposed", decision_command)
        self.assertIn("scripts\\check_decision_acceptance.py", check_command)
        self.assertIn("--id decision_t5", check_command)
        self.assertIn("scripts\\accept_decision.py", accept_command)
        self.assertIn("--id decision_t5", accept_command)
        self.assertIn("scripts\\create_note.py", note_command)
        self.assertIn("--node problem_text", note_command)

    def test_workflow_command_templates_use_python_env_override(self) -> None:
        previous = os.environ.get("RESEARCH_COCKPIT_PYTHON")
        os.environ["RESEARCH_COCKPIT_PYTHON"] = "uv run python"
        self.addCleanup(
            lambda: (
                os.environ.__setitem__("RESEARCH_COCKPIT_PYTHON", previous)
                if previous is not None
                else os.environ.pop("RESEARCH_COCKPIT_PYTHON", None)
            )
        )

        command = build_record_finding_command("exp_t5")

        self.assertTrue(command.startswith("uv run python scripts\\record_finding.py"))

    def test_format_finding_rows_keeps_missing_fields_readable(self) -> None:
        rows = format_finding_rows([
            {
                "id": "exp_t5_finding_001",
                "statement": "T5 improves replace following.",
                "confidence": "medium",
                "metrics": ["replace_following", "remove_success"],
                "linked_artifacts": [],
            }
        ])

        self.assertEqual(rows[0]["metrics"], "replace_following; remove_success")
        self.assertEqual(rows[0]["linked_artifacts"], "")

    def test_filter_node_ids_matches_common_node_fields(self) -> None:
        nodes = {
            "problem_text": SimpleNamespace(
                id="problem_text",
                title="Weak text control",
                type="problem",
                status="active",
                summary="Event-level control",
                tags=["text"],
            ),
            "option_t5": SimpleNamespace(
                id="option_t5",
                title="T5 branch",
                type="option",
                status="promising",
                summary="Encoder option",
                tags=["encoder"],
            ),
        }

        self.assertEqual(filter_node_ids(nodes, "event"), ["problem_text"])
        self.assertEqual(filter_node_ids(nodes, "encoder"), ["option_t5"])
        self.assertEqual(filter_node_ids(nodes, ""), ["option_t5", "problem_text"])

    def test_format_resource_rows_maps_exists_for_display(self) -> None:
        rows = format_resource_rows([
            {"node_id": "problem_text", "target": "notes/problem.md", "exists": True},
            {"node_id": "exp_t5", "target": "https://example.com", "exists": None},
            {"node_id": "artifact_fig", "target": "figures/missing.png", "exists": False},
        ])

        self.assertEqual(rows[0]["exists"], "yes")
        self.assertEqual(rows[1]["exists"], "unknown")
        self.assertEqual(rows[2]["exists"], "missing")

    def test_format_resource_index_rows_marks_indexed_truncated_and_skipped(self) -> None:
        rows = format_resource_index_rows(
            [
                {"node_id": "problem_text", "label": "context", "target": "resources/context.txt", "exists": True},
                {"node_id": "problem_text", "label": "plot", "target": "figures/plot.png", "exists": True},
            ],
            [
                {
                    "source": "resource",
                    "node_id": "problem_text",
                    "resource_label": "context",
                    "target": "resources/context.txt",
                    "truncated": True,
                    "skip_reason": "",
                },
                {
                    "source": "resource",
                    "node_id": "problem_text",
                    "resource_label": "plot",
                    "target": "figures/plot.png",
                    "truncated": False,
                    "skip_reason": "unsupported_extension",
                },
            ],
        )

        self.assertEqual(rows[0]["indexed"], "yes")
        self.assertEqual(rows[0]["truncated"], "yes")
        self.assertEqual(rows[0]["skip_reason"], "")
        self.assertEqual(rows[1]["indexed"], "")
        self.assertEqual(rows[1]["skip_reason"], "unsupported_extension")

    def test_action_suggestion_helpers_format_and_filter_rows(self) -> None:
        suggestions = [
            {
                "id": "s1",
                "kind": "run_experiment",
                "priority": "high",
                "action": "Run T5 ablation.",
                "reason": "Planned experiment.",
                "source_node_id": "exp_t5",
                "source_node_type": "experiment",
                "related_node_ids": ["option_t5"],
                "suggested_command": "scripts\\update_status.py --id exp_t5 --status running",
                "is_focus_related": True,
                "queued_in_current": True,
                "queued_in_node": False,
                "lifecycle_state": "active",
                "lifecycle_reason": "",
            },
            {
                "id": "s2",
                "kind": "fix_resource",
                "priority": "low",
                "action": "Create missing resource.",
                "reason": "Missing local file.",
                "source_node_id": "artifact_fig",
                "source_node_type": "artifact",
                "related_node_ids": [],
                "suggested_command": "",
                "is_focus_related": False,
                "queued_in_current": False,
                "queued_in_node": True,
                "lifecycle_state": "completed",
                "lifecycle_reason": "Done manually.",
            },
        ]

        rows = format_action_suggestion_rows(suggestions)
        filtered = filter_action_suggestions(suggestions, {"run_experiment"}, {"high"}, {"active"}, True)
        current_command = build_apply_suggestion_command("s1", "current")
        node_command = build_apply_suggestion_command("s1", "node")
        lifecycle_command = build_update_suggestion_state_command("s1", "completed")

        self.assertEqual(rows[0]["related_node_ids"], "option_t5")
        self.assertEqual(rows[0]["is_focus_related"], "yes")
        self.assertEqual(rows[0]["queued_in_current"], "yes")
        self.assertEqual(rows[1]["queued_in_node"], "yes")
        self.assertEqual(rows[1]["lifecycle_state"], "completed")
        self.assertEqual(rows[1]["lifecycle_reason"], "Done manually.")
        self.assertEqual([item["id"] for item in filtered], ["s1"])
        self.assertIn("scripts\\apply_suggestion.py", current_command)
        self.assertIn("--id s1", current_command)
        self.assertIn("--target node", node_command)
        self.assertIn("scripts\\update_suggestion_state.py", lifecycle_command)
        self.assertIn("--state completed", lifecycle_command)

    def test_suggestion_lifecycle_cleanup_helpers_format_rows_and_commands(self) -> None:
        rows = format_suggestion_lifecycle_rows([
            {
                "key": "sg_old",
                "state": "completed",
                "reason": "Done.",
                "updated_at": "2026-04-20",
                "action": "Old action",
                "kind": "run_experiment",
                "source_node_id": "exp_old",
                "active_match": False,
                "orphan": True,
                "age_days": 8,
            },
            {
                "key": "sg_current",
                "state": "dismissed",
                "reason": "",
                "updated_at": "not-a-date",
                "action": "Current action",
                "kind": "record_finding",
                "source_node_id": "exp_t5",
                "active_match": True,
                "orphan": False,
                "age_days": None,
            },
        ])
        dry_run_command = build_cleanup_suggestion_lifecycle_command(dry_run=True)
        clean_completed_command = build_cleanup_suggestion_lifecycle_command(
            dry_run=False,
            state="completed",
            older_than_days=7,
        )

        self.assertEqual(rows[0]["orphan"], "yes")
        self.assertEqual(rows[0]["active_match"], "")
        self.assertEqual(rows[0]["age_days"], 8)
        self.assertEqual(rows[1]["active_match"], "yes")
        self.assertEqual(rows[1]["age_days"], "")
        self.assertIn("scripts\\cleanup_suggestion_lifecycle.py", dry_run_command)
        self.assertIn("--dry-run", dry_run_command)
        self.assertIn("--state completed", clean_completed_command)
        self.assertIn("--older-than-days 7", clean_completed_command)
        self.assertNotIn("--dry-run", clean_completed_command)

    def test_search_helpers_format_and_filter_results(self) -> None:
        index = [
            {
                "entry_id": "node:problem_text",
                "source": "node",
                "node_id": "problem_text",
                "node_type": "problem",
                "node_title": "Weak text",
                "title": "Weak text",
                "path": "graph/nodes/problem_text.yaml",
                "text": "Needle appears in YAML summary.",
                "updated_at": "",
                "is_focus_related": True,
            },
            {
                "entry_id": "note:notes/free.md",
                "source": "note",
                "node_id": None,
                "node_type": None,
                "node_title": None,
                "title": "Free note",
                "path": "notes/free.md",
                "text": "Needle appears in an old note.",
                "updated_at": "",
                "is_focus_related": False,
            },
            {
                "entry_id": "resource:problem_text:context:resources/context.txt",
                "source": "resource",
                "node_id": "problem_text",
                "node_type": "problem",
                "node_title": "Weak text",
                "title": "context",
                "path": "resources/context.txt",
                "text": "Needle appears in a linked resource.",
                "updated_at": "",
                "is_focus_related": True,
                "truncated": False,
                "skip_reason": "",
            },
        ]

        results = filter_search_results(index, "needle", {"resource"}, {"problem"}, focus_only=True, limit=5)
        rows = format_search_result_rows(results)

        self.assertEqual([item["entry_id"] for item in results], ["resource:problem_text:context:resources/context.txt"])
        self.assertEqual(rows[0]["source"], "resource")
        self.assertEqual(rows[0]["node"], "problem_text")
        self.assertIn("Needle", rows[0]["snippet"])

    def test_format_evidence_summary_degrades_without_findings(self) -> None:
        summary = format_evidence_summary({
            "experiment_count": 1,
            "findings_count": 0,
            "outcome_counts": {},
            "latest_finding": None,
        })

        self.assertEqual(summary["experiment_count"], 1)
        self.assertEqual(summary["findings_count"], 0)
        self.assertEqual(summary["latest_finding"], "")

    def test_format_decision_checklist_rows_are_readable(self) -> None:
        rows = format_decision_checklist({
            "checks": [
                {
                    "id": "supporting_experiments",
                    "state": "fail",
                    "blocking": True,
                    "label": "Supporting experiments are present",
                    "reason": "At least one supporting experiment is required.",
                    "related_node_ids": ["exp_t5", "exp_clap"],
                },
                {
                    "id": "weak_evidence",
                    "state": "warning",
                    "blocking": False,
                    "label": "Evidence is weak",
                    "reason": "Review before acceptance.",
                    "related_node_ids": [],
                },
            ],
        })

        self.assertEqual(rows[0]["blocking"], "yes")
        self.assertEqual(rows[0]["related_node_ids"], "exp_t5; exp_clap")
        self.assertEqual(rows[1]["blocking"], "")
        self.assertEqual(rows[1]["state"], "warning")


if __name__ == "__main__":
    unittest.main()
