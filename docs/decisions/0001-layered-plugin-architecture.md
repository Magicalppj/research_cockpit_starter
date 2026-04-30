# ADR-0001: Layer Research Cockpit Plugin Internals

## Status

Accepted

## Date

2026-04-30

## Context

Research Cockpit started with much of the data model, graph helpers, context builders, sidecar state, and domain logic in `research_cockpit.model`. That made early iteration fast, but it increased maintenance cost:

- `model.py` grew too large for focused review.
- Search, onboarding, UI, and commands depended on private helpers from one broad module.
- Domain areas such as decisions and option workstreams were harder to evolve independently.
- Future agents had to load too much context to understand a narrow change.

The plugin also needs to remain safe for public use. Runtime code belongs in the plugin package, while project-specific research state belongs in the caller repository's `research_cockpit/` data root.

## Decision

Adopt a layered internal architecture:

- `types.py`, `storage.py`, and `paths.py` own low-level contracts and IO.
- `graph_core.py`, `resources.py`, `graph_views.py`, and `interaction_log.py` own graph and sidecar state helpers.
- `decisions.py`, `option_workstreams.py`, `suggestions.py`, `search_index.py`, `node_onboarding.py`, and `context_packs.py` own domain and read-model logic.
- `commands/` owns CLI argument parsing, validation flow, and controlled writes.
- `ui/` owns Streamlit rendering and frontend component integration.
- `model.py` remains as a compatibility facade for existing imports, but new code should import from focused modules directly.

The public `research-cockpit` CLI remains the stable integration surface. YAML/JSON data contracts are unchanged by this refactor.

## Alternatives Considered

### Keep `model.py` As The Central Module

- Pros: fewer files and fewer import decisions.
- Cons: continued growth would make reviews slower and increase accidental coupling.
- Rejected because the plugin is now intended for external use and long-term maintenance.

### Remove `model.py` Immediately

- Pros: cleaner module boundary with no compatibility facade.
- Cons: high migration risk for tests, downstream users, and release branch synchronization.
- Rejected for this phase. A facade keeps compatibility while allowing new code to follow the new boundaries.

### Split By Command Instead Of Domain

- Pros: each CLI command could own its logic end to end.
- Cons: repeated validation, graph traversal, evidence, and suggestion logic would drift across commands.
- Rejected because agents and UI need the same read models as CLI commands.

## Consequences

- New contributors should consult `docs/internal-architecture.md` before placing new code.
- `model.py` should shrink over time and should not receive new large domain logic.
- Commands should use shared runtime helpers for common validated-state and mutation-finalization flows.
- Release branches can keep the same public CLI and data contracts while internal modules evolve.
- Tests should cover behavior rather than module placement so refactors remain low risk.
