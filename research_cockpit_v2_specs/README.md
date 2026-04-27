# Research Cockpit v2 Schema + UI Spec

This package contains two implementation-ready specifications:

1. `docs/A_node_schema_v2.md`
   - upgraded graph/node schema
   - focus fields
   - evidence fields
   - agent context fields
   - edge schema
   - example node definitions

2. `docs/B_ui_interaction_spec.md`
   - Focus Mode
   - graph visual encoding
   - right detail panel tabs
   - human-agent focus synchronization
   - Branch Comparison View
   - Decision Trace View
   - MVP implementation priorities

It also includes example YAML files under `examples/`.

Recommended next step:
- Merge the schema into your existing `research_cockpit/graph/nodes/*.yaml`.
- Add `focus_context_pack.json` generation.
- Update the UI graph page to default to Focus Mode.
