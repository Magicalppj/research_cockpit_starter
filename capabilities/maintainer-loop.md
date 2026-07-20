# Maintainer Loop

Load this playbook only for migration, repair, retention, release packaging, or repository hygiene. Maintainer authority is root-scoped and must not be inferred by workers or reviewers.

## Inspect Before Mutation

Use the narrow audit that matches the task. Prefer dry-run for repair, migration, compaction, and worktree cleanup. Do not run dashboard build or full smoke merely because structured truth changed.

Query one missing contract with:

```sh
research-cockpit commands --role maintainer --name <command> --json --compact
```

Do not request broad discovery at startup. Read `capabilities/maintenance.md` only after the audit identifies a specific retention, worktree, interaction-log, or migration procedure.

## Invariants

- Preserve legacy structured data, unknown fields, artifact payload bytes, provenance refs, and append-only history.
- Never delete payloads as part of metadata compaction.
- Run artifact compaction as dry-run, then execute one eligible artifact at a time.
- Keep generated dashboards and validation indexes rebuildable; do not treat them as truth.
- Verify resolved paths before recursive worktree or temporary-fixture cleanup.
- A repair or migration receipt must identify changed files and recovery actions.

Full validate/build/smoke runs once only for release or `milestone_handoff`. Ordinary maintenance audits and dry-runs do not trigger that gate.
