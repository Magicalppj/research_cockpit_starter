---
name: research-cockpit
description: Route worker, reviewer, coordinator, and maintainer agents to bounded Research Cockpit workflows.
---

# Research Cockpit Role Router

Research Cockpit stores structured research state under one project-local `research_cockpit/` data root. This file selects one role playbook; it is not a command sequence.

## Resolve The Boundary

Use one canonical data root. Prefer an explicit absolute `--root <data-root>` when cwd is unreliable. Project state belongs in the caller repository, never in the plugin directory. Set `RESEARCH_COCKPIT_ROOT` and, for assigned work, `RESEARCH_COCKPIT_ASSIGNMENT_ID` when repeated flags are inconvenient.

Structured truth lives in nodes, assignments, runs, gates, artifact records, coordinator state, and append-only interaction events. Markdown notes and artifact payloads are supporting evidence. Generated dashboards are rebuildable views, not startup context or truth.

## Select One Role

- Assigned worker: read `capabilities/worker-loop.md`.
- Explicit review assignment: read `capabilities/reviewer-loop.md`.
- Portfolio decomposition, assignment, decision, or handoff: read `capabilities/coordinator-loop.md`.
- Migration, repair, retention, release packaging, or repository hygiene: read `capabilities/maintainer-loop.md`.

Do not load other role playbooks by default. A worker or reviewer does not become coordinator merely because global context is available.

## Startup Rule

An assigned role opens exactly one bounded packet; do not substitute one role's packet command for the other.

Worker:

```sh
research-cockpit work open --root <data-root> --assignment <assignment_id> --json --compact
```

Reviewer:

```sh
research-cockpit review open --root <data-root> --assignment <review_assignment_id> --json --compact
```

Use coordinator bootstrap only when the target is unknown and the task is global triage. Known-node work without an assignment uses bounded `context --view execution`. Do not chain packet, context, bootstrap, dashboard packs, and broad search for the same startup.

## Shared Invariants

- Assignment scope is the worker/reviewer concurrency boundary; coordinator state owns global/UI selection.
- Use CLI mutations for supported structured writes and pass `--assignment` for assignment-scoped work.
- Mutating role facades require an operation id. Reuse it only for an exact retry; a changed request requires a new id.
- Successful assignment mutations renew the lease. Explicit `work renew` is recovery-only; launcher heartbeat stays outside model-visible turns.
- Same-root truth commits remain serialized; long computation and experiments happen outside the commit lock.
- Never execute suggested actions automatically or set a decision directly to accepted.
- Final run output can be staged by `work close`; standalone artifact records are for earlier durability, and graph promotion requires a durable navigation reason.
- `graph/interaction_log.yaml` becomes an immutable legacy prefix after the event manifest exists.
- A role-facade receipt with `verification.status: internally_verified` and `additional_verification_required: false` needs no validation, reread, build, or smoke tail; legacy receipts may use `verified: true`.
- Full validate/build/smoke runs only at `milestone_handoff`, not after an ordinary agent turn.

## Progressive Discovery

Role playbooks contain the default path. When one operation is missing, query that command only:

```sh
research-cockpit commands --role <role> --name <command> --json --compact
```

Do not request broad command discovery during normal startup. Use deeper capability files only for a condition named by the selected playbook. Use `docs/internal-architecture.md` for implementation work, not routine operation.

## Compatibility

The CLI may remove old public command names during the one-version cutover. Existing nodes, assignments, runs, gates, artifact records/manifests, interaction history, unknown legacy fields, artifact payload bytes, and provenance refs remain readable and writable.

If the console script is unavailable, use the same interpreter with `python -m research_cockpit.cli`. Markdown is UTF-8; never persist machine-specific absolute paths.