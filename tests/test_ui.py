from __future__ import annotations

import inspect
import shutil
import tempfile
import time
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT_DIR
sys.path.insert(0, str(ROOT_DIR / "src"))

import research_cockpit.ui.app as ui_app
from research_cockpit.commands.build_dashboard import build_dashboard
from research_cockpit.ui.app import _load_graph_data, build_pyvis_html, dashboard_staleness, get_text
from research_cockpit.baselines import (
    build_accepted_decision_rows,
    build_accepted_option_rows,
    build_graph_baseline_metadata,
    build_baseline_overview_rows,
    build_set_baseline_command,
)
from research_cockpit.graph_core import graph_to_json
from research_cockpit.ui.view_helpers import (
    _collapsed_descendant_ids,
    baseline_command_problem_ids,
    build_accept_decision_command,
    build_check_decision_acceptance_command,
    build_claim_option_command,
    build_cleanup_suggestion_lifecycle_command,
    build_node_overview,
    build_option_workstream_context_command,
    build_promote_decision_command,
    build_report_option_workstream_command,
    build_update_decision_checklist_command,
    build_update_decision_evidence_command,
    default_detail_node_id,
    default_selected_node_types,
    default_selected_statuses,
    edge_style_for_type,
    format_comparison_rows,
    format_decision_checklist,
    format_decision_repair_hints,
    format_finding_rows,
    format_resource_rows,
    format_action_suggestion_rows,
    format_evidence_summary,
    format_suggestion_lifecycle_rows,
    filter_action_suggestions,
    filter_graph_for_view,
    filter_graph_for_view_with_visibility,
    revealable_child_ids_for_view,
    graph_view_state_from_saved_view,
    graph_filter_options,
    filter_search_results,
    filter_node_ids,
    format_node_option,
    format_resource_index_rows,
    format_search_result_rows,
    build_graph_component_payload,
    build_graph_component_base_payload,
    build_graph_component_payload_from_base,
    graph_cache_key,
    graph_component_event_id,
    graph_component_payload_cache_key,
    graph_component_selected_node_id,
    graph_filter_cache_key,
    ordered_tab_keys,
    ordered_tab_labels,
    reset_global_graph_filter_state,
)
from research_cockpit.ui.graph_component import graph_component_build_available


def copy_demo_research_cockpit_without_dashboards(target: Path) -> None:
    shutil.copytree(
        ROOT_DIR / "examples" / "demo_research_cockpit",
        target,
        ignore=shutil.ignore_patterns("dashboards"),
    )


