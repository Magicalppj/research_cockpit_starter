from __future__ import annotations

import re
import shutil
import sys
import unittest
import uuid
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.operation_receipts import normalized_request_hash
from research_cockpit.runtime_ids import generate_runtime_id


class OperationReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"operation_receipts_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_normalized_request_hash_is_order_independent(self) -> None:
        first = normalized_request_hash({"agent_id": "agent_a", "reassign": False})
        second = normalized_request_hash({"reassign": False, "agent_id": "agent_a"})
        changed = normalized_request_hash({"agent_id": "agent_b", "reassign": False})

        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)

    def test_runtime_ids_are_namespaced_unique_and_file_safe(self) -> None:
        ids = {generate_runtime_id("run", scope_hint="assign_x", slug_hint="trial") for _ in range(50)}

        self.assertEqual(len(ids), 50)
        self.assertTrue(all(re.fullmatch(r"run_assign_x_trial_[a-f0-9]{12}", value) for value in ids))


if __name__ == "__main__":
    unittest.main()
