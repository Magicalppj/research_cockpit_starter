# Research Cockpit Starter

A repo-native, Python-based research dashboard for graph-structured research planning.

It is designed for remote GPU servers and can be launched with Streamlit.

## Quick Start

```bash
cd research_cockpit_starter
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python scripts/build_dashboard.py
streamlit run ui/app.py --server.address 0.0.0.0 --server.port 8501
```

Then open the forwarded port in your browser.

Example SSH forwarding:

```bash
ssh -L 8501:localhost:8501 user@remote-gpu-server
```

## Core Idea

- YAML nodes are the source of truth.
- Markdown notes contain detailed reasoning.
- Python scripts build dashboard JSON/Markdown.
- Streamlit + PyVis renders an interactive graph.
- Agents read `research_cockpit/dashboards/agent_context_pack.json` first.

## Directory Layout

```text
research_cockpit/
  current_state.yaml
  graph/
    nodes/*.yaml
  notes/
  dashboards/
cockpit/
  model.py
scripts/
  build_dashboard.py
  add_node.py
ui/
  app.py
```
