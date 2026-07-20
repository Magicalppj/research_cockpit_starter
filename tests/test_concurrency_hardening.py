from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import sys
import time
import unittest
import uuid


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))
sys.path.insert(0, str(ROOT_DIR / "dev" / "scripts"))

from multi_agent_baseline import benchmark_concurrency
from research_cockpit.mutation_lock import MutationError, mutation_lock
from research_cockpit.storage import save_yaml


class MutationLockHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"lock_hardening_{uuid.uuid4().hex}"
        (self.root / "graph").mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_stale_lock_owned_by_dead_process_is_recovered(self) -> None:
        lock_path = self.root / "graph" / ".mutation.lock"
        lock_path.write_text(
            "pid: 2147483647\ncreated_at: 0\n",
            encoding="utf-8",
        )

        with mutation_lock(
            self.root,
            timeout_seconds=0.5,
            stale_after_seconds=0.0,
        ):
            self.assertTrue(lock_path.exists())

        self.assertFalse(lock_path.exists())

    def test_malformed_stale_lock_is_not_removed_without_owner_evidence(self) -> None:
        lock_path = self.root / "graph" / ".mutation.lock"
        lock_path.write_text("not owner metadata\n", encoding="utf-8")

        with self.assertRaises(MutationError):
            with mutation_lock(
                self.root,
                timeout_seconds=0.01,
                stale_after_seconds=0.0,
            ):
                self.fail("malformed lock metadata must not be guessed stale")

        self.assertTrue(lock_path.exists())

    def test_timeout_receipt_can_be_recovered_by_retry_after_release(self) -> None:
        with mutation_lock(self.root):
            with self.assertRaises(MutationError) as caught:
                with mutation_lock(self.root, timeout_seconds=0.01):
                    self.fail("nested lock must time out")

        acquired = False
        with mutation_lock(self.root, timeout_seconds=0.5):
            acquired = True

        self.assertTrue(acquired)
        self.assertEqual(caught.exception.payload["recovery"], "retry_same_operation")
        self.assertTrue(caught.exception.payload["retryable"])

    def test_waiters_make_bounded_progress_without_fifo_guarantee(self) -> None:
        worker_count = 8

        def enter(index: int) -> int:
            with mutation_lock(self.root, timeout_seconds=5.0):
                time.sleep(0.005)
                return index

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            completed = list(executor.map(enter, range(worker_count)))

        self.assertEqual(sorted(completed), list(range(worker_count)))


@unittest.skipUnless(
    os.environ.get("RESEARCH_COCKPIT_RUN_CONCURRENCY_STRESS") == "1",
    "set RESEARCH_COCKPIT_RUN_CONCURRENCY_STRESS=1 for the opt-in 8/16-agent benchmark",
)
class MultiAgentStressTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"concurrency_stress_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)
        save_yaml(self.root / "current_state.yaml", {})
        for index in range(16):
            node_id = f"experiment_stress_{index:02d}"
            save_yaml(
                self.root / "graph" / "nodes" / f"{node_id}.yaml",
                {
                    "id": node_id,
                    "type": "experiment",
                    "title": f"Stress experiment {index}",
                    "status": "planned",
                },
            )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_disjoint_and_same_target_8_16_agent_rounds_are_consistent(self) -> None:
        payload = benchmark_concurrency(
            self.root,
            agent_counts=(8, 16),
            scenarios=("disjoint", "same_target"),
            temp_parent=ROOT_DIR / ".test_tmp",
        )

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(len(payload["rounds"]), 4)
        for row in payload["rounds"]:
            with self.subTest(
                scenario=row["scenario"],
                agent_count=row["agent_count"],
            ):
                self.assertTrue(row["consistency"]["ok"], row)
                self.assertEqual(
                    row["consistency"]["events"]["missing_sample_ids"],
                    [],
                )
                self.assertEqual(
                    row["consistency"]["index"]["mismatched_node_ids"],
                    [],
                )


if __name__ == "__main__":
    unittest.main()
