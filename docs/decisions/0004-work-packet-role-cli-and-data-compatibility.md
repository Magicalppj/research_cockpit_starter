# ADR-0004: Adopt Work Packets And A One-Version Role CLI

## Status

Accepted

## Date

2026-07-19

## Context

Research Cockpit 0.2.x exposes 70 public commands and a broad root skill. The runtime already supports assignment scope, compact context, transactional closeout, targeted validation, append-only interaction events, and record-first artifacts, but downstream agents still need to choose among overlapping low-level operations.

The next architecture uses Work Packets as bounded agent tasks and role-oriented facades for worker, reviewer, coordinator, and maintainer workflows. Maintaining aliases and compatibility metadata for every replaced command would preserve the largest source of interface complexity.

Existing downstream repositories may contain long-lived 0.2.x project data and artifact payloads. Losing or forcing a full rewrite of that state is not acceptable.

## Decision

Adopt a one-version public CLI and additive persistent-data compatibility.

- `work open` is the canonical worker read operation.
- Worker operations are `open`, `claim`, `renew`, `start`, `record`, and `close`.
- Reviewer operations are `open` and `report`.
- Coordinator operations are `overview`, `assign`, `review`, `decide`, and `handoff`.
- Maintenance and diagnostic capabilities keep one canonical route each.
- Once a role facade covers an old top-level command, the old public command name is removed from parser, help, manifest, examples, and agent documentation in the same release.
- No alias, deprecated-warning route, or hidden compatibility parser is retained for superseded CLI names.
- Facades call shared Python domain functions directly; they never chain legacy Research Cockpit subprocesses.
- The breaking public package version is `0.3.0`.

The new runtime must directly read and continue writing 0.2.x persisted state:

- graph nodes, edges, views, coordinator/current state;
- agents and assignments without new Work Packet fields;
- runs and gate result YAML/JSON;
- `artifact_records_v1`, payload directories, and artifact manifests;
- legacy YAML interaction prefixes and segmented JSONL interaction events.

Compatibility means:

- missing new fields receive documented derived defaults;
- unknown legacy fields survive read-modify-write;
- artifact payload bytes and relative provenance links are not relocated by ordinary mutations;
- ordinary reads and writes do not require an eager migration;
- explicit migrations are dry-run-first, audited, and do not fabricate lease, review, or revision truth.

## Alternatives Considered

### Keep All Old Commands As Aliases

This would reduce immediate migration work but preserve a large parser, manifest, documentation, and testing surface. It also makes it easy for downstream agents to keep selecting inefficient workflows. Rejected.

### Remove Old Data Support Together With Old Commands

This would simplify loaders but make existing research roots unusable and risk losing evidence provenance. Rejected.

### Maintain Two Public CLI Versions

This would duplicate command routing and documentation while leaving agents to choose between versions. Rejected under the one-version rule.

## Consequences

- CLI automation must migrate at the `0.3.0` cutover.
- Release notes must include an old-to-new intent table.
- Tests focus on the current public CLI and legacy-data round trips, not legacy command behavior.
- Assignment and artifact writers must preserve unknown fields.
- Internal old command modules may temporarily remain as implementation code, but are not public routes.
- Removal occurs only after the replacement facade and legacy-data tests pass in the same branch.
