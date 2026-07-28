# ADR-0005: Use Leases, Idempotent Operations, And A Short Global Commit Sequencer

## Status

Accepted

## Date

2026-07-19

## Context

Multiple agents need to read, compute, and run experiments concurrently against one canonical Research Cockpit data root. Concurrent writes must prevent duplicate claims, stale owners, repeated closeouts, lost interaction events, and partial multi-file state.

The existing runtime already uses a root mutation lock, stale-write checks, rollback, append-only interaction segments, and a separate validation-index lock. Replacing this immediately with per-node locks would add ordering, deadlock, recovery, and cross-platform filesystem complexity before lock contention has been measured.

Retries also need a durable result. A timeout cannot tell an agent whether a mutation committed, and one receipt file per operation would accelerate repository file growth.

## Decision

Use assignment leases, persistent operation ids, and the existing short global commit sequencer.

### Lease

- Default lease duration is 900 seconds.
- Default heartbeat cadence is 300 seconds.
- A claim atomically updates assignment owner, lease id, lease epoch, expiry, and agent active-assignment references.
- Successful assignment-scoped mutations renew the lease.
- Long-running launchers use a runtime heartbeat hook outside model turns.
- Explicit `work renew` is a recovery/diagnostic operation, not part of the normal worker recipe.
- Lease expiry marks work reclaimable for coordinator review; it does not terminate processes, delete worktrees, or automatically reassign an assignment with an active run.
- Reassignment increments the lease epoch. Mutations from an old owner or epoch are rejected.

### Idempotency

- Every mutating role-facade operation requires an `operation_id`.
- The uniqueness scope is the assignment for worker/reviewer operations and the portfolio/root for coordinator/maintainer operations.
- The normalized request payload is hashed.
- The mutation's append-only interaction event stores `operation_id`, scope, request hash, and the bounded success receipt in the same transactional commit.
- A derived operation index maps scope and operation id to the event/receipt for fast lookup.
- If the index is stale or missing, recovery scans interaction segments newest-first; the index never becomes truth.
- An exact replay returns the stored receipt without another mutation.
- A reused id with a different request hash returns `idempotency_conflict`.
- No per-operation receipt file is created.

### Commit Ordering

- Read, compute, payload staging, schema validation, and transaction planning run outside the root mutation lock.
- The root lock protects dependency/lease recheck, stale signature checks, atomic truth writes, and interaction-event append.
- The lock is released before the derived validation/operation index is patched under its own lock.
- Dashboard build, full validation, and smoke never run while the mutation lock is held.
- Lock striping requires a separate ADR backed by 8/16-agent wait/hold measurements.

## Alternatives Considered

### Per-Node Or Per-File Locks

This could increase parallel commit throughput but introduces lock ordering and multi-file transaction recovery complexity. Rejected until profiling proves the short global commit sequencer is the bottleneck.

### Store Receipts In One Growing YAML Map

Lookup would be simple, but every operation would rewrite a growing shared file and create a new contention point. Rejected.

### One Receipt File Per Operation

This avoids map rewrites but causes unbounded file growth. Rejected.

### In-Memory Deduplication

CLI processes do not share memory and retries may happen after process exit. Rejected.

## Consequences

- Interaction-event schema remains additive and must preserve old events.
- Operation lookup needs a derived index and a correct stale-index fallback.
- Facade receipts must remain bounded so storing them in events does not inflate segments excessively.
- Concurrency tests must cover one-winner claim, exact replay, payload mismatch, stale owner, disjoint closeout, same-target conflict, and index recovery.
- Background heartbeat cost is measured separately and never appears in model-visible workflow output.

## Implementation Status (2026-07-28)

Successful assignment mutations renew leases, and the lease domain exposes a heartbeat operation. The bundled launcher templates do not currently invoke that hook, and writing `progress.json` does not renew a lease. Until an integration wires the hook, long mutation-free runs must choose a sufficient `work_start_v1.lease_seconds` or have an external runtime explicitly arrange `work renew`; this status note narrows the accepted design claim to shipped behavior.
