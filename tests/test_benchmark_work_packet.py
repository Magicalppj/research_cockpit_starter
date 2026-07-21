from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.storage import save_yaml


class WorkPacketBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.root = parent / f"work_packet_benchmark_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True)
        save_yaml(
            self.root / "assignments" / "assign_legacy.yaml",
            {
                "assignment_id": "assign_legacy",
                "agent_id": "agent_legacy",
                "status": "active",
                "root_node": "option_x",
                "current_node": "experiment_x",
                "allowed_subtree": {
                    "root": "option_x",
                    "policy": "descendants_only",
                },
                "objective": "Continue the bounded legacy experiment.",
            },
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_runtime_benchmark_measures_work_packet_and_unchanged_poll(self) -> None:
        out = subprocess.run(
            [
                sys.executable,
                str(ROOT_DIR / "dev" / "scripts" / "benchmark_runtime.py"),
                "--root",
                str(self.root),
                "--cold-runs",
                "1",
                "--warm-runs",
                "2",
                "--operation",
                "work_packet",
                "--operation",
                "work_packet_unchanged",
                "--assignment",
                "assign_legacy",
                "--json",
            ],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        payload = json.loads(out.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["assignment_id"], "assign_legacy")
        self.assertEqual(
            payload["operations"],
            ["work_packet", "work_packet_unchanged"],
        )
        by_operation = {item["operation"]: item for item in payload["results"]}
        self.assertLess(
            by_operation["work_packet"]["warm_summary"]["wall_time_ms"]["p95"],
            2000,
        )
        self.assertLess(
            by_operation["work_packet"]["warm_summary"]["stdout_bytes"]["max"],
            8 * 1024,
        )
        self.assertLess(
            by_operation["work_packet_unchanged"]["warm_summary"]["stdout_bytes"]["max"],
            512,
        )


if __name__ == "__main__":
    unittest.main()
