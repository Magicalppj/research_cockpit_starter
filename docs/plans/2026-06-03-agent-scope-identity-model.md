# Spec: Agent Scope Identity Model

## Status

Implemented historical plan, superseded operationally by the 0.3.0 role facade. Command examples below are not executable on the current CLI. Current operational guidance: `AGENTS.md`, `SKILL.md`, `capabilities/graph-state.md`, and `docs/migrations/0.3.0-cli-cutover.md`.

## Date

2026-06-03

## Objective

Research Cockpit should stop treating a single global `current_state` focus as the default source of truth for every worker agent. In multi-agent work, each downstream agent should operate inside an explicit assignment scope, with a system-generated identity and a local cursor. Global coordinator state should remain useful for humans and dashboards, but must not drive ordinary agent behavior.

Success means:

- A downstream agent can start without inventing or guessing its own `agent_id`.
- Multiple agents can work concurrently without duplicate identity records.
- Agent context defaults to the assigned branch, not global coordinator focus.
- Mutating commands reject out-of-scope writes unless a coordinator override is explicit.
- Dashboard and context output clearly separate assignment-local work from coordinator/global state.

## Assumptions

- This is allowed to break old `current_state.yaml` semantics.
- Node lifecycle status such as `active`, `done`, `accepted`, and `resolved` remains on graph nodes.
- Agent identity is assigned by the canonical Research Cockpit root, not by the model or downstream agent.
- Git worktrees remain code/experiment isolation only. Research state still writes to the canonical root.
- No authentication system exists in v1; "coordinator override" is a command-mode and workflow boundary, not a security boundary.

## Core Model

The new model separates four concepts:

- `node.status`: whether a research node is alive, terminal, or blocked.
- `agent`: the system-generated identity of a worker process/session.
- `assignment`: a scoped unit of work owned by one agent.
- `coordinator_state`: human/coordinator dashboard state and global planning notes.

There is no single global focus for normal agent work. Each assignment has its own `current_node` cursor.

## Data Model

### Agents

Store generated identities under:

```text
research_cockpit/agents/<agent_id>.yaml
```

Example:

```yaml
agent_id: agent_20260603_8f4c2a_cache_probe
label: cache_probe
display_name: Cache probe
status: active
created_at: 2026-06-03T10:00:00Z
last_seen_at: 2026-06-03T10:00:00Z
active_assignment_ids:
  - assign_20260603_b17e2a
```

Rules:

- `agent_id` is immutable and unique.
- `label` is human-readable only and is never a primary key.
- Agents can be `active`, `idle`, `completed`, or `retired`.
- Agent files are created only by Research Cockpit commands while holding the mutation lock.

### Assignments

Store scoped work records under:

```text
research_cockpit/assignments/<assignment_id>.yaml
```

Example:

```yaml
assignment_id: assign_20260603_b17e2a
agent_id: agent_20260603_8f4c2a_cache_probe
status: active
root_node: option_cache_probe
current_node: experiment_cache_smoke
allowed_subtree:
  root: option_cache_probe
  policy: descendants_only
objective: Run cache probe experiments under option_cache_probe.
next_actions:
  - Review smoke gate metrics.
worktree:
  branch: agent/option_cache_probe
  label: agent_cache_probe
created_at: 2026-06-03T10:00:00Z
updated_at: 2026-06-03T10:30:00Z
```

Rules:

- `assignment_id` is immutable and unique.
- `root_node` defines the branch boundary.
- `current_node` must be inside `allowed_subtree`.
- New nodes created by the assignment must have a parent inside `allowed_subtree`, unless a coordinator override is explicit.
- Assignment statuses are `queued`, `active`, `blocked`, `completed`, `cancelled`, and `retired`.

### Coordinator State

Replace global task focus with coordinator-only state:

```text
research_cockpit/coordinator_state.yaml
```

Example:

```yaml
selected_node: problem_overview
selected_assignment: assign_20260603_b17e2a
global_next_actions:
  - Decide whether to merge cache probe results.
dashboard_filters:
  hide_statuses:
    - parked
    - rejected
    - cancelled
```

