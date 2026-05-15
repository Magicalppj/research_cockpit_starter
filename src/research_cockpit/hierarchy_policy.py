from typing import Any


def hierarchy_policy(
    *,
    parent_option_id: str | None = None,
    source_experiment_id: str | None = None,
) -> dict[str, Any]:
    option_id = parent_option_id or "<parent_option_id>"
    source_id = source_experiment_id or "<source_experiment_id>"
    return {
        "default_branch_shape": "option -> problem -> option -> experiment/decision",
        "recommended_command": "research-cockpit create-workstream --root <root> --file workstream.yaml --dry-run --json --show-diff",
        "use_nested_workstream_when": [
            "A worktree result opens more than one follow-up question.",
            "Later experiments should inherit a prior option, decision, or artifact bundle.",
            "Several follow-up experiments would otherwise sit flat under the same option.",
        ],
        "workstream_file_hint": {
            "problem.parent": option_id,
            "problem.derived_from": [source_id],
        },
        "command_created_shape": {
            "active_option.parent": "<created_problem_id>",
            "experiments.parent": "<created_active_option_id>",
        },
        "single_gate_exception": (
            "Use create-followup-experiment only for one small queued gate; it records derived_from "
            "but intentionally keeps the new experiment under the same option."
        ),
        "avoid": (
            "Do not use experiment -> experiment as the primary hierarchy. Keep experiments under options "
            "and record derivation with derived_from."
        ),
        "source_experiment_id": source_experiment_id,
    }
