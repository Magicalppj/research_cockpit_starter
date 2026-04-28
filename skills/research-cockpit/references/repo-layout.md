# Repository Layout Reference

Use this reference when the task involves packaging, exporting, or validating the skill layout.

## Export Boundary

- `skills/research-cockpit/`: skill package entry, UI metadata, and skill references.
- `AGENTS.md`: repository-level coding-agent rules, not part of the installable skill package.
- `dev/`: development status logs, historical requirements, design notes, and v2 planning specs.
- `tests/`: repository verification suite.

## Runtime Boundary

The skill currently operates against runtime files in the repository root:

- `cockpit/`: model and data helpers.
- `scripts/`: CLI workflow entry points.
- `ui/`: Streamlit frontend.
- `research_cockpit/`: YAML truth source and generated dashboard context.
- `requirements.txt`: Python dependencies.

Run workflow commands from the repository root unless a future packaged distribution explicitly vendors runtime files into the skill folder.