Rules:

- `selected_node` is UI/coordinator selection only.
- `global_next_actions` are not agent-local tasks.
- Ordinary agent bootstrap may show coordinator state, but must mark it as `coordinator_only`.

## Identity Generation

`start-agent-session` becomes the canonical factory for agent identity and assignment identity.

Recommended command:

```sh
research-cockpit start-agent-session --root research_cockpit --option option_cache_probe --label cache_probe --objective "Run cache probe experiments" --branch agent/option_cache_probe --worktree ../worktrees/agent_cache_probe --json
```

The command generates IDs such as:

```text
agent_20260603_8f4c2a_cache_probe
assign_20260603_b17e2a
```

Generation algorithm:

1. Normalize optional `--label` into a short slug.
2. Generate a random 6-8 hex token.
3. Compose `agent_<date>_<token>_<slug>` and `assign_<date>_<token>`.
4. Acquire the mutation lock.
5. Check that target `agents/*.yaml` and `assignments/*.yaml` files do not exist.
6. If a collision exists, retry with a new token.
7. Write the agent, assignment, and option claim as one atomic mutation.

Do not let downstream agents choose their own primary `agent_id`.

## Handoff And Startup

`start-agent-session` returns identity and launch context:

```json
{
  "agent_id": "agent_20260603_8f4c2a_cache_probe",
  "assignment_id": "assign_20260603_b17e2a",
  "startup_command": "research-cockpit bootstrap --json",
  "launch_env": {
    "RESEARCH_COCKPIT_ROOT": "D:/main_repo/research_cockpit",
    "RESEARCH_COCKPIT_AGENT_ID": "agent_20260603_8f4c2a_cache_probe",
    "RESEARCH_COCKPIT_ASSIGNMENT_ID": "assign_20260603_b17e2a"
  }
}
```

Identity resolution priority:

1. Explicit `--assignment <assignment_id>`.
2. Explicit `--agent <agent_id>` when it has exactly one active assignment.
3. `RESEARCH_COCKPIT_ASSIGNMENT_ID`.
4. `RESEARCH_COCKPIT_AGENT_ID` when it has exactly one active assignment.
5. A local session file such as `.research_cockpit_session.yaml`.

If multiple active assignments exist and no identity can be resolved, bootstrap fails:

```json
{
  "ok": false,
  "error": "assignment_identity_required",
  "message": "Multiple active assignments exist. Pass --assignment or set RESEARCH_COCKPIT_ASSIGNMENT_ID."
}
```

## CLI Changes

### New Or Changed Commands

```sh
research-cockpit start-agent-session --root <root> --option <option_id> --label <label> --objective "..." --json
research-cockpit bootstrap --root <root> --assignment <assignment_id> --json
research-cockpit agent-session-context --root <root> --assignment <assignment_id> --compact --json
research-cockpit assignment-view --root <root> --json
research-cockpit set-cursor --root <root> --assignment <assignment_id> --node <node_id> --no-build
```

Assignment completion remains a future lifecycle workflow. In the current design, close graph work with existing branch and experiment lifecycle commands, and move worker-local progress with `set-cursor`.

### Replaced Or Removed Semantics

- Replace `set-agent-focus` with `set-cursor`.
- Replace `agent_focuses` in `current_state.yaml` with `assignments/*.yaml`.
- Replace global `current_focus_node` with `coordinator_state.selected_node`.
- Remove `sync-focus-actions` for worker agents. Use assignment-local `next_actions`.
- Keep `set-focus` only as a coordinator/UI command if retained at all; rename to `select-node` if possible.

### Scope Enforcement For Mutations

Mutating commands should accept scope through explicit flags or environment:

```sh
research-cockpit complete-experiment --assignment assign_20260603_b17e2a --id experiment_cache_smoke --finding "..." --confidence medium --no-build
research-cockpit create-workstream --assignment assign_20260603_b17e2a --file workstream.yaml --dry-run --json --show-diff
```