class UiRenderingTests(unittest.TestCase):
    def test_coordination_page_uses_shared_snapshot_builder(self) -> None:
        expected = {
            "schema_version": "coordination_snapshot_v1",
            "changed": True,
            "revision": "coord-v1:test",
        }
        root = Path("coordination-root")
        with patch.object(
            ui_app,
            "build_coordination_snapshot",
            return_value=expected,
        ) as builder:
            result = ui_app._load_coordination_snapshot(
                root,
                statuses={"active"},
                page="page-token",
            )

        self.assertEqual(result, expected)
        builder.assert_called_once_with(
            root,
            limit=100,
            page="page-token",
            statuses={"active"},
        )

    def test_coordination_page_routes_before_full_graph_load(self) -> None:
        source = inspect.getsource(ui_app.main)

        self.assertLess(
            source.index('if page_key == "coordination"'),
            source.index("load_graph_data()"),
        )

    def test_update_decision_checklist_label_is_localized(self) -> None:
        text = get_text("中文")

        self.assertEqual(text["update_decision_checklist_command"], "更新决策检查清单命令")

    def test_app_multiselects_use_explicit_keys(self) -> None:
        source = (ROOT_DIR / "src" / "research_cockpit" / "ui" / "app.py").read_text(encoding="utf-8")
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

    def test_graph_tab_exposes_manual_refresh_button(self) -> None:
        source = (ROOT_DIR / "src" / "research_cockpit" / "ui" / "app.py").read_text(encoding="utf-8")
        refresh_index = source.find('key="graph_refresh_data"')

        self.assertNotEqual(refresh_index, -1)
        self.assertIn("on_click=clear_graph_data_cache", source[refresh_index:refresh_index + 250])
        self.assertNotIn("st.rerun()", source[refresh_index:refresh_index + 200])

    def test_graph_data_load_uses_truth_source_cache(self) -> None:
        source = (ROOT_DIR / "src" / "research_cockpit" / "ui" / "app.py").read_text(encoding="utf-8")

        self.assertIn("@st.cache_data", source)
        self.assertIn("def _truth_source_revision", source)
        self.assertIn("def _dashboard_revision", source)
        self.assertIn("truth_revision: tuple[tuple[str, int, int], ...]", source)
        self.assertIn("_truth_source_revision(RESEARCH_ROOT)", source)
        self.assertIn("_dashboard_revision(RESEARCH_ROOT)", source)
        self.assertIn("_load_graph_data_cached.clear()", source)

    def test_graph_data_load_prefers_generated_dashboard_files(self) -> None:
        tmp_parent = ROOT_DIR / ".test_tmp" / "ui_dashboard_loader"
        tmp_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp_parent) as temp_dir:
            root = Path(temp_dir) / "research_cockpit"
            copy_demo_research_cockpit_without_dashboards(root)
            build_dashboard(root)

            with (
                patch("research_cockpit.ui.app.graph_to_json", side_effect=AssertionError("rebuilt graph")),
                patch("research_cockpit.ui.app.build_agent_context", side_effect=AssertionError("rebuilt context")),
                patch(
                    "research_cockpit.ui.app.build_dashboard_read_models",
                    side_effect=AssertionError("rebuilt read models"),
                ),
            ):
                loaded = _load_graph_data(root)

        graph = loaded[2]
        context = loaded[3]
        validation_errors = loaded[4]
        link_rows = loaded[5]
        search_index = loaded[8]
        dashboard_status = loaded[11]

        self.assertFalse(validation_errors)
        self.assertTrue(graph["nodes"])
        self.assertNotIn("raw", graph["nodes"][0])
        self.assertIn("metadata", context)
        self.assertIsInstance(link_rows, list)
        self.assertIsInstance(search_index, list)
        self.assertTrue(dashboard_status["available"])
        self.assertFalse(dashboard_status["stale"])

    def test_graph_data_load_falls_back_when_dashboard_file_missing(self) -> None:
        tmp_parent = ROOT_DIR / ".test_tmp" / "ui_dashboard_missing_file"
        tmp_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp_parent) as temp_dir:
            root = Path(temp_dir) / "research_cockpit"
            copy_demo_research_cockpit_without_dashboards(root)
            build_dashboard(root)
            (root / "dashboards" / "search_index.json").unlink()

            with patch(
                "research_cockpit.ui.app.build_dashboard_read_models",
                wraps=ui_app.build_dashboard_read_models,
            ) as read_model_builder:
                loaded = _load_graph_data(root)

        self.assertTrue(loaded[2]["nodes"])
        self.assertIsInstance(loaded[8], list)
        self.assertGreater(read_model_builder.call_count, 0)
        self.assertFalse(loaded[11]["available"])
        self.assertIn("dashboards/search_index.json", loaded[11]["missing"])

    def test_graph_data_load_falls_back_when_dashboard_json_is_invalid(self) -> None:
        tmp_parent = ROOT_DIR / ".test_tmp" / "ui_dashboard_invalid_json"
        tmp_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp_parent) as temp_dir:
            root = Path(temp_dir) / "research_cockpit"
            copy_demo_research_cockpit_without_dashboards(root)
            build_dashboard(root)
            (root / "dashboards" / "graph_view.json").write_text("{", encoding="utf-8")

            with patch("research_cockpit.ui.app.graph_to_json", wraps=ui_app.graph_to_json) as graph_builder:
                loaded = _load_graph_data(root)

        self.assertTrue(loaded[2]["nodes"])
        self.assertGreater(graph_builder.call_count, 0)
        self.assertTrue(loaded[11]["available"])

    def test_dashboard_staleness_marks_truth_source_newer_than_dashboard(self) -> None:
        tmp_parent = ROOT_DIR / ".test_tmp" / "ui_dashboard_staleness"
        tmp_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp_parent) as temp_dir:
            root = Path(temp_dir) / "research_cockpit"
            copy_demo_research_cockpit_without_dashboards(root)
            build_dashboard(root)

            fresh = dashboard_staleness(root)
            current_state = root / "current_state.yaml"
            future = time.time() + 5
            os.utime(current_state, (future, future))
            stale = dashboard_staleness(root)

        self.assertTrue(fresh["available"])
        self.assertFalse(fresh["stale"])
        self.assertTrue(stale["stale"])
        self.assertIn("research-cockpit build --root", stale["recommended_command"])

    def test_graph_data_load_falls_back_when_dashboard_is_stale(self) -> None:
        tmp_parent = ROOT_DIR / ".test_tmp" / "ui_dashboard_stale_fallback"
        tmp_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp_parent) as temp_dir:
            root = Path(temp_dir) / "research_cockpit"
            copy_demo_research_cockpit_without_dashboards(root)
            build_dashboard(root)
            current_state = root / "current_state.yaml"
            future = time.time() + 5
            os.utime(current_state, (future, future))

            with (
                patch("research_cockpit.ui.app.graph_to_json", wraps=ui_app.graph_to_json) as graph_builder,
                patch(
                    "research_cockpit.ui.app.build_dashboard_read_models",
                    wraps=ui_app.build_dashboard_read_models,
                ) as read_model_builder,
            ):
                loaded = _load_graph_data(root)

        self.assertTrue(loaded[2]["nodes"])
        self.assertIsInstance(loaded[8], list)
        self.assertGreater(graph_builder.call_count, 0)
        self.assertGreater(read_model_builder.call_count, 0)
        self.assertTrue(loaded[11]["available"])
        self.assertTrue(loaded[11]["stale"])

    def test_main_navigation_uses_sidebar_radio_instead_of_top_tabs(self) -> None:
        source = (ROOT_DIR / "src" / "research_cockpit" / "ui" / "app.py").read_text(encoding="utf-8")

        self.assertIn('key="main_page"', source)
        self.assertNotIn("tabs = st.tabs(tab_labels)", source)

    def test_baselines_page_is_in_main_navigation(self) -> None:
        text = get_text("中文")

        self.assertIn("baselines", ordered_tab_keys(text))
        self.assertIn("基线 / Accepted", ordered_tab_labels(text))

    def test_baseline_command_empty_state_only_requires_problem(self) -> None:
        zh_text = get_text("中文")
        en_text = get_text("English")

        self.assertEqual(zh_text["no_baseline_command_targets"], "需要至少一个 problem 才能生成 baseline 命令。")
        self.assertEqual(
            en_text["no_baseline_command_targets"],
            "At least one problem is required to generate a baseline command.",
        )

    def test_baselines_page_generates_commands_without_direct_mutation(self) -> None:
        source = (ROOT_DIR / "src" / "research_cockpit" / "ui" / "app.py").read_text(encoding="utf-8")

        self.assertIn("def render_baselines", source)
        self.assertIn("build_set_baseline_command", source)
        self.assertIn("coord decide", build_set_baseline_command("problem_t5", "option_t5"))
        self.assertNotIn("set-baseline", build_set_baseline_command("problem_t5", "option_t5"))
        self.assertNotIn("from research_cockpit.commands.set_baseline import", source)

    def test_baseline_and_accepted_rows_stay_compact(self) -> None:
        nodes = {
            "stage_t5": SimpleNamespace(
                id="stage_t5",
                type="stage",
                title="Stage",
                status="active",
                parent=None,
                children=["problem_t5"],
                priority=None,
                summary="",
                raw={},
            ),
            "problem_t5": SimpleNamespace(
                id="problem_t5",
                type="problem",
                title="Problem",
                status="active",
                parent="stage_t5",
                children=["option_t5", "option_old"],
                priority=None,
                summary="",
                raw={
                    "baseline": {
                        "option": "option_t5",
                        "decision": "decision_t5",
                        "reason": "Default branch.",
                    }
                },
            ),
            "option_t5": SimpleNamespace(
                id="option_t5",
                type="option",
                title="T5",
                status="accepted",
                parent="problem_t5",
                children=["exp_t5", "decision_t5"],
                priority=None,
                summary="Accepted branch.",
                raw={"decision_state": "accepted", "linked_artifacts": ["artifact_t5"]},
            ),
            "option_old": SimpleNamespace(
                id="option_old",
                type="option",
                title="Old",
                status="rejected",
                parent="problem_t5",
                children=[],
                priority=None,
                summary="Rejected branch.",
                raw={},
            ),
            "exp_t5": SimpleNamespace(
                id="exp_t5",
                type="experiment",
                title="Experiment",
                status="done",
                parent="option_t5",
                children=[],
                priority=None,
                summary="",
                raw={"findings": [{"statement": "Positive.", "outcome": "positive"}]},
            ),
            "decision_t5": SimpleNamespace(
                id="decision_t5",
                type="decision",
                title="Accept T5",
                status="accepted",
                parent="option_t5",
                children=[],
                priority=None,
                summary="Accepted.",
                raw={"supporting_experiments": ["exp_t5"], "evidence_strength": "medium"},
            ),
            "decision_draft": SimpleNamespace(
                id="decision_draft",
                type="decision",
                title="Draft",
                status="proposed",
                parent="option_t5",
                children=[],
                priority=None,
                summary="Draft.",
                raw={},
            ),
            "artifact_t5": SimpleNamespace(
                id="artifact_t5",
                type="artifact",
                title="Bundle",
                status="done",
                parent=None,
                children=[],
                priority=None,
                summary="",
                raw={},
            ),
        }
        current = {"current_problem": "problem_t5", "current_option": "option_t5"}

        baseline_rows = build_baseline_overview_rows(nodes, current)
        accepted_options = build_accepted_option_rows(nodes, current)
        accepted_decisions = build_accepted_decision_rows(nodes)

        self.assertEqual(baseline_rows[0]["baseline_option_id"], "option_t5")
        self.assertEqual([row["id"] for row in accepted_options], ["option_t5"])
        self.assertEqual(accepted_options[0]["finding_count"], 1)
        self.assertEqual([row["id"] for row in accepted_decisions], ["decision_t5"])
        self.assertEqual(accepted_decisions[0]["supporting_experiment_count"], 1)

    def test_baseline_command_targets_include_problems_without_accepted_options(self) -> None:
        nodes = {
            "stage_t5": SimpleNamespace(
                id="stage_t5",
                type="stage",
                title="Stage",
                status="active",
                parent=None,
                children=["problem_t5"],
                priority=None,
                summary="",
                raw={},
            ),
            "problem_t5": SimpleNamespace(
                id="problem_t5",
                type="problem",
                title="Problem",
                status="active",
                parent="stage_t5",
                children=["option_t5"],
                priority=None,
                summary="",
                raw={"baseline": {"option": "option_t5", "reason": "Keep this baseline."}},
            ),
            "option_t5": SimpleNamespace(
                id="option_t5",
                type="option",
                title="T5",
                status="active",
                parent="problem_t5",
                children=[],
                priority=None,
                summary="Active baseline.",
                raw={},
            ),
        }

        baseline_rows = build_baseline_overview_rows(nodes, {})
        accepted_options = build_accepted_option_rows(nodes, {})
        source = (ROOT_DIR / "src" / "research_cockpit" / "ui" / "app.py").read_text(encoding="utf-8")

        self.assertEqual(accepted_options, [])
        self.assertEqual(baseline_command_problem_ids(baseline_rows), ["problem_t5"])
        self.assertIn("baseline_command_problem_ids(baseline_rows)", source)

    def test_graph_baseline_metadata_marks_effective_baseline_without_global_current_fallback(self) -> None:
        nodes = {
            "stage_t5": SimpleNamespace(
                id="stage_t5",
                type="stage",
                title="Stage",
                status="active",
                parent=None,
                children=["problem_t5", "problem_other"],
                priority=None,
                summary="",
                raw={},
            ),
            "problem_t5": SimpleNamespace(
                id="problem_t5",
                type="problem",
                title="Problem",
                status="active",
                parent="stage_t5",
                children=["option_t5"],
                priority=None,
                summary="",
                raw={"baseline": {"option": "option_t5", "reason": "Default branch."}},
            ),
            "option_t5": SimpleNamespace(
                id="option_t5",
                type="option",
                title="T5",
                status="accepted",
                parent="problem_t5",
                children=["exp_t5"],
                priority=None,
                summary="",
                raw={},
            ),
            "exp_t5": SimpleNamespace(
                id="exp_t5",
                type="experiment",
                title="Experiment",
                status="done",
                parent="option_t5",
                children=[],
                priority=None,
                summary="",
                raw={},
            ),
            "problem_other": SimpleNamespace(
                id="problem_other",
                type="problem",
                title="Other Problem",
                status="active",
                parent="stage_t5",
                children=["option_current"],
                priority=None,
                summary="",
                raw={},
            ),
            "option_current": SimpleNamespace(
                id="option_current",
                type="option",
                title="Global Current",
                status="active",
                parent="problem_other",
                children=[],
                priority=None,
                summary="",
                raw={},
            ),
        }
        current = {
            "current_focus_node": "exp_t5",
            "current_focus_path": ["stage_t5", "problem_t5", "option_t5", "exp_t5"],
            "current_option": "option_current",
        }

        metadata = build_graph_baseline_metadata(nodes, current)
        graph = graph_to_json(nodes, current_focus_path=current["current_focus_path"], current=current)
        graph_nodes = {node["id"]: node for node in graph["nodes"]}
        overview = build_node_overview(nodes["exp_t5"], nodes, current, [], [])

        self.assertEqual(metadata["exp_t5"]["effective_baseline_option_id"], "option_t5")
        self.assertEqual(metadata["exp_t5"]["baseline_source_id"], "problem_t5")
        self.assertEqual(metadata["problem_other"]["effective_baseline_option_id"], "")
        self.assertTrue(metadata["problem_t5"]["is_baseline_source"])
        self.assertTrue(metadata["option_t5"]["is_effective_baseline_option"])
        self.assertTrue(metadata["option_t5"]["is_current_effective_baseline_option"])
        self.assertFalse(metadata["option_current"]["is_effective_baseline_option"])
        self.assertEqual(graph_nodes["exp_t5"]["effective_baseline_option_id"], "option_t5")
        self.assertEqual(overview["effective_baseline"]["option"]["id"], "option_t5")

    def test_graph_renders_before_control_panel(self) -> None:
        source = (ROOT_DIR / "src" / "research_cockpit" / "ui" / "app.py").read_text(encoding="utf-8")
        graph_index = source.find('key="research_graph_component"')
        controls_index = source.find("Graph Controls")

        self.assertNotEqual(graph_index, -1)
        self.assertNotEqual(controls_index, -1)
        self.assertLess(graph_index, controls_index)

    def test_graph_controls_stay_inside_graph_column(self) -> None:
        source = (ROOT_DIR / "src" / "research_cockpit" / "ui" / "app.py").read_text(encoding="utf-8")
        graph_area_index = source.find("with graph_area:")
        controls_index = source.find("Graph Controls", graph_area_index)
        detail_index = source.find("with detail:", graph_area_index)

        self.assertNotEqual(graph_area_index, -1)
        self.assertNotEqual(controls_index, -1)
        self.assertNotEqual(detail_index, -1)
        self.assertLess(graph_area_index, controls_index)
        self.assertLess(controls_index, detail_index)

    def test_baseline_lens_control_drives_payload_and_saved_views(self) -> None:
        source = (ROOT_DIR / "src" / "research_cockpit" / "ui" / "app.py").read_text(encoding="utf-8")

        self.assertIn('key="graph_show_baseline_lens"', source)
        self.assertIn("default_show_baseline_lens(view_mode)", source)
        self.assertIn("show_baseline_lens=show_baseline_lens", source)
        self.assertIn('"show_baseline_lens": show_baseline_lens', source)
        self.assertIn('key="graph_hide_inactive_option_branches"', source)
        self.assertIn("hide_inactive_option_branches=hide_inactive_option_branches", source)
        self.assertIn('"hide_inactive_option_branches": hide_inactive_option_branches', source)

    def test_set_focus_resets_stale_graph_filters(self) -> None:
        source = (ROOT_DIR / "src" / "research_cockpit" / "ui" / "app.py").read_text(encoding="utf-8")
        set_focus_index = source.find("save_current_focus(RESEARCH_ROOT, focus_node=node_id)")
        rerun_index = source.find("st.rerun()", set_focus_index)

        self.assertNotEqual(set_focus_index, -1)
        self.assertNotEqual(rerun_index, -1)
        focus_block = source[set_focus_index:rerun_index]
        self.assertIn('st.session_state["graph_view_mode"] = text["focus_depth_2"]', focus_block)
        self.assertIn('st.session_state["graph_detail_node"] = node_id', focus_block)
        self.assertIn('st.session_state["graph_pending_detail_select"] = node_id', focus_block)
        self.assertIn('st.session_state["graph_pending_node_search"] = ""', focus_block)
        self.assertNotIn('st.session_state["graph_detail_select"] = node_id', focus_block)
        self.assertNotIn('st.session_state["graph_node_search"] = ""', focus_block)
        self.assertIn('st.session_state["graph_workstreams"] = []', focus_block)
        self.assertIn('st.session_state.pop("graph_focus_roles", None)', focus_block)

    def test_pending_detail_state_is_applied_before_widgets(self) -> None:
        source = (ROOT_DIR / "src" / "research_cockpit" / "ui" / "app.py").read_text(encoding="utf-8")
        pending_index = source.find('st.session_state.pop("graph_pending_detail_select", None)')
        search_widget_index = source.find('key="graph_node_search"')
        select_key_index = source.find('select_key = "graph_detail_select"')

        self.assertNotEqual(pending_index, -1)
        self.assertNotEqual(search_widget_index, -1)
        self.assertNotEqual(select_key_index, -1)
        self.assertLess(select_key_index, pending_index)
        self.assertLess(pending_index, search_widget_index)

    def test_graph_view_defaults_to_global_mode(self) -> None:
        source = (ROOT_DIR / "src" / "research_cockpit" / "ui" / "app.py").read_text(encoding="utf-8")

        self.assertIn('DEFAULT_GRAPH_VIEW_MODE', source)
        self.assertIn('default_view_label = mode_value_to_label.get(DEFAULT_GRAPH_VIEW_MODE, text["global_graph"])', source)
        self.assertIn('st.session_state.get("graph_view_mode", default_view_label)', source)
        self.assertIn('mode_label_to_value.get(view_label, DEFAULT_GRAPH_VIEW_MODE)', source)

    def test_node_detail_prioritizes_overview_before_raw_metadata(self) -> None:
        source = (ROOT_DIR / "src" / "research_cockpit" / "ui" / "app.py").read_text(encoding="utf-8")
        detail_start = source.find("def render_node_detail(")
        baseline_helper_start = source.find("def render_effective_baseline(")
        evidence_start = source.find("with evidence_tab:", detail_start)
        overview_block = source[detail_start:evidence_start]
        baseline_helper = source[baseline_helper_start:detail_start]

        self.assertIn('text["overview_tab"]', overview_block)
        self.assertIn('text["node_purpose"]', overview_block)
        self.assertIn('text["current_state"]', overview_block)
        self.assertIn("render_effective_baseline", overview_block)
        self.assertIn('text["effective_baseline"]', baseline_helper)
        self.assertIn('text["key_resources"]', overview_block)
        self.assertNotIn("c1.code(node.type)", overview_block)
        self.assertNotIn("c2.code(node.status)", overview_block)

    def test_global_graph_transition_resets_stale_scope_filters(self) -> None:
        state = {
            "graph_previous_view_mode": "focus_depth_2",
            "graph_node_types": ["stage", "problem", "option", "artifact"],
            "graph_statuses": ["active"],
            "graph_stages": ["stage_demo"],
            "graph_focus_roles": ["parent", "current", "unrelated"],
            "graph_workstreams": ["option_demo"],
            "graph_only_blocking": True,
            "graph_only_next_actions": True,
            "graph_only_missing_evidence": True,
        }

        changed = reset_global_graph_filter_state(
            state,
            "global",
            all_types=["artifact", "decision", "experiment", "option", "problem", "stage"],
            all_statuses=["active", "cancelled", "done", "open", "parked", "planned", "proposed", "rejected"],
            all_stages=["stage_demo"],
            all_focus_roles=["child", "current", "parent", "sibling", "unrelated"],
        )

        self.assertTrue(changed)
        self.assertEqual(state["graph_previous_view_mode"], "global")
        self.assertEqual(state["graph_node_types"], ["decision", "experiment", "option", "problem", "stage"])
        self.assertEqual(state["graph_statuses"], ["active", "open", "planned", "proposed"])
        self.assertEqual(state["graph_focus_roles"], ["child", "current", "parent", "sibling", "unrelated"])
        self.assertEqual(state["graph_workstreams"], [])
        self.assertFalse(state["graph_only_blocking"])
        self.assertFalse(state["graph_only_next_actions"])
        self.assertFalse(state["graph_only_missing_evidence"])

    def test_global_graph_preserves_manual_filters_after_transition(self) -> None:
        state = {
            "graph_previous_view_mode": "global",
            "graph_statuses": ["active"],
            "graph_focus_roles": ["current"],
        }

        changed = reset_global_graph_filter_state(
            state,
            "global",
            all_types=["option"],
            all_statuses=["active", "planned"],
            all_stages=["stage_demo"],
            all_focus_roles=["current", "child"],
        )

        self.assertFalse(changed)
        self.assertEqual(state["graph_statuses"], ["active"])
        self.assertEqual(state["graph_focus_roles"], ["current"])

    def test_leaving_option_workstream_clears_stale_workstream_filter(self) -> None:
        state = {
            "graph_previous_view_mode": "option_workstream",
            "graph_workstreams": ["option_t5"],
        }

        changed = reset_global_graph_filter_state(
            state,
            "focus_depth_1",
            all_types=["experiment", "option", "problem", "stage"],
            all_statuses=["active", "open", "planned"],
            all_stages=["stage_demo"],
            all_focus_roles=["child", "current", "parent"],
        )

        self.assertTrue(changed)
        self.assertEqual(state["graph_previous_view_mode"], "focus_depth_1")
        self.assertEqual(state["graph_workstreams"], [])

    def test_saved_global_view_can_keep_saved_filters(self) -> None:
        state = {
            "graph_previous_view_mode": "focus_depth_2",
            "graph_skip_global_filter_reset": True,
            "graph_statuses": ["active"],
            "graph_focus_roles": ["current"],
        }

        changed = reset_global_graph_filter_state(
            state,
            "global",
            all_types=["option"],
            all_statuses=["active", "planned"],
            all_stages=["stage_demo"],
            all_focus_roles=["current", "child"],
        )

        self.assertFalse(changed)
        self.assertEqual(state["graph_previous_view_mode"], "global")
        self.assertNotIn("graph_skip_global_filter_reset", state)
        self.assertEqual(state["graph_statuses"], ["active"])
        self.assertEqual(state["graph_focus_roles"], ["current"])

    def test_react_flow_component_uses_dagre_dragging_and_smooth_edges(self) -> None:
        source = (ROOT_DIR / "src" / "research_cockpit" / "ui" / "graph_component" / "frontend" / "src" / "GraphComponent.tsx").read_text(
            encoding="utf-8"
        )

        self.assertIn('import dagre from "dagre"', source)
        self.assertIn('rankdir: "LR"', source)
        self.assertIn("nodesDraggable", source)
        self.assertIn('type: "smoothstep"', source)
        self.assertIn("key={layoutKey}", source)
        self.assertIn("layoutSignature", source)
        self.assertIn("renderSignature", source)
        self.assertIn("event_id", source)
        self.assertIn("visualSelectedNodeId", source)
        self.assertIn("setVisualSelectedNodeId(node.id)", source)
        self.assertIn("toReactFlowNodes(payload)", source)
        self.assertIn("applyNodeSelection", source)
        self.assertIn("previousVisualSelectedNodeId", source)
        self.assertIn("pendingSelectedNodeId", source)
        self.assertIn("pendingSelectedNodeId.current = node.id", source)
        self.assertIn("selectedNodeId === pending", source)
        self.assertIn("badges?: string[]", source)
        self.assertIn("graph-node-badges", source)
        self.assertIn("CURRENT BASELINE", source)

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

    def test_graph_filter_supports_branch_workstream_and_evidence_facets(self) -> None:
        graph = {
            "nodes": [
                {
                    "id": "stage_text",
                    "type": "stage",
                    "status": "active",
                    "focus_role": "parent",
                    "stage_id": "stage_text",
                    "in_current_branch": True,
                    "is_focus_visible": True,
                    "focus_visible_depth": 1,
                    "has_blockers": False,
                    "has_evidence": False,
                    "option_workstream_id": None,
                },
                {
                    "id": "problem_text",
                    "type": "problem",
                    "status": "active",
                    "focus_role": "current",
                    "stage_id": "stage_text",
                    "in_current_branch": True,
                    "is_focus_visible": True,
                    "focus_visible_depth": 0,
                    "has_blockers": True,
                    "has_evidence": False,
                    "option_workstream_id": None,
                },
                {
                    "id": "option_t5",
                    "type": "option",
                    "status": "active",
                    "focus_role": "child",
                    "stage_id": "stage_text",
                    "in_current_branch": True,
                    "is_focus_visible": True,
                    "focus_visible_depth": 1,
                    "has_blockers": False,
                    "has_evidence": False,
                    "option_workstream_id": "option_t5",
                    "option_workstream_upstream_problem_id": "problem_text",
                },
                {
                    "id": "exp_t5",
                    "type": "experiment",
                    "status": "planned",
                    "focus_role": "child",
                    "stage_id": "stage_text",
                    "in_current_branch": True,
                    "is_focus_visible": True,
                    "focus_visible_depth": 2,
                    "has_blockers": False,
                    "has_evidence": True,
                    "option_workstream_id": "option_t5",
                    "option_workstream_upstream_problem_id": "problem_text",
                },
                {
                    "id": "option_other",
                    "type": "option",
                    "status": "open",
                    "focus_role": "unrelated",
                    "stage_id": "stage_other",
                    "in_current_branch": False,
                    "is_focus_visible": False,
                    "focus_visible_depth": None,
                    "has_blockers": False,
                    "has_evidence": False,
                    "option_workstream_id": "option_other",
                    "option_workstream_upstream_problem_id": "problem_other",
                },
            ],
            "edges": [
                {"from": "problem_text", "to": "option_t5", "relation": "child"},
                {"from": "option_t5", "to": "exp_t5", "relation": "child"},
                {"from": "problem_other", "to": "option_other", "relation": "child"},
            ],
        }

        branch = filter_graph_for_view(
            graph,
            "current_branch",
            {"stage", "problem", "option", "experiment"},
            {"active", "open", "planned"},
            selected_focus_roles={"current", "child"},
            only_missing_evidence=True,
        )
        workstream = filter_graph_for_view(
            graph,
            "option_workstream",
            {"stage", "problem", "option", "experiment"},
            {"active", "open", "planned"},
            selected_workstreams={"option_t5"},
        )
        empty_workstream = filter_graph_for_view(
            graph,
            "option_workstream",
            {"stage", "problem", "option", "experiment"},
            {"active", "open", "planned"},
            selected_workstreams=set(),
        )
        options = graph_filter_options(graph)

        self.assertEqual({node["id"] for node in branch["nodes"]}, {"problem_text", "option_t5"})
        self.assertEqual({node["id"] for node in workstream["nodes"]}, {"problem_text", "option_t5", "exp_t5"})
        self.assertEqual(empty_workstream["nodes"], [])
        self.assertEqual(empty_workstream["edges"], [])
        self.assertEqual(options["stages"], ["stage_other", "stage_text"])
        self.assertIn("option_t5", options["workstreams"])

    def test_graph_filter_collapses_branch_descendants(self) -> None:
        graph = {
            "nodes": [
                {
                    "id": "problem_text",
                    "type": "problem",
                    "status": "active",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 0,
                },
                {
                    "id": "option_t5",
                    "type": "option",
                    "status": "active",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 1,
                },
                {
                    "id": "exp_t5",
                    "type": "experiment",
                    "status": "running",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 2,
                },
                {
                    "id": "artifact_t5",
                    "type": "artifact",
                    "status": "done",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 3,
                },
                {
                    "id": "option_other",
                    "type": "option",
                    "status": "active",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 1,
                },
            ],
            "edges": [
                {"from": "problem_text", "to": "option_t5", "relation": "child"},
                {"from": "option_t5", "to": "exp_t5", "relation": "child"},
                {"from": "exp_t5", "to": "artifact_t5", "relation": "child"},
                {"from": "problem_text", "to": "option_other", "relation": "child"},
            ],
        }

        option_collapsed = filter_graph_for_view(
            graph,
            "global",
            {"problem", "option", "experiment", "artifact"},
            {"active", "running", "done"},
            collapsed_branch_roots={"option_t5"},
        )
        problem_collapsed = filter_graph_for_view(
            graph,
            "global",
            {"problem", "option", "experiment", "artifact"},
            {"active", "running", "done"},
            collapsed_branch_roots={"problem_text"},
        )

        self.assertEqual(
            {node["id"] for node in option_collapsed["nodes"]},
            {"problem_text", "option_t5", "option_other"},
        )
        self.assertEqual(
            option_collapsed["edges"],
            [
                {"from": "problem_text", "to": "option_t5", "relation": "child"},
                {"from": "problem_text", "to": "option_other", "relation": "child"},
            ],
        )
        self.assertEqual({node["id"] for node in problem_collapsed["nodes"]}, {"problem_text"})
        self.assertEqual(problem_collapsed["edges"], [])

    def test_graph_filter_collapsed_branch_preserves_root_when_structural_edges_cycle(self) -> None:
        graph = {
            "nodes": [
                {
                    "id": "problem_text",
                    "type": "problem",
                    "status": "active",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 0,
                },
                {
                    "id": "option_t5",
                    "type": "option",
                    "status": "active",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 1,
                },
            ],
            "edges": [
                {"from": "problem_text", "to": "option_t5", "relation": "child"},
                {"from": "option_t5", "to": "problem_text", "relation": "child"},
            ],
        }

        filtered = filter_graph_for_view(
            graph,
            "global",
            {"problem", "option"},
            {"active"},
            collapsed_branch_roots={"problem_text"},
        )

        self.assertEqual({node["id"] for node in filtered["nodes"]}, {"problem_text"})
        self.assertEqual(filtered["edges"], [])

    def test_graph_filter_parent_collapse_hides_nested_collapsed_roots(self) -> None:
        graph = {
            "nodes": [
                {
                    "id": "problem_text",
                    "type": "problem",
                    "status": "active",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 0,
                },
                {
                    "id": "option_t5",
                    "type": "option",
                    "status": "active",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 1,
                },
                {
                    "id": "exp_t5",
                    "type": "experiment",
                    "status": "running",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 2,
                },
            ],
            "edges": [
                {"from": "problem_text", "to": "option_t5", "relation": "child"},
                {"from": "option_t5", "to": "exp_t5", "relation": "child"},
            ],
        }

        filtered = filter_graph_for_view(
            graph,
            "global",
            {"problem", "option", "experiment"},
            {"active", "running"},
            collapsed_branch_roots={"problem_text", "option_t5"},
        )

        self.assertEqual({node["id"] for node in filtered["nodes"]}, {"problem_text"})
        self.assertEqual(filtered["edges"], [])

    def test_graph_status_filter_prunes_descendants_of_hidden_structural_parent(self) -> None:
        graph = {
            "nodes": [
                {
                    "id": "problem_text",
                    "type": "problem",
                    "status": "active",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 0,
                },
                {
                    "id": "option_parked",
                    "type": "option",
                    "status": "parked",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 1,
                },
                {
                    "id": "exp_child",
                    "type": "experiment",
                    "status": "running",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 2,
                },
                {
                    "id": "option_active",
                    "type": "option",
                    "status": "active",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 1,
                },
            ],
            "edges": [
                {"from": "problem_text", "to": "option_parked", "relation": "child"},
                {"from": "option_parked", "to": "exp_child", "relation": "child"},
                {"from": "problem_text", "to": "option_active", "relation": "child"},
            ],
        }

        filtered = filter_graph_for_view(
            graph,
            "global",
            {"problem", "option", "experiment"},
            {"active", "running"},
        )

        self.assertEqual({node["id"] for node in filtered["nodes"]}, {"problem_text", "option_active"})
        self.assertEqual(filtered["edges"], [{"from": "problem_text", "to": "option_active", "relation": "child"}])

    def test_graph_filter_can_hide_inactive_option_branches_without_hiding_open_problems(self) -> None:
        graph = {
            "nodes": [
                {
                    "id": "problem_open",
                    "type": "problem",
                    "status": "open",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 0,
                },
                {
                    "id": "option_open",
                    "type": "option",
                    "status": "open",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 1,
                },
                {
                    "id": "exp_open_child",
                    "type": "experiment",
                    "status": "planned",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 2,
                },
                {
                    "id": "option_active",
                    "type": "option",
                    "status": "active",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 1,
                },
                {
                    "id": "exp_active_child",
                    "type": "experiment",
                    "status": "running",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 2,
                },
            ],
            "edges": [
                {"from": "problem_open", "to": "option_open", "relation": "child"},
                {"from": "option_open", "to": "exp_open_child", "relation": "child"},
                {"from": "problem_open", "to": "option_active", "relation": "child"},
                {"from": "option_active", "to": "exp_active_child", "relation": "child"},
            ],
        }

        compact = filter_graph_for_view(
            graph,
            "global",
            {"problem", "option", "experiment"},
            {"active", "open", "planned", "running"},
            hide_inactive_option_branches=True,
        )
        expanded = filter_graph_for_view(
            graph,
            "global",
            {"problem", "option", "experiment"},
            {"active", "open", "planned", "running"},
            hide_inactive_option_branches=False,
        )

        self.assertEqual(
            {node["id"] for node in compact["nodes"]},
            {"problem_open", "option_active", "exp_active_child"},
        )
        self.assertEqual(
            compact["edges"],
            [
                {"from": "problem_open", "to": "option_active", "relation": "child"},
                {"from": "option_active", "to": "exp_active_child", "relation": "child"},
            ],
        )
        self.assertEqual(
            {node["id"] for node in expanded["nodes"]},
            {"problem_open", "option_open", "exp_open_child", "option_active", "exp_active_child"},
        )

    def test_collapsed_descendant_walk_does_not_rescan_nested_roots(self) -> None:
        class CountingChildren(dict):
            def __init__(self, values: dict[str, list[str]]) -> None:
                super().__init__(values)
                self.get_calls: list[str] = []

            def get(self, key: str, default: object = None) -> object:
                self.get_calls.append(key)
                return super().get(key, default)

        node_ids = [f"node_{index}" for index in range(25)]
        child_ids_by_parent = CountingChildren({
            node_id: [node_ids[index + 1]] if index + 1 < len(node_ids) else []
            for index, node_id in enumerate(node_ids)
        })

        hidden = _collapsed_descendant_ids(
            child_ids_by_parent,
            set(node_ids),
            set(node_ids),
        )

        self.assertEqual(hidden, set(node_ids[1:]))
        self.assertLessEqual(len(child_ids_by_parent.get_calls), len(node_ids) * 2)

    def test_graph_filter_reveals_direct_hidden_children_without_bypassing_semantic_filters(self) -> None:
        graph = {
            "nodes": [
                {
                    "id": "problem_text",
                    "type": "problem",
                    "status": "active",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 0,
                    "option_workstream_id": None,
                },
                {
                    "id": "option_t5",
                    "type": "option",
                    "status": "active",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 1,
                    "option_workstream_id": "option_t5",
                    "option_workstream_upstream_problem_id": "problem_text",
                },
                {
                    "id": "exp_done",
                    "type": "experiment",
                    "status": "done",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 2,
                    "option_workstream_id": "option_t5",
                    "option_workstream_upstream_problem_id": "problem_text",
                    "has_evidence": True,
                },
                {
                    "id": "exp_other_workstream",
                    "type": "experiment",
                    "status": "done",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 2,
                    "option_workstream_id": "option_other",
                    "option_workstream_upstream_problem_id": "problem_text",
                    "has_evidence": True,
                },
                {
                    "id": "artifact_done",
                    "type": "artifact",
                    "status": "done",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 2,
                    "option_workstream_id": "option_t5",
                    "option_workstream_upstream_problem_id": "problem_text",
                    "has_evidence": True,
                },
            ],
            "edges": [
                {"from": "problem_text", "to": "option_t5", "relation": "child"},
                {"from": "option_t5", "to": "exp_done", "relation": "child"},
                {"from": "option_t5", "to": "exp_other_workstream", "relation": "child"},
                {"from": "option_t5", "to": "artifact_done", "relation": "child"},
            ],
        }

        filtered = filter_graph_for_view(
            graph,
            "option_workstream",
            {"problem", "option", "experiment"},
            {"active"},
            selected_workstreams={"option_t5"},
            revealed_child_roots={"option_t5"},
        )
        collapsed = filter_graph_for_view(
            graph,
            "option_workstream",
            {"problem", "option", "experiment"},
            {"active"},
            selected_workstreams={"option_t5"},
            collapsed_branch_roots={"option_t5"},
            revealed_child_roots={"option_t5"},
        )
        revealable = revealable_child_ids_for_view(
            graph,
            "option_t5",
            "option_workstream",
            {"problem", "option", "experiment"},
            {"active"},
            selected_workstreams={"option_t5"},
        )

        self.assertEqual(
            {node["id"] for node in filtered["nodes"]},
            {"problem_text", "option_t5", "exp_done"},
        )
        self.assertEqual({node["id"] for node in collapsed["nodes"]}, {"problem_text", "option_t5"})
        self.assertEqual(revealable, ["exp_done"])

    def test_graph_filter_visibility_context_can_be_reused_for_reveal_counts(self) -> None:
        graph = {
            "nodes": [
                {
                    "id": "problem_text",
                    "type": "problem",
                    "status": "active",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 0,
                },
                {
                    "id": "option_t5",
                    "type": "option",
                    "status": "active",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 1,
                },
                {
                    "id": "exp_done",
                    "type": "experiment",
                    "status": "done",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 2,
                },
            ],
            "edges": [
                {"from": "problem_text", "to": "option_t5", "relation": "child"},
                {"from": "option_t5", "to": "exp_done", "relation": "child"},
            ],
        }

        filtered, visibility = filter_graph_for_view_with_visibility(
            graph,
            "global",
            {"problem", "option", "experiment"},
            {"active"},
        )
        revealable = revealable_child_ids_for_view(
            graph,
            "option_t5",
            "global",
            {"problem", "option", "experiment"},
            {"active"},
            included_node_ids=visibility["included_node_ids"],
            hidden_by_collapse=visibility["hidden_by_collapse"],
            child_ids_by_parent=visibility["child_ids_by_parent"],
        )

        self.assertEqual({node["id"] for node in filtered["nodes"]}, {"problem_text", "option_t5"})
        self.assertEqual(visibility["included_node_ids"], {"problem_text", "option_t5"})
        self.assertEqual(revealable, ["exp_done"])

    def test_saved_graph_view_state_filters_missing_options(self) -> None:
        state = graph_view_state_from_saved_view(
            {
                "scope": "current_branch",
                "filters": {
                    "node_types": ["problem", "artifact", "missing_type"],
                    "statuses": ["active", "archived"],
                    "stages": ["stage_text", "stage_old"],
                    "focus_roles": ["current", "unrelated"],
                    "workstreams": ["option_t5", "option_old"],
                    "collapsed_branch_roots": ["option_t5", "missing"],
                    "revealed_child_roots": ["option_old", "missing"],
                    "only_blocking": "true",
                    "only_next_actions": False,
                    "only_missing_evidence": True,
                    "hide_inactive_option_branches": False,
                },
            },
            {
                "types": ["problem", "artifact", "option"],
                "statuses": ["active"],
                "stages": ["stage_text"],
                "focus_roles": ["current", "child"],
                "workstreams": ["option_t5"],
            },
            {
                "focus_depth_2": "Focus Depth 2",
                "current_branch": "Current Branch",
            },
            ["problem_text", "option_t5", "option_old"],
        )

        self.assertEqual(state["graph_view_mode"], "Current Branch")
        self.assertEqual(state["graph_node_types"], ["problem", "artifact"])
        self.assertEqual(state["graph_statuses"], ["active"])
        self.assertEqual(state["graph_stages"], ["stage_text"])
        self.assertEqual(state["graph_focus_roles"], ["current"])
        self.assertEqual(state["graph_workstreams"], ["option_t5"])
        self.assertEqual(state["graph_collapsed_branch_roots"], ["option_t5"])
        self.assertEqual(state["graph_revealed_child_roots"], ["option_old"])
        self.assertTrue(state["graph_only_blocking"])
        self.assertFalse(state["graph_only_next_actions"])
        self.assertTrue(state["graph_only_missing_evidence"])
        self.assertTrue(state["graph_show_baseline_lens"])
        self.assertFalse(state["graph_hide_inactive_option_branches"])

        global_state = graph_view_state_from_saved_view(
            {"scope": "global", "filters": {}},
            {"types": [], "statuses": [], "stages": [], "focus_roles": [], "workstreams": []},
            {"focus_depth_2": "Focus Depth 2", "global": "Global"},
        )
        explicit_state = graph_view_state_from_saved_view(
            {"scope": "global", "filters": {"show_baseline_lens": True}},
            {"types": [], "statuses": [], "stages": [], "focus_roles": [], "workstreams": []},
            {"focus_depth_2": "Focus Depth 2", "global": "Global"},
        )
        self.assertTrue(global_state["graph_show_baseline_lens"])
        self.assertTrue(explicit_state["graph_show_baseline_lens"])
        self.assertTrue(global_state["graph_hide_inactive_option_branches"])

        no_scope_state = graph_view_state_from_saved_view(
            {"filters": {}},
            {"types": [], "statuses": [], "stages": [], "focus_roles": [], "workstreams": []},
            {"focus_depth_2": "Focus Depth 2", "global": "Global"},
        )
        self.assertEqual(no_scope_state["graph_view_mode"], "Global")
        self.assertTrue(no_scope_state["graph_show_baseline_lens"])
        self.assertTrue(no_scope_state["graph_hide_inactive_option_branches"])

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

    def test_graph_component_payload_omits_raw_node_data(self) -> None:
        graph = {
            "nodes": [
                {
                    "id": "problem_text",
                    "label": "Weak text",
                    "title": "Focus problem",
                    "type": "problem",
                    "status": "active",
                    "priority": "high",
                    "color": "#FFE9A8",
                    "shape": "diamond",
                    "is_current_focus": True,
                    "focus_visible_depth": 0,
                    "raw": {"private": "do not send to component"},
                },
                {
                    "id": "option_t5",
                    "label": "T5",
                    "title": "Candidate option",
                    "type": "option",
                    "status": "open",
                    "color": "#FFFFFF",
                    "shape": "box",
                    "focus_visible_depth": 1,
                    "raw": {"private": "do not send to component"},
                },
            ],
            "edges": [{"from": "problem_text", "to": "option_t5", "relation": "contains"}],
            "current_focus_node": "problem_text",
        }

        payload = build_graph_component_payload(graph, selected_node_id="option_t5")

        self.assertEqual(payload["selected_node_id"], "option_t5")
        self.assertEqual([node["id"] for node in payload["nodes"]], ["problem_text", "option_t5"])
        self.assertNotIn("raw", payload["nodes"][0])
        self.assertNotIn("position", payload["nodes"][0])
        self.assertEqual(
            set(payload["nodes"][0]),
            {"id", "label", "title", "type", "status", "priority", "color", "is_current_focus", "is_focus"},
        )
        self.assertEqual(payload["edges"][0]["source"], "problem_text")
        self.assertEqual(payload["edges"][0]["target"], "option_t5")
        self.assertIn("color", payload["edges"][0])

    def test_graph_component_base_payload_reuses_nodes_and_edges_for_selection(self) -> None:
        graph = {
            "nodes": [
                {
                    "id": "problem_text",
                    "label": "Weak text",
                    "title": "Focus problem",
                    "type": "problem",
                    "status": "active",
                    "color": "#FFE9A8",
                },
                {
                    "id": "option_t5",
                    "label": "T5",
                    "title": "Candidate option",
                    "type": "option",
                    "status": "open",
                    "color": "#FFFFFF",
                    "effective_baseline_option_id": "problem_text",
                },
            ],
            "edges": [{"from": "problem_text", "to": "option_t5", "relation": "contains"}],
        }

        base_payload = build_graph_component_base_payload(graph, show_baseline_lens=True)
        selected_problem = build_graph_component_payload_from_base(base_payload, "problem_text")
        selected_option = build_graph_component_payload_from_base(base_payload, "option_t5")

        self.assertEqual(selected_problem["selected_node_id"], "problem_text")
        self.assertEqual(selected_option["selected_node_id"], "option_t5")
        self.assertIs(selected_problem["nodes"], base_payload["nodes"])
        self.assertIs(selected_problem["edges"], base_payload["edges"])
        self.assertIs(selected_option["nodes"], base_payload["nodes"])
        self.assertIs(selected_option["edges"], base_payload["edges"])
        self.assertEqual(selected_problem["nodes"], selected_option["nodes"])
        self.assertEqual(selected_problem["edges"], selected_option["edges"])

    def test_graph_cache_keys_ignore_selection_but_track_filters_and_baseline_lens(self) -> None:
        graph = {
            "nodes": [
                {
                    "id": "problem_text",
                    "label": "Weak text",
                    "title": "Focus problem",
                    "type": "problem",
                    "status": "active",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 0,
                },
                {
                    "id": "option_t5",
                    "label": "T5",
                    "title": "Candidate option",
                    "type": "option",
                    "status": "open",
                    "stage_id": "stage_text",
                    "is_focus_visible": True,
                    "focus_visible_depth": 1,
                    "effective_baseline_option_id": "problem_text",
                },
            ],
            "edges": [{"from": "problem_text", "to": "option_t5", "relation": "contains"}],
        }

        base_payload = build_graph_component_base_payload(graph)
        selected_problem = build_graph_component_payload_from_base(base_payload, "problem_text")
        selected_option = build_graph_component_payload_from_base(base_payload, "option_t5")
        default_filter_key = graph_filter_cache_key(
            graph,
            "global",
            {"problem", "option"},
            {"active", "open"},
            selected_stages={"stage_text"},
        )
        collapsed_filter_key = graph_filter_cache_key(
            graph,
            "global",
            {"problem", "option"},
            {"active", "open"},
            selected_stages={"stage_text"},
            collapsed_branch_roots={"problem_text"},
        )

        self.assertEqual(selected_problem["nodes"], selected_option["nodes"])
        self.assertEqual(selected_problem["edges"], selected_option["edges"])
        self.assertNotEqual(default_filter_key, collapsed_filter_key)
        self.assertNotEqual(
            graph_component_payload_cache_key(default_filter_key, show_baseline_lens=False),
            graph_component_payload_cache_key(default_filter_key, show_baseline_lens=True),
        )
        inactive_options_filter_key = graph_filter_cache_key(
            graph,
            "global",
            {"problem", "option"},
            {"active", "open"},
            selected_stages={"stage_text"},
            hide_inactive_option_branches=True,
        )
        self.assertNotEqual(default_filter_key, inactive_options_filter_key)

        changed_graph = {
            **graph,
            "nodes": [
                {**graph["nodes"][0], "status": "done"},
                graph["nodes"][1],
            ],
        }
        self.assertNotEqual(graph_cache_key(graph), graph_cache_key(changed_graph))

    def test_graph_component_payload_adds_baseline_lens_markers_only_when_enabled(self) -> None:
        graph = {
            "nodes": [
                {
                    "id": "problem_text",
                    "label": "Weak text",
                    "title": "Focus problem",
                    "type": "problem",
                    "status": "active",
                    "color": "#FFE9A8",
                    "is_baseline_source": True,
                    "effective_baseline_option_id": "option_t5",
                    "baseline_source_id": "problem_text",
                    "baseline_source_kind": "explicit",
                },
                {
                    "id": "option_t5",
                    "label": "T5",
                    "title": "Baseline option",
                    "type": "option",
                    "status": "accepted",
                    "color": "#CFEAD6",
                    "is_effective_baseline_option": True,
                    "is_current_effective_baseline_option": True,
                },
                {
                    "id": "exp_t5",
                    "label": "Run",
                    "title": "Experiment",
                    "type": "experiment",
                    "status": "done",
                    "color": "#CFEAD6",
                    "effective_baseline_option_id": "option_t5",
                    "baseline_source_id": "problem_text",
                    "baseline_source_kind": "inherited",
                },
            ],
            "edges": [
                {"from": "problem_text", "to": "option_t5", "relation": "contains"},
                {"from": "option_t5", "to": "exp_t5", "relation": "contains"},
            ],
        }

        hidden = build_graph_component_payload(graph, selected_node_id="exp_t5")
        visible = build_graph_component_payload(graph, selected_node_id="exp_t5", show_baseline_lens=True)

        self.assertNotIn("badges", hidden["nodes"][0])
        self.assertFalse(any(edge["type"].startswith("baseline") for edge in hidden["edges"]))
        nodes = {node["id"]: node for node in visible["nodes"]}
        self.assertEqual(nodes["problem_text"]["badges"], ["SOURCE"])
        self.assertEqual(nodes["option_t5"]["badges"], ["CURRENT BASELINE"])
        self.assertEqual(nodes["exp_t5"]["effective_baseline_option_id"], "option_t5")
        self.assertIn(
            ("problem_text", "option_t5", "baseline"),
            {(edge["source"], edge["target"], edge["type"]) for edge in visible["edges"]},
        )
        self.assertNotIn("baseline_use", {edge["type"] for edge in visible["edges"]})

    def test_graph_component_selection_only_accepts_visible_nodes(self) -> None:
        visible_node_ids = ["problem_text", "option_t5"]

        selected = graph_component_selected_node_id(
            {"selected_node_id": "option_t5", "event_type": "node_click"},
            visible_node_ids,
        )
        missing = graph_component_selected_node_id(
            {"selected_node_id": "option_hidden", "event_type": "node_click"},
            visible_node_ids,
        )

        self.assertEqual(selected, "option_t5")
        self.assertIsNone(missing)
        self.assertEqual(
            graph_component_event_id({"selected_node_id": "option_t5", "event_type": "node_click", "event_id": "evt-1"}),
            "evt-1",
        )

    def test_graph_click_and_detail_select_use_separate_state_keys(self) -> None:
        source = (ROOT_DIR / "src" / "research_cockpit" / "ui" / "app.py").read_text(encoding="utf-8")

        self.assertIn("graph_component_processed_event_id", source)
        self.assertIn("new_component_click", source)
        self.assertIn('key="research_graph_component"', source)
        self.assertIn('select_key = "graph_detail_select"', source)
        self.assertIn("selected_from_select = st.session_state.get(select_key)", source)
        self.assertIn('st.session_state["graph_detail_node"] = clicked_node_id', source)
        self.assertIn('st.session_state["graph_detail_select"] = clicked_node_id', source)
        click_index = source.find("new_component_click")
        controls_index = source.find("Graph Controls")
        detail_index = source.find("with detail:", controls_index)
        self.assertNotEqual(click_index, -1)
        self.assertNotEqual(controls_index, -1)
        self.assertNotEqual(detail_index, -1)
        self.assertNotIn("st.rerun()", source[click_index:controls_index])
        self.assertNotIn("selection_changed", source)

    def test_graph_component_layout_ignores_selection_only_changes(self) -> None:
        source = (
            ROOT_DIR
            / "src"
            / "research_cockpit"
            / "ui"
            / "graph_component"
            / "frontend"
            / "src"
            / "GraphComponent.tsx"
        ).read_text(encoding="utf-8")

        self.assertIn('selectionOnlyEdgeTypes = new Set(["baseline_use"])', source)
        self.assertIn("function baselineUseEdgeFor", source)
        self.assertIn("function visibleGraphEdges", source)
        self.assertIn("effective_baseline_option_id?: string", source)
        self.assertIn("function layoutSignature", source)
        self.assertIn("function renderSignature", source)
        self.assertIn("const layoutKey = useMemo(() => layoutSignature(payload), [payload])", source)
        self.assertIn("const renderKey = useMemo(() => renderSignature(payload), [payload])", source)
        self.assertIn("visibleGraphEdges(payload)", source)
        self.assertIn("selectedBaselineEdge", source)
        self.assertIn("isSelectionOnlyFlowEdge", source)
        self.assertIn("key={layoutKey}", source)
        self.assertNotIn("return layoutNodes(flowNodes, payload.edges || [])", source)
        self.assertNotIn("graphSignature", source)

    def test_graph_view_mode_radio_clears_stale_workstream_before_rerun(self) -> None:
        source = (ROOT_DIR / "src" / "research_cockpit" / "ui" / "app.py").read_text(encoding="utf-8")
        callback_index = source.find("def sync_graph_view_mode_filters")
        radio_index = source.find('key="graph_view_mode"')
        callback_arg_index = source.find("on_change=sync_graph_view_mode_filters", radio_index)

        self.assertNotEqual(callback_index, -1)
        self.assertNotEqual(radio_index, -1)
        self.assertNotEqual(callback_arg_index, -1)
        self.assertLess(callback_index, radio_index)

    def test_graph_branch_visibility_controls_are_wired_to_session_and_saved_views(self) -> None:
        source = (ROOT_DIR / "src" / "research_cockpit" / "ui" / "app.py").read_text(encoding="utf-8")

        self.assertIn("render_branch_visibility_controls(", source)
        self.assertIn("graph_collapsed_branch_roots", source)
        self.assertIn("graph_revealed_child_roots", source)
        self.assertIn('"collapsed_branch_roots": [', source)
        self.assertIn('"revealed_child_roots": [', source)
        self.assertIn('text["reset_branch_visibility"]', source)
        self.assertIn("revealable_child_ids_for_view(", source)
        self.assertIn("all_node_ids,", source)

    def test_graph_component_build_availability_detects_missing_assets(self) -> None:
        temp_root = ROOT_DIR / ".test_tmp" / "ui"
        build_dir = temp_root / "graph_component_build_available"
        assets_dir = build_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        index_path = build_dir / "index.html"
        script_path = assets_dir / "index.js"
        for path in (index_path, script_path):
            if path.exists():
                path.unlink()

        self.assertFalse(graph_component_build_available(build_dir))

        index_path.write_text("<div></div>", encoding="utf-8")
        script_path.write_text("export default {}", encoding="utf-8")

        self.assertTrue(graph_component_build_available(build_dir))

    def test_default_detail_and_tab_order_prioritize_research_graph(self) -> None:
        graph = {"current_focus_node": "problem_text"}
        text = {"research_graph": "研究图谱", "dashboard": "总览", "experiment_matrix": "实验矩阵"}

        self.assertEqual(default_detail_node_id(graph, ["stage_text", "problem_text"]), "problem_text")
        self.assertEqual(ordered_tab_keys(text)[0], "research_graph")
        self.assertEqual(ordered_tab_labels(text)[0], "研究图谱")

    def test_default_status_filter_uses_focus_mode_hide_statuses(self) -> None:
        graph = {"focus_mode": {"hide_statuses": ["rejected", "parked", "archived"]}}

        defaults = default_selected_statuses(graph, ["active", "archived", "done", "parked", "rejected"])

        self.assertEqual(defaults, ["active"])

    def test_default_status_filter_hides_terminal_and_rejected_statuses_without_focus_mode(self) -> None:
        defaults = default_selected_statuses(
            {},
            ["active", "cancelled", "done", "parked", "planned", "rejected", "running"],
        )

        self.assertEqual(defaults, ["active", "planned", "running"])

    def test_default_node_type_filter_treats_artifact_as_supporting_material(self) -> None:
        defaults = default_selected_node_types(["artifact", "decision", "experiment", "option", "problem", "stage"])

        self.assertEqual(defaults, ["decision", "experiment", "option", "problem", "stage"])

    def test_node_overview_uses_type_specific_purpose_fields(self) -> None:
        nodes = {
            "problem_text": SimpleNamespace(
                id="problem_text",
                title="Weak text control",
                type="problem",
                status="active",
                priority="high",
                summary="Fallback problem summary",
                parent="stage_text",
                children=[],
                tags=[],
                raw={"question": "Why is text control weak?"},
            ),
            "option_t5": SimpleNamespace(
                id="option_t5",
                title="T5 option",
                type="option",
                status="active",
                priority="medium",
                summary="Fallback option summary",
                parent="problem_text",
                children=[],
                tags=[],
                raw={"hypothesis": "T5 alignment improves edits."},
            ),
            "decision_t5": SimpleNamespace(
                id="decision_t5",
                title="Use T5",
                type="decision",
                status="proposed",
                priority="high",
                summary="Fallback decision summary",
                parent="option_t5",
                children=[],
                tags=[],
                raw={"rationale": "Best evidence so far."},
            ),
        }

        problem = build_node_overview(nodes["problem_text"], nodes, {}, [], [])
        option = build_node_overview(nodes["option_t5"], nodes, {}, [], [])
        decision = build_node_overview(nodes["decision_t5"], nodes, {}, [], [])

        self.assertEqual(problem["purpose"], "Why is text control weak?")
        self.assertEqual(option["purpose"], "T5 alignment improves edits.")
        self.assertEqual(decision["purpose"], "Best evidence so far.")

    def test_node_overview_prioritizes_result_summary_for_done_experiment(self) -> None:
        node = SimpleNamespace(
            id="experiment_t5",
            title="T5 evaluation",
            type="experiment",
            status="done",
            priority=None,
            summary="Run T5 evaluation.",
            parent="option_t5",
            children=[],
            tags=[],
            raw={"result_summary": "T5 improves alignment but hurts latency."},
        )

        overview = build_node_overview(node, {"experiment_t5": node}, {}, [], [])

        self.assertEqual(overview["current_state"]["kind"], "result_summary")
        self.assertEqual(overview["current_state"]["items"], ["T5 improves alignment but hurts latency."])

    def test_node_overview_combines_next_actions_and_related_suggestions(self) -> None:
        node = SimpleNamespace(
            id="experiment_t5",
            title="T5 evaluation",
            type="experiment",
            status="done",
            priority=None,
            summary="Run T5 evaluation.",
            parent=None,
            children=[],
            tags=[],
            raw={"next_actions": ["Record final metrics."]},
        )
        suggestions = [
            {"source_node_id": "experiment_t5", "action": "Promote the strongest option."},
            {"source_node_id": "other", "related_node_ids": ["experiment_t5"], "action": "Review related decision."},
            {"source_node_id": "other", "action": "Ignore unrelated suggestion."},
        ]

        overview = build_node_overview(node, {"experiment_t5": node}, {}, [], suggestions)

        self.assertEqual(
            overview["next"],
            ["Record final metrics.", "Promote the strongest option.", "Review related decision."],
        )

    def test_node_overview_limits_key_resources_and_uses_readable_relation_labels(self) -> None:
        parent = SimpleNamespace(
            id="option_t5",
            title="T5 option",
            type="option",
            status="active",
            priority=None,
            summary="",
            parent=None,
            children=["experiment_t5"],
            tags=[],
            raw={},
        )
        child = SimpleNamespace(
            id="experiment_t5",
            title="T5 evaluation",
            type="experiment",
            status="done",
            priority=None,
            summary="Run T5 evaluation.",
            parent="option_t5",
            children=["decision_t5"],
            tags=[],
            raw={},
        )
        decision = SimpleNamespace(
            id="decision_t5",
            title="Use T5",
            type="decision",
            status="proposed",
            priority=None,
            summary="",
            parent="experiment_t5",
            children=[],
            tags=[],
            raw={},
        )
        resources = [
            {"node_id": "experiment_t5", "target": f"resource_{index}.json", "kind": "path"}
            for index in range(4)
        ]

        overview = build_node_overview(
            child,
            {"option_t5": parent, "experiment_t5": child, "decision_t5": decision},
            {},
            resources,
            [],
        )

        self.assertEqual([row["target"] for row in overview["key_resources"]], ["resource_0.json", "resource_1.json", "resource_2.json"])
        self.assertEqual(overview["relations"]["parent"][0]["label"], "T5 option | option_t5 | option/active")
        self.assertEqual(overview["relations"]["children"][0]["label"], "Use T5 | decision_t5 | decision/proposed")

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
        decision_command = build_promote_decision_command("option_t5")
        check_command = build_check_decision_acceptance_command("decision_t5")
        checklist_command = build_update_decision_checklist_command("decision_t5")
        evidence_command = build_update_decision_evidence_command("decision_t5")
        accept_command = build_accept_decision_command("decision_t5")
        commands_by_route = {
            "coord decide": [decision_command, checklist_command, evidence_command, accept_command],
            "context": [check_command, build_option_workstream_context_command("option_t5")],
            "coord assign": [
                build_claim_option_command("option_t5"),
            ],
            "work close": [build_report_option_workstream_command("option_t5")],
            "maintenance repair": [build_cleanup_suggestion_lifecycle_command()],
        }
        commands = [command for values in commands_by_route.values() for command in values]

        self.assertTrue(all(command.startswith("research-cockpit ") for command in commands))
        self.assertFalse(any("D:\\Tools" in command for command in commands))
        self.assertFalse(any("miniconda" in command.lower() for command in commands))
        target_commands = {
            "option_t5": decision_command,
            "decision_t5": check_command,
        }
        for target_id, command in target_commands.items():
            with self.subTest(target_id=target_id):
                self.assertIn(target_id, command)
        self.assertNotIn("coord review", check_command)
        self.assertNotIn("--file", check_command)
        for route, route_commands in commands_by_route.items():
            with self.subTest(route=route):
                self.assertTrue(
                    all(command.startswith(f"research-cockpit {route}") for command in route_commands),
                    route_commands,
                )
        removed_routes = (
            "record-finding",
            "promote-decision",
            "check-decision-acceptance",
            "update-decision-checklist",
            "update-decision-evidence",
            "accept-decision",
            "create-note",
            "set-focus",
            "claim-option",
            "option-workstream-context",
            "report-option-workstream",
            "apply-suggestion",
            "update-suggestion-state",
            "cleanup-suggestion-lifecycle",
        )
        self.assertFalse(any(f"research-cockpit {route}" in command for route in removed_routes for command in commands))

    def test_workflow_command_templates_ignore_python_env_override(self) -> None:
        previous = os.environ.get("RESEARCH_COCKPIT_PYTHON")
        os.environ["RESEARCH_COCKPIT_PYTHON"] = "uv run python"
        self.addCleanup(
            lambda: (
                os.environ.__setitem__("RESEARCH_COCKPIT_PYTHON", previous)
                if previous is not None
                else os.environ.pop("RESEARCH_COCKPIT_PYTHON", None)
            )
        )

        command = build_promote_decision_command("option_t5")

        self.assertTrue(command.startswith("research-cockpit coord decide"))

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
                "suggested_command": "research-cockpit update-status --id exp_t5 --status running",
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

        self.assertEqual(rows[0]["related_node_ids"], "option_t5")
        self.assertEqual(rows[0]["is_focus_related"], "yes")
        self.assertEqual(rows[0]["queued_in_current"], "yes")
        self.assertEqual(rows[1]["queued_in_node"], "yes")
        self.assertEqual(rows[1]["lifecycle_state"], "completed")
        self.assertEqual(rows[1]["lifecycle_reason"], "Done manually.")
        self.assertEqual([item["id"] for item in filtered], ["s1"])

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
        self.assertIn("research-cockpit maintenance repair", dry_run_command)
        self.assertNotEqual(dry_run_command, clean_completed_command)
        self.assertIn("dry_run", dry_run_command)
        self.assertIn("completed", clean_completed_command)
        self.assertIn("older_7_days_execute", clean_completed_command)

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

    def test_format_decision_repair_hints_ignores_ready_decisions(self) -> None:
        rows = format_decision_repair_hints({
            "decision_id": "decision_t5",
            "blocking_failures": [],
        })

        self.assertEqual(rows, [])

    def test_format_decision_repair_hints_maps_checklist_fields_to_command(self) -> None:
        rows = format_decision_repair_hints({
            "decision_id": "decision_t5",
            "blocking_failures": [
                {
                    "id": "alternatives_considered",
                    "label": "Alternatives were considered",
                    "reason": "alternatives_considered must be non-empty.",
                    "blocking": True,
                },
                {
                    "id": "consequences",
                    "label": "Consequences are recorded",
                    "reason": "consequences must be non-empty.",
                    "blocking": True,
                },
                {
                    "id": "next_required_actions",
                    "label": "Next required actions are recorded",
                    "reason": "next_required_actions must be non-empty.",
                    "blocking": True,
                },
            ],
        })

        self.assertEqual([row["target_field"] for row in rows], [
            "alternatives_considered",
            "consequences",
            "next_required_actions",
        ])
        self.assertTrue(all(row["repair_kind"] == "command" for row in rows))
        self.assertTrue(all("research-cockpit coord decide" in row["suggested_command"] for row in rows))

    def test_format_decision_repair_hints_maps_evidence_failures_to_commands(self) -> None:
        rows = format_decision_repair_hints({
            "decision_id": "decision_t5",
            "blocking_failures": [
                {
                    "id": "supporting_evidence",
                    "label": "Supporting experiments contain evidence",
                    "reason": "At least one supporting experiment must contain findings.",
                    "blocking": True,
                    "related_node_ids": ["exp_t5"],
                },
                {
                    "id": "evidence_summary",
                    "label": "Evidence summary is present",
                    "reason": "evidence_summary must be non-empty.",
                    "blocking": True,
                },
            ],
        })

        self.assertEqual(rows[0]["repair_kind"], "command")
        self.assertIn("research-cockpit work close", rows[0]["suggested_command"])
        self.assertIn("research-cockpit coord decide", rows[0]["suggested_command"])
        self.assertEqual(rows[1]["target_field"], "evidence_summary")
        self.assertIn("research-cockpit coord decide", rows[1]["suggested_command"])

    def test_format_decision_repair_hints_maps_structural_failures_to_yaml(self) -> None:
        rows = format_decision_repair_hints({
            "decision_id": "decision_t5",
            "blocking_failures": [
                {
                    "id": "decision_parent",
                    "label": "Decision parent is an option",
                    "reason": "Decision parent must be an option node.",
                    "blocking": True,
                },
                {
                    "id": "alternative_refs",
                    "label": "Alternative references are valid",
                    "reason": "Invalid alternative option reference(s): option_missing",
                    "blocking": True,
                    "related_node_ids": ["option_missing"],
                },
            ],
        })

        self.assertEqual([row["repair_kind"] for row in rows], ["yaml", "yaml"])
        self.assertEqual(rows[0]["target_field"], "parent")
        self.assertEqual(rows[1]["target_field"], "alternatives_considered")
        self.assertEqual(rows[0]["suggested_command"], "")
        self.assertEqual(rows[1]["suggested_command"], "")

    def test_format_decision_repair_hints_maps_invalid_experiment_refs_to_yaml(self) -> None:
        rows = format_decision_repair_hints({
            "decision_id": "decision_t5",
            "blocking_failures": [
                {
                    "id": "supporting_experiments",
                    "label": "Supporting experiments are present",
                    "reason": "Invalid supporting experiment reference(s): exp_missing",
                    "blocking": True,
                    "related_node_ids": ["exp_missing"],
                },
            ],
        })

        self.assertEqual(rows[0]["repair_kind"], "yaml")
        self.assertEqual(rows[0]["target_field"], "supporting_experiments")
        self.assertEqual(rows[0]["suggested_command"], "")

    def test_decision_repair_hints_are_rendered_in_decision_views(self) -> None:
        source = (ROOT_DIR / "src" / "research_cockpit" / "ui" / "app.py").read_text(encoding="utf-8")

        self.assertIn("format_decision_repair_hints", source)
        self.assertGreaterEqual(source.count("render_decision_repair_hints(checklist, text)"), 2)


if __name__ == "__main__":
    unittest.main()
