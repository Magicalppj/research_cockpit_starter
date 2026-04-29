# UI Dashboard

Use this capability when working with the Streamlit frontend or React Flow graph component.

## Launch

```powershell
research-cockpit build --root research_cockpit
research-cockpit ui --root research_cockpit --server.port 8501
```

Source checkout fallback:

```powershell
python scripts\build_dashboard.py --root research_cockpit
python -m streamlit run src\research_cockpit\ui\app.py
```

Set `RESEARCH_COCKPIT_ROOT` if launching Streamlit directly and the current working directory does not contain `research_cockpit/`.

## Graph Component

The graph uses React Flow with Dagre layout. Node clicks return a selected node id to Streamlit and drive the right-side inspector. Temporary dragging is visual only; positions are not written to YAML.

Use `刷新图谱 / Refresh` when a background agent has changed YAML. Refresh reruns Streamlit and reloads the current graph data without rebuilding the React bundle.

## Frontend Build

Only rebuild after changing frontend source:

```powershell
cd src\research_cockpit\ui\graph_component\frontend
npm.cmd install
npm.cmd run build
```

Do not commit `node_modules`.
