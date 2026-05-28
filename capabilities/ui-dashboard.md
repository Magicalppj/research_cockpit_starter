# UI Dashboard

Use this capability when working with the Streamlit frontend or React Flow graph component.

## Launch

```sh
research-cockpit build --root research_cockpit
research-cockpit ui --root research_cockpit --server.port 8501
```

Source checkout fallback:

```sh
research-cockpit build --root research_cockpit
python -m streamlit run src/research_cockpit/ui/app.py
```

Set `RESEARCH_COCKPIT_ROOT` if launching Streamlit directly and the current working directory does not contain `research_cockpit/`.

## Graph Component

The graph uses React Flow with Dagre layout. Node clicks return a selected node id to Streamlit and drive the right-side inspector. Temporary dragging is visual only; positions are not written to YAML.

The graph hides `artifact` nodes by default so the first view stays focused on research reasoning. Researchers can show artifact nodes from Graph Controls when they need to inspect supporting materials in the graph.

Use Branch Visibility in the right-side inspector to collapse or expand the selected node's full descendant branch. It only changes UI visibility. Use Reveal Hidden Children when you need to temporarily inspect direct children hidden by default status/depth filters, such as completed experiments.

Use Baseline Lens from Graph Controls to mark the current default baseline, its source node, and the baseline used by the selected node. It is enabled by default for focus and option-workstream views, disabled by default for global graphs, and does not expand the full accepted history into the graph.

React Flow is the default graph renderer. PyVis remains available as a legacy fallback when the React Flow production build is missing.

Use `Refresh` when a background agent has changed YAML. Refresh reruns Streamlit and reloads the current graph data without rebuilding the React bundle.

The UI prefers fresh generated dashboard JSON for fast refresh. If dashboard files are missing, malformed, or older than truth-source YAML/notes/runs/gates, it falls back to live builders and shows a stale-dashboard warning with the recommended `research-cockpit build --root <root>` command. For large or multi-agent roots, keep `research-cockpit build --root <canonical_root> --watch --interval 5 --json` running from the main repository so Refresh usually only reloads generated files.

## Baselines / Accepted

Use the Baselines / Accepted page to review default baselines, accepted options, and accepted decisions without expanding all accepted history into agent context. Baseline rows are scoped per problem and do not reuse the global `current_state.current_option` as every problem's default. The page is read-only in v1: it generates `set-baseline`, `context`, and `node-context` commands for review instead of writing YAML directly.

## Frontend Build

Only rebuild after changing frontend source:

```sh
cd src/research_cockpit/ui/graph_component/frontend
npm install
npm run build
```

Do not commit `node_modules`.
