# Repository Layout Reference

Use this reference when the task involves packaging, exporting, or validating the skill layout.

## Export Boundary

The packageable skill is the whole `skills/research-cockpit/` directory. Copy this directory as-is when publishing or installing the skill elsewhere.

The development repository keeps non-package material outside the skill:

- `dev/docs/`: status logs, historical requirements, and design notes.
- `dev/specs/`: planning specs, schemas, and example YAML used during feature design.
- `dev/tests/`: development verification suite.

## Skill Package Contents

- `SKILL.md`: Codex skill entry.
- `AGENTS.md`: rules for coding agents operating inside the skill package.
- `agents/openai.yaml`: UI metadata for skill lists.
- `references/`: optional context loaded by agents as needed.
- `cockpit/`: model and data helpers.
- `scripts/`: CLI workflow entry points.
- `scripts/skill_smoke_test.py`: read-only package smoke test for agent invocation.
- `ui/`: Streamlit frontend.
- `research_cockpit/`: YAML truth source and generated dashboard context.
- `requirements.txt`: Python dependencies.
- `README.md`: user-facing skill usage guide.

Run workflow commands from the skill package root. If an agent cannot reliably set cwd, use absolute script paths; scripts derive the package root from their own location.
