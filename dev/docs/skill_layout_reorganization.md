# Skill Layout Reorganization v2

> **Historical record:** this file preserves design or implementation context and is not current operational guidance. Current operational guidance: repository-root `README.md`, `SKILL.md`, `AGENTS.md`, `capabilities/`, and `docs/internal-architecture.md`.


Date: 2026-04-28

This batch makes `skills/research-cockpit/` the complete package boundary. Copying that single directory is enough to publish or install the skill elsewhere.

Package contents:

- `SKILL.md`, `AGENTS.md`, `README.md`, `agents/openai.yaml`, and `references/`
- `cockpit/`, `scripts/`, and `ui/`
- `research_cockpit/` sample/default cockpit state and generated context
- `requirements.txt`

Development-only contents:

- `dev/docs/`: status logs, design notes, and historical requirements
- `dev/specs/`: planning specs and schema examples
- `dev/tests/`: repository verification tests

`scripts/agent_bootstrap.py --json` now reports `skill.path=.` when run from the package root. In the development workspace, parent agents should pass `skills/research-cockpit/` to subagents for skill invocation testing.

Next candidates:

- Add a `package_skill.py` sanity checker that validates the package can run after being copied to a temporary folder.
- Add forward-test prompts for subagent skill validation.
- Decide whether `research_cockpit/` should remain bundled demo state or be split into `examples/` plus an initialization script.
