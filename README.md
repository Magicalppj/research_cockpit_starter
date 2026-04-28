# Research Cockpit Skill Workspace

This repository is a development workspace for the packageable Research Cockpit skill.

## Package Boundary

Copy or publish this directory when distributing the skill:

```text
skills/research-cockpit/
```

It contains the skill metadata, agent rules, Python runtime code, workflow scripts, Streamlit UI, sample cockpit data, generated context packs, and dependency list.

## Development Material

Development-only material stays outside the package:

```text
dev/
  docs/
  specs/
  tests/
```

Run development tests from the repository root:

```powershell
python -m unittest discover -s dev\tests
```

Run skill commands from the skill package root:

```powershell
cd skills\research-cockpit
python scripts\skill_smoke_test.py --json
python scripts\agent_bootstrap.py --json
python scripts\validate_cockpit.py
python scripts\build_dashboard.py
```
