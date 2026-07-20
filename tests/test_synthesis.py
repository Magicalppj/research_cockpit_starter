from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.commands.build_dashboard import build_dashboard
from research_cockpit.public_contracts import parse_public_contract, public_contract_example
from research_cockpit.storage import load_yaml, save_yaml
from research_cockpit.synthesis import build_synthesis_packet
from research_cockpit.work_packets import build_work_packet


NOW = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
REVISION_A = "result-v1:" + "a" * 64
REVISION_B = "result-v1:" + "b" * 64
REVISION_UNRELATED = "result-v1:" + "c" * 64


def _empty_lease() -> dict:
    return {
        "lease_id": None,
        "owner_agent_id": None,
        "lease_epoch": 0,
        "heartbeat_at": None,
        "expires_at": None,
    }


class SynthesisPacketTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"synthesis_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)
        save_yaml(self.root / "current_state.yaml", {})
        self._write_graph()
        self._write_evidence_sidecars()
        self._write_assignments()
        build_dashboard(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_graph(self) -> None:
        nodes = [
            {
                "id": "problem_x",
                "type": "problem",
                "title": "Choose a strategy",
                "status": "active",
                "children": ["option_a", "option_b"],
            },
            {
                "id": "option_a",
                "type": "option",
                "title": "Option A",
                "status": "active",
                "parent": "problem_x",
                "children": ["experiment_a"],
            },
            {
                "id": "option_b",
                "type": "option",
                "title": "Option B",
                "status": "active",
                "parent": "problem_x",
                "children": ["experiment_b"],
            },
            {
                "id": "experiment_a",
                "type": "experiment",
                "title": "Experiment A",
                "status": "done",
                "parent": "option_a",
                "findings": [
                    {
                        "id": "finding_a",
                        "statement": "Option A improved accuracy within the latency budget.",
                        "confidence": "strong",
                        "outcome": "positive",
                        "metrics": ["accuracy=0.84", "latency_ms=18"],
                        "evidence": ["experiment_a"],
                        "linked_artifacts": [],
                    }
                ],
            },
            {
                "id": "experiment_b",
                "type": "experiment",
                "title": "Experiment B",
                "status": "queued",
                "parent": "option_b",
            },
        ]
        for node in nodes:
            save_yaml(self.root / "graph" / "nodes" / f"{node['id']}.yaml", node)

    def _bundle(self, assignment_id: str, revision: str, *, outcome: str) -> dict:
        bundle = public_contract_example("evidence_bundle_v1")
        bundle.update(
            {
                "assignment_id": assignment_id,
                "operation_id": f"op_{assignment_id}",
                "revision": revision,
                "outcome": outcome,
                "summary": f"Evidence from {assignment_id}.",
            }
        )
        bundle["runs"]["items"] = ["run_a"]
        bundle["runs"]["total"] = 1
        bundle["runs"]["omitted"] = 0
        bundle["findings"]["items"] = ["finding_a"]
        bundle["findings"]["total"] = 1
        bundle["findings"]["omitted"] = 0
        bundle["artifact_records"]["items"] = ["artifact_record_a"]
        bundle["artifact_records"]["total"] = 1
        bundle["artifact_records"]["omitted"] = 0
        return bundle

    def _assignment(
        self,
        assignment_id: str,
        *,
        status: str,
        root_node: str,
        current_node: str,
        result: dict | None = None,
    ) -> dict:
        return {
            "assignment_id": assignment_id,
            "agent_id": None,
            "status": status,
            "kind": "experiment",
            "root_node": root_node,
            "current_node": current_node,
            "allowed_subtree": {"root": root_node, "policy": "descendants_only"},
            "scope": {
                "root_node": root_node,
                "subtree_policy": "descendants_only",
                "write_policy": "exclusive",
            },
            "inputs": {
                "effective_baseline_revision": None,
                "dependency_revisions": {},
            },
            "input_revision": f"input-v1:{assignment_id}",
            "lease": _empty_lease(),
            "review": {"required": False, "status": "not_required", "result_revision": None},
            "result": result or {},
        }

    def _write_assignments(self) -> None:
        save_yaml(
            self.root / "assignments" / "assign_a.yaml",
            self._assignment(
                "assign_a",
                status="completed",
                root_node="option_a",
                current_node="experiment_a",
                result=self._bundle("assign_a", REVISION_A, outcome="positive"),
            ),
        )
        save_yaml(
            self.root / "assignments" / "assign_b.yaml",
            self._assignment(
                "assign_b",
                status="queued",
                root_node="option_b",
                current_node="experiment_b",
            ),
        )
        save_yaml(
            self.root / "assignments" / "assign_unrelated.yaml",
            self._assignment(
                "assign_unrelated",
                status="completed",
                root_node="option_b",
                current_node="experiment_b",
                result=self._bundle(
                    "assign_unrelated",
                    REVISION_UNRELATED,
                    outcome="negative",
                ),
            ),
        )
        synthesis = self._assignment(
            "assign_synthesis",
            status="queued",
            root_node="problem_x",
            current_node="problem_x",
        )
        synthesis.update(
            {
                "kind": "synthesis",
                "objective": "Which strategy should become the baseline?",
                "dependencies": [
                    {"assignment_id": "assign_a", "required_status": "completed"},
                    {"assignment_id": "assign_b", "required_status": "completed"},
                ],
                "inputs": {
                    "effective_baseline_revision": None,
                    "dependency_revisions": {
                        "assign_a": REVISION_A,
                        "assign_b": REVISION_B,
                    },
                },
                "synthesis": {
                    "research_question": "Which strategy should become the baseline?",
                    "candidate_options": ["option_a", "option_b"],
                    "decision_criteria": ["accuracy", "latency"],
                    "unresolved_questions": ["Does Option B meet the target?"],
                },
            }
        )
        save_yaml(self.root / "assignments" / "assign_synthesis.yaml", synthesis)

    def _write_evidence_sidecars(self) -> None:
        save_yaml(
            self.root / "runs" / "run_a.yaml",
            {"run_id": "run_a", "status": "completed", "experiment_id": "experiment_a"},
        )
        save_yaml(
            self.root / "artifact_records" / "experiment_a.yaml",
            {
                "schema_version": "artifact_records_v1",
                "experiment_id": "experiment_a",
                "records": {
                    "artifact_record_a": {
                        "record_id": "artifact_record_a",
                        "experiment_id": "experiment_a",
                        "run_id": "run_a",
                        "links": {},
                    }
                },
            },
        )
        save_yaml(
            self.root / "gate_results" / "gate_a.yaml",
            {
                "schema_version": "gate_result_record_v1",
                "gate_id": "gate_a",
                "experiment_id": "experiment_a",
                "run_id": "run_a",
                "gate_result_file": "gate_results/gate_a.json",
            },
        )
        gate_path = self.root / "gate_results" / "gate_a.json"
        gate_path.parent.mkdir(parents=True, exist_ok=True)
        gate_path.write_text(
            json.dumps(
                {
                    "gate_type": "quality",
                    "passed": True,
                    "experiment_id": "experiment_a",
                    "run_id": "run_a",
                    "summary": "Quality gate passed.",
                }
            ),
            encoding="utf-8",
        )

    def test_packet_uses_only_selected_revision_bound_evidence(self) -> None:
        packet = build_synthesis_packet(self.root, "assign_synthesis")

        parse_public_contract(packet)
        self.assertRegex(packet["revision"], r"^synthesis-v1:[a-f0-9]{64}$")
        self.assertEqual(packet["candidate_options"]["items"], ["option_a", "option_b"])
        self.assertEqual(packet["evidence_bundles"]["items"], [REVISION_A])
        self.assertNotIn(REVISION_UNRELATED, packet["evidence_bundles"]["items"])
        self.assertEqual(packet["outcome_summaries"]["items"][0]["confidence"], "high")
        self.assertEqual(packet["metrics"]["items"][0]["name"], "accuracy")
        self.assertEqual(packet["metrics"]["items"][0]["value"], 0.84)
        self.assertEqual(packet["gate_summaries"]["items"][0]["gate_id"], "gate_a")
        self.assertEqual(packet["artifact_links"]["items"], ["artifact_record_a"])
        self.assertTrue(any("assign_b" in item for item in packet["missing_evidence"]["items"]))
        self.assertTrue(
            any("assign_b" in item for item in packet["stale_input_warnings"]["items"])
        )

    def test_synthesis_work_open_embeds_packet_within_existing_budget(self) -> None:
        packet = build_work_packet(self.root, "assign_synthesis", now=NOW)
        encoded = json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        self.assertEqual(packet["kind"], "synthesis")
        self.assertEqual(packet["synthesis_packet"]["schema_version"], "synthesis_packet_v1")
        self.assertLess(len(encoded), 8 * 1024)
        unchanged = build_work_packet(
            self.root,
            "assign_synthesis",
            since_revision=packet["revision"],
            now=NOW,
        )
        self.assertEqual(unchanged["changed"], False)
        self.assertNotIn("synthesis_packet", unchanged)

    def test_candidate_option_fallback_uses_dependency_roots(self) -> None:
        assignment_path = self.root / "assignments" / "assign_synthesis.yaml"
        assignment = load_yaml(assignment_path)
        assignment["synthesis"].pop("candidate_options")
        save_yaml(assignment_path, assignment)

        packet = build_synthesis_packet(self.root, "assign_synthesis")

        self.assertEqual(packet["candidate_options"]["items"], ["option_a", "option_b"])
        self.assertNotIn("assign_a", packet["candidate_options"]["items"])

    def test_synthesis_collections_and_text_are_bounded(self) -> None:
        assignment_path = self.root / "assignments" / "assign_synthesis.yaml"
        assignment = load_yaml(assignment_path)
        assignment["synthesis"]["decision_criteria"] = [f"criterion_{index}" for index in range(40)]
        assignment["synthesis"]["unresolved_questions"] = ["q" * 500 for _ in range(40)]
        save_yaml(assignment_path, assignment)

        packet = build_synthesis_packet(self.root, "assign_synthesis")
        encoded = json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        self.assertEqual(
            len(packet["evidence_bundles"]["items"]),
            len(packet["outcome_summaries"]["items"]),
        )
        self.assertEqual(
            packet["evidence_bundles"]["items"],
            [item["result_revision"] for item in packet["outcome_summaries"]["items"]],
        )
        self.assertEqual(packet["decision_criteria"]["limit"], 20)
        self.assertEqual(len(packet["decision_criteria"]["items"]), 20)
        self.assertGreater(packet["unresolved_questions"]["omitted"], 0)
        self.assertLess(len(encoded), 6 * 1024)

    def test_missing_index_does_not_scan_unrelated_gate_records(self) -> None:
        save_yaml(
            self.root / "gate_results" / "gate_unrelated.yaml",
            {
                "schema_version": "gate_result_record_v1",
                "gate_id": "gate_unrelated",
                "experiment_id": "experiment_b",
                "run_id": "run_unrelated",
                "gate_result_file": "gate_results/gate_unrelated.json",
            },
        )
        (self.root / "dashboards" / "validation_index.json").unlink()
        loaded_paths: list[Path] = []

        def tracking_load(path: Path) -> object:
            loaded_paths.append(Path(path))
            return load_yaml(path)

        with patch("research_cockpit.synthesis.load_yaml", side_effect=tracking_load):
            packet = build_synthesis_packet(self.root, "assign_synthesis")

        self.assertEqual(packet["gate_summaries"]["items"], [])
        self.assertFalse(
            any(path.name == "gate_unrelated.yaml" for path in loaded_paths),
            loaded_paths,
        )
        self.assertTrue(
            any(
                "validation index" in item.lower()
                for item in packet["missing_evidence"]["items"]
            )
        )


if __name__ == "__main__":
    unittest.main()
