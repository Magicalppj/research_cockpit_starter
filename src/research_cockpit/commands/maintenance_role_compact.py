from __future__ import annotations

from research_cockpit.commands._maintenance_role_cli import run_maintenance_role


MAINTENANCE_COMPACT_SCHEMA = {
    "schema_version": "maintenance_action_v1",
    "action": "artifact",
    "execute": False,
    "parameters": {"artifact_id": None, "show_diff": False},
}


def main() -> None:
    run_maintenance_role(command="compact", schema=MAINTENANCE_COMPACT_SCHEMA)


if __name__ == "__main__":
    main()