Before writing, commands check:

- Target node is inside assignment `allowed_subtree`.
- Created node parent is inside assignment `allowed_subtree`.
- Artifact/finding/gate/run links point to nodes inside the assignment scope.
- Assignment status allows mutation.

Out-of-scope writes fail before file changes:

```json
{
  "ok": false,
  "error": "node_out_of_assignment_scope",
  "assignment_id": "assign_20260603_b17e2a",
  "node_id": "experiment_other_branch",
  "allowed_root": "option_cache_probe"
}
```

Coordinator override must be explicit:

```sh
research-cockpit complete-experiment --coordinator --id experiment_other_branch ...
```

## Context Output Contract

Scoped bootstrap output should lead with assignment-local state:

```json
{
  "scope": {
    "mode": "assignment",
    "primary_context": "assignment_scope",
    "assignment_id": "assign_20260603_b17e2a",
    "agent_id": "agent_20260603_8f4c2a_cache_probe"
  },
  "assignment_scope": {
    "root_node": "option_cache_probe",
    "current_node": "experiment_cache_smoke",
    "next_actions": [],
    "option_context": {}
  },
  "coordinator_state": {
    "coordinator_only": true,
    "selected_node": "problem_overview"
  }
}
```

Generated dashboards may aggregate all assignments, but agent-facing context must not place coordinator selected node above assignment scope.

## Validation Rules

`validate` should fail when:

- Duplicate `agent_id` or `assignment_id` is found.
- Assignment references a missing agent.
- Assignment references a missing `root_node` or `current_node`.
- `current_node` is outside `allowed_subtree`.
- Active assignment root is terminal unless explicitly allowed.
- Two active assignments claim the same root without `allow_parallel_assignments: true`.
- Coordinator state references missing nodes or assignments.

`lint --semantic` should warn when:

- An active assignment has no active/open downstream work.
- An assignment cursor points to a terminal node.
- Coordinator selected node is terminal but still has global next actions.
- An agent has no active assignments but is still `active`.

## Testing Strategy

Use `python -m unittest tests.test_model tests.test_scripts` for core and CLI behavior.

Required regression tests:

- `start-agent-session` generates unique agent and assignment IDs under lock.
- Collision retry works when a generated file already exists.
- Bootstrap with no identity fails when multiple active assignments exist.
- Bootstrap resolves identity from `RESEARCH_COCKPIT_ASSIGNMENT_ID`.
- Worker mutation inside assignment scope succeeds.
- Worker mutation outside assignment scope fails without writing.
- Coordinator override can write outside assignment scope.
- Dashboard/context output marks coordinator state as `coordinator_only`.
- Command discovery exposes assignment-aware flags.
- Documentation examples match CLI help and manifest.

## Boundaries

Always:

- Generate IDs in Research Cockpit commands, not in downstream agents.
- Hold the mutation lock before writing agent or assignment records.
- Treat assignment scope as the default worker boundary.
- Keep coordinator state separate from assignment state.
- Return machine-readable errors for identity and scope failures.

Ask first:

- Whether to allow multiple active assignments under the same root.
- Whether to keep a compatibility migration from `current_state.yaml`.
- Whether to rename retained coordinator UI commands from `set-focus` to `select-node`.
- Whether coordinator override needs stronger authentication outside the CLI workflow.

Never:

- Let agents infer identity from model name, OS username, process id, or worktree path.
- Use human-readable labels as primary keys.
- Let ordinary worker bootstrap silently fall back to coordinator/global focus.
- Let worker commands write outside assignment scope by default.
- Store machine-local absolute worktree paths in long-lived YAML truth source.

## Implementation Plan

### Phase 1: Model And Validation

- Add `agents/*.yaml`, `assignments/*.yaml`, and `coordinator_state.yaml` loaders.
- Add model validation for identity uniqueness and assignment scope.
- Add tests for valid/invalid records.

Verify:

```sh
python -m unittest tests.test_model
```

### Phase 2: Identity Factory

