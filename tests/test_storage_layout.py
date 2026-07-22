from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import unittest
import uuid


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from research_cockpit.storage import save_yaml
from research_cockpit.storage_layout import _classify_path_syntax, resolve_storage_layout


class StorageLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT_DIR / ".test_tmp"
        parent.mkdir(exist_ok=True)
        self.tmp_root = parent / f"storage_layout_{uuid.uuid4().hex}"
        self.state_root = self.tmp_root / "state"
        self.state_root.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_unconfigured_layout_keeps_legacy_reads_but_disables_managed_writes(self) -> None:
        legacy_root = self.state_root / "artifacts"
        legacy_root.mkdir()

        layout = resolve_storage_layout(self.state_root, environ={})

        self.assertIsNone(layout.managed_artifact_root)
        self.assertEqual(layout.legacy_artifact_root, legacy_root.resolve())
        self.assertFalse(layout.managed_writes_enabled)
        with self.assertRaisesRegex(ValueError, "managed artifact root is not configured"):
            layout.require_managed_artifact_root()
        self.assertEqual(
            layout.policy_payload(),
            {
                "schema_version": "storage_policy_v1",
                "default_evidence_mode": "reference",
                "managed_artifact_root": None,
                "managed_writes_enabled": False,
                "legacy_artifact_root": str(legacy_root.resolve()),
                "legacy_new_writes_allowed": False,
                "source": "unconfigured",
                "project_id": None,
            },
        )

    def test_profile_resolves_external_managed_and_quarantine_roots(self) -> None:
        save_yaml(
            self.state_root / "storage.yaml",
            {
                "schema_version": "storage_layout_v1",
                "project_id": "project_x",
                "artifact_root": "../managed-artifacts",
            },
        )

        layout = resolve_storage_layout(self.state_root, environ={})

        expected = (self.tmp_root / "managed-artifacts").resolve()
        self.assertEqual(layout.managed_artifact_root, expected)
        self.assertEqual(layout.quarantine_root, expected / ".quarantine")
        self.assertEqual(layout.require_managed_artifact_root(), expected)
        self.assertEqual(layout.source, "profile")
        self.assertEqual(layout.project_id, "project_x")

    def test_explicit_root_overrides_environment_and_profile(self) -> None:
        profile_root = self.tmp_root / "profile"
        env_root = self.tmp_root / "environment"
        explicit_root = self.tmp_root / "explicit"
        save_yaml(
            self.state_root / "storage.yaml",
            {
                "schema_version": "storage_layout_v1",
                "artifact_root": str(profile_root),
            },
        )

        layout = resolve_storage_layout(
            self.state_root,
            explicit_artifact_root=explicit_root,
            environ={"RESEARCH_COCKPIT_ARTIFACT_ROOT": str(env_root)},
        )

        self.assertEqual(layout.managed_artifact_root, explicit_root.resolve())
        self.assertEqual(layout.source, "explicit")

    def test_environment_root_overrides_profile(self) -> None:
        profile_root = self.tmp_root / "profile"
        env_root = self.tmp_root / "environment"
        save_yaml(
            self.state_root / "storage.yaml",
            {
                "schema_version": "storage_layout_v1",
                "artifact_root": str(profile_root),
            },
        )

        layout = resolve_storage_layout(
            self.state_root,
            environ={"RESEARCH_COCKPIT_ARTIFACT_ROOT": str(env_root)},
        )

        self.assertEqual(layout.managed_artifact_root, env_root.resolve())
        self.assertEqual(layout.source, "environment")

    def test_managed_root_must_not_overlap_state_root(self) -> None:
        invalid_roots = (
            self.state_root / "artifacts-v2",
            self.state_root.parent,
        )
        for artifact_root in invalid_roots:
            with self.subTest(artifact_root=artifact_root), self.assertRaisesRegex(
                ValueError,
                "must not overlap state root",
            ):
                resolve_storage_layout(
                    self.state_root,
                    explicit_artifact_root=artifact_root,
                    environ={},
                )

    def test_invalid_profile_is_rejected(self) -> None:
        invalid_profiles = (
            {"schema_version": "storage_layout_v2", "artifact_root": "../managed"},
            {"schema_version": "storage_layout_v1", "artifact_root": ["../managed"]},
            {
                "schema_version": "storage_layout_v1",
                "artifact_root": "../managed",
                "unexpected": True,
            },
        )
        for index, profile in enumerate(invalid_profiles):
            with self.subTest(index=index):
                save_yaml(self.state_root / "storage.yaml", profile)
                with self.assertRaises(ValueError):
                    resolve_storage_layout(self.state_root, environ={})

    def test_default_environment_uses_process_environment(self) -> None:
        artifact_root = self.tmp_root / "from-process-env"
        previous = os.environ.get("RESEARCH_COCKPIT_ARTIFACT_ROOT")
        os.environ["RESEARCH_COCKPIT_ARTIFACT_ROOT"] = str(artifact_root)
        try:
            layout = resolve_storage_layout(self.state_root)
        finally:
            if previous is None:
                os.environ.pop("RESEARCH_COCKPIT_ARTIFACT_ROOT", None)
            else:
                os.environ["RESEARCH_COCKPIT_ARTIFACT_ROOT"] = previous

        self.assertEqual(layout.managed_artifact_root, artifact_root.resolve())


    def test_path_syntax_classification_is_platform_independent(self) -> None:
        self.assertEqual(_classify_path_syntax(r"C:\managed\artifacts"), "windows_absolute")
        self.assertEqual(_classify_path_syntax(r"C:managed"), "windows_drive_relative")
        self.assertEqual(
            _classify_path_syntax(r"\\server\share\artifacts"),
            "windows_absolute",
        )
        self.assertEqual(_classify_path_syntax("/var/lib/research-artifacts"), "posix_absolute")
        self.assertEqual(_classify_path_syntax("../managed-artifacts"), "relative")

    def test_foreign_absolute_path_syntax_is_rejected(self) -> None:
        foreign = "/var/lib/research-artifacts" if os.name == "nt" else r"C:\managed\artifacts"
        with self.assertRaisesRegex(ValueError, "foreign path syntax"):
            resolve_storage_layout(
                self.state_root,
                explicit_artifact_root=foreign,
                environ={},
            )

    def test_windows_drive_relative_managed_root_is_always_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "drive-relative"):
            resolve_storage_layout(
                self.state_root,
                explicit_artifact_root=r"C:managed",
                environ={},
            )


if __name__ == "__main__":
    unittest.main()
