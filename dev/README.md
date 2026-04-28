# Development Workspace

This directory keeps project-development material out of the packageable skill.

- `docs/`: development status, design notes, and historical requirements.
- `specs/`: planning specs, schemas, and example/test YAML used during feature design.
- `tests/`: development verification suite.
- `scripts/`: development-only checks and release harnesses.

The reusable Codex skill package lives in `skills/research-cockpit/` and includes its runtime code, scripts, UI, sample cockpit data, generated context, and agent-facing Markdown.

## Release Check

Run the full pre-release skill harness from the repository root:

```powershell
python dev\scripts\run_skill_release_check.py --json
```

For a faster read-only pass:

```powershell
python dev\scripts\run_skill_release_check.py --json --skip-mutating
```

The release check verifies package shape, public-path hygiene, read-only agent startup, package portability, isolated mutating workflow, and the decision acceptance quality gate. Mutating checks operate only on a temporary copied package under `.test_tmp/`.