- Redesign `start-agent-session` to generate `agent_id` and `assignment_id`.
- Return `launch_env`, `startup_command`, and optional session file content.
- Remove worker-facing requirement to pass hand-authored `--agent`.

Verify:

```sh
python -m unittest tests.test_scripts.ScriptBehaviorTests -k start_agent_session
```

### Phase 3: Scoped Bootstrap

- Make `bootstrap` resolve assignment identity from flags, environment, or session file.
- Fail with `assignment_identity_required` when multiple active assignments exist and no identity is available.
- Put `assignment_scope` before `coordinator_state` in JSON output.

Verify:

```sh
python -m unittest tests.test_scripts.ScriptBehaviorTests -k agent_bootstrap
```

### Phase 4: Scope Enforcement

- Add shared scope guard helpers.
- Apply them to common mutating commands: `complete-experiment`, `record-finding`, `create-workstream`, run/gate/artifact ingest, and status updates.
- Keep coordinator override explicit.

Verify:

```sh
python -m unittest tests.test_scripts
```

### Phase 5: Dashboard And Context Builders

- Remove global focus from agent primary context.
- Add assignment overview/dashboard read models.
- Show coordinator selected node as dashboard/UI state only.

Verify:

```sh
python -m unittest discover -s tests -t .
```

### Phase 6: Documentation And Release Checks

- Update `SKILL.md`, `capabilities/focus-context.md`, `capabilities/experiment-tracking.md`, and command discovery tests.
- Add explicit startup examples for generated handoff env.
- Document the breaking schema change.

Verify:

```sh
python dev/scripts/run_skill_release_check.py --json --skip-mutating
git diff --check
```

## Task Breakdown

- [x] Task 1: Add agent/assignment/coordinator data model.
  - Acceptance: `validate` accepts correct records and rejects duplicate/missing references.
  - Verify: `python -m unittest tests.test_model`
  - Files: `src/research_cockpit/model.py`, new model helpers, `tests/test_model.py`

- [x] Task 2: Generate identities in `start-agent-session`.
  - Acceptance: command returns unique `agent_id`, `assignment_id`, and launch env; no downstream self-naming required.
  - Verify: targeted `start_agent_session` tests.
  - Files: `src/research_cockpit/commands/start_agent_session.py`, `src/research_cockpit/agent_sessions.py`, `tests/test_scripts.py`

- [x] Task 3: Add assignment-scoped bootstrap.
  - Acceptance: `bootstrap --json` resolves env/session identity; ambiguous multi-assignment bootstrap fails.
  - Verify: targeted `agent_bootstrap` tests.
  - Files: `src/research_cockpit/commands/agent_bootstrap.py`, `tests/test_scripts.py`

- [x] Task 4: Implement shared scope guard.
  - Acceptance: out-of-scope mutation fails before writes with structured JSON.
  - Verify: targeted mutation tests.
  - Files: new scope guard helper, selected command modules, `tests/test_scripts.py`

- [x] Task 5: Replace focus/cursor semantics.
  - Acceptance: worker cursor lives in assignment record; coordinator selection lives in `coordinator_state.yaml`.
  - Verify: context and dashboard tests.
  - Files: focus commands, context builders, dashboard builders, tests.

- [x] Task 6: Update command discovery and docs.
  - Acceptance: command manifest and docs consistently tell downstream agents to use assignment scope.
  - Verify: documented flags/manifest tests and release check.
  - Files: `SKILL.md`, `capabilities/*.md`, `list_agent_commands.py`, tests.

## Open Questions

- Should multiple active assignments be allowed under one option when they target different child experiments?
- Should `--agent` continue to exist as a lookup convenience, or should worker commands use only `--assignment`?
- Should a local `.research_cockpit_session.yaml` be written into worktrees, or should env-only handoff be preferred?
- Should the implementation provide a one-shot migration from `current_state.yaml`, or intentionally require fresh assignment setup?
- Should coordinator override be a single `--coordinator` flag or a named `--scope coordinator` mode?
