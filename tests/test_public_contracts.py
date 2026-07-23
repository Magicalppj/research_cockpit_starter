from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.public_contracts import (
    PUBLIC_CONTRACT_EXAMPLES,
    ROLE_OPERATIONS,
    WORKFLOW_BUDGETS,
    parse_public_contract,
    public_contract_example,
)


class PublicContractTests(unittest.TestCase):
    def test_frozen_contract_examples_are_json_serializable(self) -> None:
        self.assertEqual(
            set(PUBLIC_CONTRACT_EXAMPLES),
            {
                "work_packet_v1",
                "evidence_bundle_v1",
                "synthesis_packet_v1",
                "coordination_snapshot_v1",
                "work_operation_v1",
            },
        )

        for schema_version, payload in PUBLIC_CONTRACT_EXAMPLES.items():
            with self.subTest(schema_version=schema_version):
                self.assertEqual(payload["schema_version"], schema_version)
                json.dumps(payload)

    def test_contract_examples_are_returned_as_independent_copies(self) -> None:
        first = public_contract_example("work_packet_v1")
        first["assignment_id"] = "changed"

        second = public_contract_example("work_packet_v1")

        self.assertNotEqual(second["assignment_id"], "changed")
        with self.assertRaises(ValueError):
            public_contract_example("unknown_v1")

    def test_role_operations_freeze_the_small_default_surface(self) -> None:
        self.assertEqual(
            ROLE_OPERATIONS["worker"],
            ("open", "claim", "renew", "start", "record", "close"),
        )
        self.assertEqual(ROLE_OPERATIONS["reviewer"], ("open", "report"))
        self.assertEqual(
            ROLE_OPERATIONS["coordinator"],
            ("overview", "assign", "review", "decide", "handoff"),
        )
        self.assertLessEqual(len(ROLE_OPERATIONS["worker"]), 12)
        self.assertLessEqual(len(ROLE_OPERATIONS["coordinator"]), 12)

    def test_workflow_budgets_match_the_accepted_contract(self) -> None:
        self.assertEqual(WORKFLOW_BUDGETS["assigned_worker_cli_invocations"], 3)
        self.assertEqual(WORKFLOW_BUDGETS["reviewer_cli_invocations"], 2)
        self.assertEqual(WORKFLOW_BUDGETS["handoff_cli_invocations"], 1)
        self.assertEqual(WORKFLOW_BUDGETS["core_nested_subprocesses"], 0)
        self.assertEqual(WORKFLOW_BUDGETS["extra_verification_after_internal_success"], 0)
        self.assertEqual(WORKFLOW_BUDGETS["worker_stdout_bytes"], 12 * 1024)
        self.assertEqual(WORKFLOW_BUDGETS["mutation_receipt_bytes"], 2 * 1024)

    def test_contracts_freeze_revisions_lease_epoch_and_bounded_collections(self) -> None:
        packet = public_contract_example("work_packet_v1")
        self.assertEqual(packet["revision"], "packet-v1:abc123")
        self.assertEqual(packet["input_revision"], "input-v1:abc123")
        self.assertEqual(packet["revision_status"], "fresh")
        self.assertEqual(packet["readiness"], "ready")
        self.assertEqual(packet["lease"]["lease_epoch"], 1)

        collections = [
            packet["dependencies"],
            packet["stale_inputs"],
            packet["success_criteria"],
            packet["deliverables"],
            packet["allowed_operations"],
            packet["cursor"]["next_actions"],
        ]
        evidence = public_contract_example("evidence_bundle_v1")
        collections.extend(
            [
                evidence["runs"],
                evidence["findings"],
                evidence["artifact_records"],
                evidence["attempts"],
                evidence["delivery"]["changed_files"],
                evidence["proposals"],
            ]
        )
        synthesis = public_contract_example("synthesis_packet_v1")
        collections.extend(
            synthesis[field]
            for field in (
                "candidate_options",
                "evidence_bundles",
                "outcome_summaries",
                "metrics",
                "gate_summaries",
                "artifact_links",
                "contradictions",
                "missing_evidence",
                "stale_input_warnings",
                "decision_criteria",
                "unresolved_questions",
            )
        )
        snapshot = public_contract_example("coordination_snapshot_v1")
        collections.extend([snapshot["assignments"], snapshot["overlap_warnings"]])
        operation = public_contract_example("work_operation_v1")
        collections.extend(
            [
                operation["allowed_operations"],
                operation["verification"]["commands"],
                operation["warnings"],
            ]
        )

        for collection in collections:
            with self.subTest(collection=collection):
                self.assertEqual(
                    set(collection),
                    {"items", "limit", "total", "omitted"},
                )
                self.assertLessEqual(len(collection["items"]), collection["limit"])
                self.assertEqual(
                    collection["total"],
                    len(collection["items"]) + collection["omitted"],
                )


    def test_contract_parser_accepts_examples_and_rejects_invalid_contracts(self) -> None:
        for schema_version, example in PUBLIC_CONTRACT_EXAMPLES.items():
            with self.subTest(schema_version=schema_version):
                self.assertEqual(
                    parse_public_contract(example),
                    example,
                )

        invalid = public_contract_example("work_packet_v1")
        invalid.pop("assignment_id")
        with self.assertRaisesRegex(ValueError, "assignment_id"):
            parse_public_contract(invalid)

        invalid_nested = public_contract_example("work_packet_v1")
        invalid_nested["lease"]["lease_epoch"] = "1"
        with self.assertRaisesRegex(ValueError, "lease.lease_epoch"):
            parse_public_contract(invalid_nested)

        invalid_bounds = public_contract_example("work_packet_v1")
        invalid_bounds["dependencies"]["total"] = 99
        with self.assertRaisesRegex(ValueError, "dependencies"):
            parse_public_contract(invalid_bounds)

        invalid_evidence = public_contract_example("evidence_bundle_v1")
        invalid_evidence["verification"] = None
        with self.assertRaisesRegex(ValueError, "verification"):
            parse_public_contract(invalid_evidence)

        unclaimed = public_contract_example("work_packet_v1")
        unclaimed["agent_id"] = None
        unclaimed["status"] = "queued"
        unclaimed["lease"] = {
            "owner_agent_id": None,
            "lease_id": None,
            "lease_epoch": 0,
            "heartbeat_at": None,
            "expires_at": None,
        }
        self.assertEqual(parse_public_contract(unclaimed), unclaimed)
        with self.assertRaisesRegex(ValueError, "unknown public schema version"):
            parse_public_contract({"schema_version": "unknown_v1"})
        with self.assertRaisesRegex(ValueError, "mapping"):
            parse_public_contract([])



    def test_legacy_packet_revision_state_is_explicit(self) -> None:
        legacy = public_contract_example("work_packet_v1")
        legacy["input_revision"] = None
        legacy["revision_status"] = "unknown"
        legacy["readiness"] = "unknown_inputs"

        self.assertEqual(parse_public_contract(legacy), legacy)

        inconsistent = public_contract_example("work_packet_v1")
        inconsistent["revision_status"] = "unknown"
        with self.assertRaisesRegex(ValueError, "input_revision"):
            parse_public_contract(inconsistent)

        inconsistent = public_contract_example("work_packet_v1")
        inconsistent["revision_status"] = "stale"
        with self.assertRaisesRegex(ValueError, "readiness"):
            parse_public_contract(inconsistent)

    def test_review_evidence_and_synthesis_details_are_frozen(self) -> None:
        review = public_contract_example("evidence_bundle_v1")
        review["bundle_kind"] = "review_result"
        review["review"] = {
            "producer_assignment_id": "assign_producer",
            "producer_result_revision": "result-v1:producer",
            "findings": {
                "items": [
                    {
                        "severity": "P1",
                        "code": "missing_evidence",
                        "summary": "Required evidence is missing.",
                        "evidence_refs": {
                            "items": ["artifact_record_x"],
                            "limit": 20,
                            "total": 1,
                            "omitted": 0,
                        },
                    }
                ],
                "limit": 20,
                "total": 1,
                "omitted": 0,
            },
            "evidence_inspected": {
                "items": ["artifact_record_x"],
                "limit": 20,
                "total": 1,
                "omitted": 0,
            },
            "validation_performed": {
                "items": ["targeted tests"],
                "limit": 20,
                "total": 1,
                "omitted": 0,
            },
            "verdict": "changes_requested",
        }
        self.assertEqual(parse_public_contract(review, mode="mutation"), review)

        invalid_review = public_contract_example("evidence_bundle_v1")
        invalid_review["bundle_kind"] = "review_result"
        invalid_review["review"] = None
        with self.assertRaisesRegex(ValueError, "review"):
            parse_public_contract(invalid_review, mode="mutation")

        synthesis = public_contract_example("synthesis_packet_v1")
        self.assertEqual(parse_public_contract(synthesis), synthesis)
        invalid_synthesis = public_contract_example("synthesis_packet_v1")
        invalid_synthesis["outcome_summaries"]["items"][0]["confidence"] = "certain"
        with self.assertRaisesRegex(ValueError, "confidence"):
            parse_public_contract(invalid_synthesis)

    def test_mutation_mode_rejects_unknown_fields_recursively(self) -> None:
        evidence = public_contract_example("evidence_bundle_v1")
        evidence["future_projection_field"] = "accepted for read projections"
        self.assertEqual(parse_public_contract(evidence), evidence)
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            parse_public_contract(evidence, mode="mutation")

        nested = public_contract_example("evidence_bundle_v1")
        nested["delivery"]["future_mutation_field"] = "reject"
        with self.assertRaisesRegex(ValueError, "delivery.*unknown fields"):
            parse_public_contract(nested, mode="mutation")

        invalid_attempt = public_contract_example("evidence_bundle_v1")
        invalid_attempt["attempts"]["items"][0]["status"] = "running"
        with self.assertRaisesRegex(ValueError, "attempts.*status"):
            parse_public_contract(invalid_attempt, mode="mutation")

        with self.assertRaisesRegex(ValueError, "not a mutation input"):
            parse_public_contract(
                public_contract_example("work_packet_v1"),
                mode="mutation",
            )

    def test_operation_error_and_cross_field_contracts_are_enforced(self) -> None:
        success = public_contract_example("work_operation_v1")
        self.assertEqual(parse_public_contract(success), success)

        error = public_contract_example("work_operation_v1")
        error.update(
            {
                "ok": False,
                "changed": False,
                "error": {
                    "code": "stale_input",
                    "message": "The packet input revision is stale.",
                    "context": {
                        "assignment_id": "assign_x",
                        "lease_id": "lease_x",
                        "input_revision": "input-v1:abc123",
                        "latest_packet_revision": "packet-v1:def456",
                    },
                    "conflict_files": {
                        "items": ["assignments/assign_x.yaml"],
                        "limit": 20,
                        "total": 1,
                        "omitted": 0,
                    },
                    "dependency_blockers": {
                        "items": [],
                        "limit": 20,
                        "total": 0,
                        "omitted": 0,
                    },
                    "retry_action": {
                        "kind": "reopen_packet",
                        "command": "research-cockpit work open --assignment assign_x",
                        "reason": "Refresh the stale packet.",
                    },
                },
                "required_action": {
                    "kind": "reopen_packet",
                    "command": "research-cockpit work open --assignment assign_x",
                    "reason": "Refresh the stale packet.",
                },
                "verification": {
                    "status": "failed",
                    "additional_verification_required": True,
                    "commands": {
                        "items": ["research-cockpit work open --assignment assign_x"],
                        "limit": 10,
                        "total": 1,
                        "omitted": 0,
                    },
                },
            }
        )
        self.assertEqual(parse_public_contract(error), error)

        missing_error = public_contract_example("work_operation_v1")
        missing_error["ok"] = False
        with self.assertRaisesRegex(ValueError, "error"):
            parse_public_contract(missing_error)

        invalid_action = public_contract_example("work_operation_v1")
        invalid_action["required_action"] = {
            "kind": "reopen_packet",
            "command": None,
            "reason": "Refresh.",
        }
        with self.assertRaisesRegex(ValueError, "required_action.command"):
            parse_public_contract(invalid_action)

        invalid_verification = public_contract_example("work_operation_v1")
        invalid_verification["verification"]["additional_verification_required"] = True
        with self.assertRaisesRegex(ValueError, "verification"):
            parse_public_contract(invalid_verification)

    def test_coordination_assignment_rows_have_a_nested_contract(self) -> None:
        snapshot = public_contract_example("coordination_snapshot_v1")
        snapshot["assignments"] = {
            "items": [
                {
                    "assignment_id": "assign_x",
                    "kind": "experiment",
                    "status": "active",
                    "readiness": "ready",
                    "agent_id": "agent_x",
                    "root_node": "option_x",
                    "review_status": "pending",
                    "lease_state": "active",
                    "packet_revision": "packet-v1:abc123",
                }
            ],
            "limit": 20,
            "total": 1,
            "omitted": 0,
        }
        self.assertEqual(parse_public_contract(snapshot), snapshot)

        snapshot["assignments"]["items"][0].pop("root_node")
        with self.assertRaisesRegex(ValueError, "root_node"):
            parse_public_contract(snapshot)


if __name__ == "__main__":
    unittest.main()
