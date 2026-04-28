# Skill Layout Reorganization v1

Date: 2026-04-28

This batch separates repository materials into clearer boundaries:

- `skills/research-cockpit/`: installable/referenceable Codex skill package with `SKILL.md`, `agents/openai.yaml`, and skill references.
- `dev/`: development status, design notes, historical requirements, v2 specs, and planning examples.
- Repository root runtime: `cockpit/`, `scripts/`, `ui/`, `research_cockpit/`, and `requirements.txt`.

`scripts/agent_bootstrap.py --json` now reports `skill.path=skills/research-cockpit`, so a future parent agent can pass that folder to a subagent for skill invocation testing.

Next candidates:

- Add a `package_skill.py` export helper that copies the skill package and selected runtime files into a temporary release folder.
- Add forward-test prompts for subagent skill validation.
- Evaluate whether demo cockpit data should move to `examples/` once CLI default-root behavior is configurable.
