# Development Workspace

This directory keeps project-development material out of the packageable skill.

- `docs/`: development status, design notes, and historical requirements.
- `specs/`: planning specs, schemas, and example/test YAML used during feature design.
- `tests/`: development verification suite.
- `scripts/`: development-only checks and release harnesses.

The reusable Research Cockpit plugin boundary is the repository root. Runtime code lives in `src/research_cockpit/`; current agent-facing guidance lives in `README.md`, `SKILL.md`, `AGENTS.md`, and `capabilities/`. Files under `dev/docs/` and `dev/specs/` are development evidence or historical design records unless they explicitly state otherwise.

## Test Profiles

Use one profile from the repository root:

```sh
python dev/scripts/run_test_profile.py fast --json --compact --progress
python dev/scripts/run_test_profile.py precommit --json --compact --progress
python dev/scripts/run_test_profile.py full --json --compact --progress
```

Do not run all three serially. `precommit` includes the fast test targets and adds facade integration plus the read-only release gate. `full` runs discovery across all tests and then the complete release gate. Add affected tests omitted from a bounded profile with repeatable `--extra-test <module_or_test_id>`.

List the machine-readable profile contract without running tests:

```sh
python dev/scripts/run_test_profile.py --list --json --compact
```

The duration targets are observational, not pass/fail gates, so slower supported platforms are not rejected. See `docs/testing-strategy.md` for selection and maintenance rules.

## Release Check

Run the full pre-release skill harness from the repository root:

```sh
python dev/scripts/run_skill_release_check.py --json
```

For a faster read-only pass:

```sh
python dev/scripts/run_skill_release_check.py --json --skip-mutating
```

The release check verifies package shape, public-path hygiene, read-only agent startup, package portability, isolated mutating workflow, and the decision acceptance quality gate. Mutating checks operate only on a temporary copied package under `.test_tmp/`.

The profile runner invokes these commands itself for `precommit` and `full`; do not append a second standalone release check to those profiles.

## Subagent Forward Check

Run the multi-agent workflow harness from the repository root:

```sh
python dev/scripts/run_subagent_forward_check.py --json
```

For a read-only plus portability pass:

```sh
python dev/scripts/run_subagent_forward_check.py --json --skip-mutating
```

This check simulates read-only context understanding, option workstream execution, retrieval branch expansion, decision checklist completion, and portable plugin startup. Mutating tracks copy the packageable repository content into `.test_tmp/subagent_runs/` and assert the original plugin root is unchanged.

The manual two-subagent cases behind this harness are recorded in `docs/subagent_forward_test_cases.md`.
