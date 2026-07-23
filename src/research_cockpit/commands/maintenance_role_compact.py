from __future__ import annotations

from research_cockpit.commands._maintenance_role_cli import run_maintenance_role


MAINTENANCE_COMPACT_SCHEMA = {
    "schema_version": "maintenance_action_v1",
    "action": "artifact_gc",
    "execute": False,
    "parameters": {
        "record_id": "record_managed",
        "operation_id": "gc-record-managed-001",
        "phase": "quarantine",
        "expected_revision": "root-v1:<revision-from-dry-run>",
        "purge_after_seconds": 86400,
    },
}


def main() -> None:
    run_maintenance_role(command="compact", schema=MAINTENANCE_COMPACT_SCHEMA)


if __name__ == "__main__":
    main()
