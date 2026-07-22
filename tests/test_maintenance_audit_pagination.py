from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.commands import maintenance_role_audit
from research_cockpit.maintenance import (
    COMPACT_AUDIT_RESULT_BYTES,
    _bounded_state_statistics,
    _summarize_maintenance_audit,
)


class MaintenanceAuditPaginationTests(unittest.TestCase):
    def _detail(self) -> dict[str, object]:
        worktrees = [
            {
                "label": "main",
                "path": "/repo",
                "branch": "main",
                "safe_to_remove": False,
                "blockers": ["primary_worktree"],
            }
        ]
        worktrees.extend(
            {
                "label": f"worker_{index:03d}",
                "path": f"/repo/worktrees/worker_{index:03d}",
                "branch": f"agent/worker_{index:03d}",
                "safe_to_remove": True,
                "blockers": [],
            }
            for index in range(80)
        )
        artifacts = [
            {
                "artifact_id": "artifact_blocked",
                "title": "Protected artifact",
                "retention_class": "must_keep",
                "total_size_bytes": 10,
                "file_count": 1,
                "large": True,
                "missing_retention": False,
                "cleanup_candidate": False,
                "blockers": ["active_assignment"],
                "warnings": [],
            },
            {
                "artifact_id": "artifact_cleanup",
                "title": "Disposable artifact",
                "retention_class": "disposable_cache",
                "total_size_bytes": 20,
                "file_count": 2,
                "large": True,
                "missing_retention": False,
                "cleanup_candidate": True,
                "blockers": [],
                "warnings": [],
            },
        ]
        return {
            "ok": True,
            "schema_version": "maintenance_audit_detail_v1",
            "root": "/state",
            "repo": "/repo",
            "base": "main",
            "state_statistics": {
                "size_bytes": 40,
                "file_count": 4,
                "exact": True,
                "lower_bound": False,
                "truncated": False,
                "unsafe_entry_count": 0,
                "file_limit": 1000,
                "entries_scanned": 4,
                "entry_limit": 2000,
            },
            "active_assignments": [{"assignment_id": "assignment_active"}],
            "running_runs": [{"run_id": "run_active", "output_root": "outputs/run"}],
            "active_resources": [
                {
                    "run_id": "run_active",
                    "output_root": "outputs/run",
                    "log_root": "logs/run",
                    "progress_file": "outputs/run/progress.json",
                    "config_file": "outputs/run/config.yaml",
                }
            ],
            "worktree_audit": {"worktrees": worktrees},
            "branch_audit": {
                "branches": [
                    {
                        "name": "agent/remove_me",
                        "branch_class": "agent",
                        "checked_out": False,
                        "merged": True,
                        "delete_candidate": True,
                        "recommended_action": "delete_candidate",
                        "blockers": [],
                    }
                ]
            },
            "artifact_retention_audit": {
                "artifacts": artifacts,
                "warnings": ["missing_retention"],
                "artifact_inventory": {
                    "status": "current",
                    "aggregates": {
                        "records": {
                            "count": 3,
                            "by_storage_mode": {"legacy": 1, "reference": 2},
                            "by_ownership": {"external": 3},
                            "by_retention_class": {"must_keep": 1, "unknown": 2},
                            "by_integrity_level": {"manifest": 2, "unverified": 1},
                            "by_availability_status": {"available": 3},
                            "statistics": {
                                "size_bytes": 30,
                                "file_count": 3,
                                "exact": True,
                                "lower_bound": False,
                                "unknown_or_incomplete_count": 0,
                            },
                        },
                        "graph_artifacts": {"count": 2, "by_retention_class": {"must_keep": 1, "disposable_cache": 1}},
                        "managed_payloads": {
                            "count": 0,
                            "count_exact": True,
                            "count_lower_bound": False,
                            "statistics": {
                                "size_bytes": 0,
                                "file_count": 0,
                                "exact": True,
                                "lower_bound": False,
                                "unknown_or_incomplete_count": 0,
                            },
                        },
                        "managed_orphans": {
                            "count": 0,
                            "count_exact": True,
                            "count_lower_bound": False,
                            "statistics": {
                                "size_bytes": 0,
                                "file_count": 0,
                                "exact": True,
                                "lower_bound": False,
                                "unknown_or_incomplete_count": 0,
                            },
                        },
                    },
                    "scan": {"managed_store": {"truncated": False}},
                },
            },
            "artifact_compaction_counts": {"can_demote": 1},
            "dashboard_performance_warnings": [{"code": "large_graph", "message": "x" * 500}],
            "unsafe_cleanup_blockers": ["active_assignment"],
            "recommended_next_actions": ["Review candidates."],
            "git_hygiene": {
                "schema_version": "git_hygiene_v1",
                "storage_roots": [
                    {
                        "kind": "state",
                        "path": "/state",
                        "inside_git_worktree": False,
                        "overlapping_worktrees": [],
                        "ignore": {"coverage": "outside_worktree"},
                        "risks": [],
                    }
                ],
                "status": {
                    "truncated": True,
                    "untracked": {"count": 2, "exact": False, "lower_bound": True},
                    "ignored": {"count": 3, "exact": False, "lower_bound": True},
                    "tracked_modified": {"count": 0, "exact": False, "lower_bound": True},
                },
                "risks": [],
            },
        }

    def test_default_summary_is_paginated_and_below_compact_budget(self) -> None:
        payload = _summarize_maintenance_audit(self._detail(), limit=5)

        self.assertEqual(payload["schema_version"], "maintenance_audit_v2")
        self.assertEqual(payload["summary"]["active"]["assignment_count"], 1)
        self.assertEqual(payload["summary"]["active"]["run_count"], 1)
        self.assertEqual(payload["summary"]["state"]["file_count"], 4)
        self.assertEqual(payload["summary"]["git_hygiene"]["status"]["ignored"]["lower_bound"], True)
        self.assertEqual(payload["summary"]["candidate_counts"]["by_classification"]["can_quarantine"], 1)
        self.assertLessEqual(
            len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
            COMPACT_AUDIT_RESULT_BYTES,
        )
        self.assertLessEqual(len(payload["candidate_page"]["items"]), 5)
        self.assertIsNotNone(payload["candidate_page"]["next_cursor"])
        self.assertNotIn("artifact_retention_audit", payload)

    def test_cursor_is_stable_and_classification_filters_candidates(self) -> None:
        first = _summarize_maintenance_audit(self._detail(), limit=3)
        second = _summarize_maintenance_audit(
            self._detail(),
            limit=3,
            cursor=first["candidate_page"]["next_cursor"],
        )
        protected = _summarize_maintenance_audit(
            self._detail(),
            limit=10,
            classification="must_keep",
        )

        first_ids = {item["key"] for item in first["candidate_page"]["items"]}
        second_ids = {item["key"] for item in second["candidate_page"]["items"]}
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(
            {item["classification"] for item in protected["candidate_page"]["items"]},
            {"must_keep"},
        )
        with self.assertRaisesRegex(ValueError, "invalid maintenance audit cursor"):
            _summarize_maintenance_audit(self._detail(), cursor="not-a-cursor")

    def test_control_state_statistics_are_bounded_without_payload_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assignments = root / "assignments"
            assignments.mkdir()
            for index in range(3):
                (assignments / f"assignment_{index}.yaml").write_text(
                    "schema_version: assignment_v1\n",
                    encoding="utf-8",
                )
            payload = root / "artifacts" / "large" / "payload.bin"
            payload.parent.mkdir(parents=True)
            payload.write_bytes(b"x" * 4096)

            statistics = _bounded_state_statistics(root, max_files=1)

        self.assertEqual(statistics["file_count"], 1)
        self.assertTrue(statistics["truncated"])
        self.assertTrue(statistics["lower_bound"])
        self.assertLess(statistics["size_bytes"], 4096)

    def test_role_cli_forwards_pagination_and_deep_git_flags(self) -> None:
        result = _summarize_maintenance_audit(self._detail(), limit=1)
        with (
            patch.object(
                sys,
                "argv",
                [
                    "maintenance-role-audit",
                    "--root",
                    "/state",
                    "--repo",
                    "/repo",
                    "--limit",
                    "7",
                    "--classification",
                    "needs_review",
                    "--cursor",
                    "cursor-value",
                    "--id",
                    "artifact_cleanup",
                    "--deep-git",
                    "--json",
                    "--compact",
                ],
            ),
            patch.object(maintenance_role_audit, "maintenance_audit_payload", return_value=result) as audit,
            patch.object(maintenance_role_audit, "emit_json") as emit,
        ):
            maintenance_role_audit.main()

        self.assertEqual(audit.call_args.kwargs["limit"], 7)
        self.assertEqual(audit.call_args.kwargs["classification"], "needs_review")
        self.assertEqual(audit.call_args.kwargs["cursor"], "cursor-value")
        self.assertEqual(audit.call_args.kwargs["candidate_id"], "artifact_cleanup")
        self.assertTrue(audit.call_args.kwargs["deep_git"])
        self.assertTrue(emit.call_args.kwargs["compact"])


if __name__ == "__main__":
    unittest.main()
