from __future__ import annotations

from research_cockpit.commands._maintenance_role_cli import run_maintenance_role


MAINTENANCE_REPAIR_SCHEMA = {
    "schema_version": "maintenance_action_v1",
    "action": "interaction_log",
    "execute": False,
    "parameters": {"show_diff": True},
}


def main() -> None:
    run_maintenance_role(command="repair", schema=MAINTENANCE_REPAIR_SCHEMA)


if __name__ == "__main__":
    main()
