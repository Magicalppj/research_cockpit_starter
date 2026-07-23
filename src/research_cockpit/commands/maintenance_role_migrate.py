from __future__ import annotations

from research_cockpit.commands._maintenance_role_cli import run_maintenance_role


MAINTENANCE_MIGRATE_SCHEMA = {
    "schema_version": "maintenance_action_v1",
    "action": "artifact_storage",
    "execute": False,
    "parameters": {
        "record_id": "record_legacy",
        "operation_id": "migrate-record-legacy-001",
    },
}


def main() -> None:
    run_maintenance_role(command="migrate", schema=MAINTENANCE_MIGRATE_SCHEMA)


if __name__ == "__main__":
    main()
