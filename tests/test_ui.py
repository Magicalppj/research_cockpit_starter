from __future__ import annotations

from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from ui.app import build_pyvis_html
from ui.view_helpers import (
    build_apply_suggestion_command,
    build_create_note_command,
    build_promote_decision_command,
    build_record_finding_command,
    build_set_focus_command,
    build_update_suggestion_state_command,
    default_detail_node_id,
    default_selected_statuses,
    edge_style_for_type,
    format_comparison_rows,
    format_finding_rows,
    format_resource_rows,
    format_action_suggestion_rows,
    format_evidence_summary,
    filter_action_suggestions,
    filter_graph_for_view,
    filter_search_results,
    filter_node_ids,
    format_node_option,
    format_search_result_rows,
    ordered_tab_labels,
)


class UiRenderingTests(unittest.TestCase):
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
        finding_command = build_record_finding_command("exp_t5")
        decision_command = build_promote_decision_command("option_t5")
        note_command = build_create_note_command("problem_text")

        self.assertIn("scripts\\record_finding.py", finding_command)
        self.assertIn("--experiment exp_t5", finding_command)
        self.assertIn("--confidence medium", finding_command)
        self.assertIn("scripts\\promote_decision.py", decision_command)
        self.assertIn("--option option_t5", decision_command)
        self.assertIn("--status proposed", decision_command)
        self.assertIn("scripts\\create_note.py", note_command)
        self.assertIn("--node problem_text", note_command)

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
        ]

        results = filter_search_results(index, "needle", {"node"}, {"problem"}, focus_only=True, limit=5)
        rows = format_search_result_rows(results)

        self.assertEqual([item["entry_id"] for item in results], ["node:problem_text"])
        self.assertEqual(rows[0]["source"], "node")
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


if __name__ == "__main__":
    unittest.main()
