# Research Cockpit UI Interaction Spec v2

This document defines the interactive UI behavior for the graph-centered Research Cockpit.

The goal is to make the graph useful for daily research navigation, not just decorative visualization.

---

## 1. Main Design Principle

Default view should answer:

```text
Where am I now?
What problem am I solving?
Which solution branch is active?
What evidence exists?
What must I do next?
```

Therefore the UI should default to **Focus Mode**, not global graph mode.

---

## 2. Page Structure

Recommended pages:

1. Dashboard
2. Research Graph
3. Branch Comparison
4. Decision Trace
5. Experiment Matrix
6. Agent Context

---

## 3. Dashboard Page

### Purpose

Provide a one-screen status overview.

### Required Sections

#### Current Focus Card

Shows:

```text
Current Stage
Current Problem
Current Option
Current Hypothesis
```

#### Blocking Problems

List active/blocking problems sorted by:

1. blocking = true
2. priority
3. focus_priority

#### Next Actions

Checklist-style list generated from `current_state.yaml` plus focused nodes.

#### Recent Decisions

Accepted/proposed decisions updated recently.

#### Dataset / Model Health

Compact cards:

```text
Dataset v2_150: valid
Text Encoder: FLAN-T5 + CLAP planned
Main Backbone: LTX 2.3 audio branch
```

---

## 4. Research Graph Page

### 4.1 Default Focus Mode

When opening the page:

1. Read `current_state.yaml`.
2. Set `current_focus_node`.
3. Build subgraph around the current focus node.
4. Hide unrelated resolved/rejected/parked nodes.
5. Center and zoom to current focus node.

### 4.2 Focus Subgraph Rules

Default visible nodes:

- current focus node
- nodes in `current_focus_path`
- parent stage
- active option
- sibling options under the same problem
- child experiments under active option
- decisions directly linked to current problem/option
- blockers directly linked to current node

Default hidden nodes:

- rejected options not directly adjacent
- parked branches
- resolved problems outside focus path
- experiments from unrelated stages
- deprecated artifacts

### 4.3 Focus Depth

UI should expose:

```text
Depth: 1 / 2 / Global
```

Depth semantics:

- Depth 1: parent, children, siblings
- Depth 2: plus decisions, experiments, dependencies
- Global: all visible nodes after filter

### 4.4 Filter Controls

Recommended filters:

- Node type
- Status
- Priority
- Stage
- Show resolved
- Show rejected
- Show parked
- Show artifacts
- Show dependencies
- Show blockers

Default:

```yaml
show_resolved: false
show_rejected: false
show_parked: false
depth: 2
```

---

## 5. Graph Visual Encoding

### 5.1 Shape = Node Type

| Type | Shape |
|---|---|
| Stage | large rounded rectangle |
| Problem | diamond |
| Option | rounded rectangle |
| Experiment | ellipse |
| Decision | hexagon |
| Artifact | document/database card |

### 5.2 Color = Status

| Status | Color |
|---|---|
| active / running | warm yellow |
| open / planned | light blue |
| promising | light green |
| accepted / done / resolved | green |
| rejected / failed | soft red |
| parked / paused | gray |
| proposed | light purple |

### 5.3 Border = Focus Relation

| Style | Meaning |
|---|---|
| thick border | node is on current focus path |
| glow | current focus node |
| double border | selected node |
| dashed border | historical or collapsed node |

### 5.4 Badge Rules

Optional badges:

| Badge | Meaning |
|---|---|
| `!` | high/critical priority |
| `B2` | two blockers |
| `E3` | three supporting experiments |
| `✓` | accepted/done |
| `?` | insufficient evidence |
| `A` | active branch |

---

## 6. Legend

Add a collapsible legend panel.

Legend must include:

1. Node type legend
2. Status color legend
3. Focus border legend
4. Edge type legend

Position recommendation:

- bottom-left or top-right of graph canvas
- collapsed by default on small screens
- expanded by default on large monitor

---

## 7. Right Detail Panel

When a node is selected, the right panel should show four tabs.

### Tab 1: Summary

Fields:

- title
- type
- status
- priority
- summary
- current conclusion
- tags

### Tab 2: Evidence

Fields:

- supporting experiments
- contradicting experiments
- linked decisions
- linked artifacts
- evidence strength
- result summary

### Tab 3: Actions

Fields:

- blockers
- next actions
- unresolved questions
- implementation steps
- success criteria

Action buttons:

- Set as current focus
- Mark as done / resolved
- Create child option
- Create experiment
- Create decision
- Open linked note

### Tab 4: Agent Context

Fields:

- local context bundle preview
- focus path
- nearby nodes
- knowledge index
- key files
- next action hint

Buttons:

- Export focus context
- Copy context JSON
- Rebuild dashboard

---

## 8. Human-Agent Focus Link

### 8.1 Set Focus Behavior

When user clicks `Set as current focus`:

1. Update `current_state.yaml`
2. Update `current_focus_node`
3. Recompute `current_focus_path`
4. Generate:
   - `dashboards/focus_context_pack.json`
   - `dashboards/agent_context_pack.json`
   - `dashboards/graph_view.json`
5. Re-render graph with new focus.

### 8.2 Focus Context Pack

`focus_context_pack.json` should contain:

```json
{
  "focus_node": {},
  "focus_path": [],
  "overview": {},
  "local_neighbors": {
    "parents": [],
    "children": [],
    "siblings": [],
    "experiments": [],
    "decisions": [],
    "artifacts": [],
    "blockers": []
  },
  "current_best_option": "",
  "blockers": [],
  "next_actions": [],
  "knowledge_index": []
}
```

Agent should read:

1. `agent_context_pack.json`
2. `focus_context_pack.json`
3. only the linked notes/files in `knowledge_index`

---

## 9. Branch Comparison View

### Purpose

Compare candidate solutions under one problem.

### Input

A problem node.

### Display

Columns:

```text
Option | Status | Evidence Strength | Pros | Cons | Experiments | Conclusion
```

Graph layout:

```text
Problem
  -> Option A
  -> Option B
  -> Option C
```

Each option node displays:

- current status
- evidence strength
- experiment count
- rejection reason if rejected

Useful for deciding between:

- Gemma baseline
- FLAN-T5 only
- FLAN-T5 + CLAP
- UMT5 + CLAP

---

## 10. Decision Trace View

### Purpose

Show why a decision was made.

### Input

A decision node.

### Display

```text
Problem -> Candidate Options -> Experiments -> Decision -> Consequences
```

Right panel should show:

- context
- alternatives considered
- supporting experiments
- consequences
- superseded decisions

This view is useful for paper writing and weekly reviews.

---

## 11. Experiment Matrix Page

Table fields:

```text
Exp ID
Title
Status
Parent Option
Dataset
Backbone
Text Encoder
Metrics
Outcome
Result Summary
Run Link
```

Filters:

- status
- dataset
- backbone
- parent option
- outcome

Recommended row colors:

- running: yellow
- done positive: green
- done negative: red
- planned: blue
- failed: red

---

## 12. Agent Context Page

Displays:

- global overview
- current focus path
- active problems
- active options
- open risks
- next actions
- recent decisions
- knowledge index

Should also include:

```text
Copy to clipboard
Export JSON
Export Markdown
```

---

## 13. Keyboard / Navigation Shortcuts

Optional but useful:

| Shortcut | Action |
|---|---|
| `f` | focus selected node |
| `g` | global view |
| `1` | depth 1 |
| `2` | depth 2 |
| `r` | toggle resolved |
| `p` | toggle parked |
| `b` | show blockers |
| `/` | search nodes |

---

## 14. Suggested Layout

```text
+-------------------------------------------------------------+
| Header: Audio Edit Research Cockpit                         |
+------------------+--------------------------+---------------+
| Sidebar          | Main Graph Canvas        | Detail Panel  |
|                  |                          |               |
| Current Focus    | Focused graph            | Summary       |
| Filters          | Legend                   | Evidence      |
| View Mode        | Breadcrumb               | Actions       |
| Health           |                          | Agent Context |
+------------------+--------------------------+---------------+
```

Sidebar should show:

- language toggle
- current focus
- dataset health
- graph filters
- view mode selector

Main graph should show:

- focused subgraph
- breadcrumb
- legend
- minimap if available

Right panel should show:

- selected node details
- actions
- agent context

---

## 15. MVP Implementation Priority

### P0

1. Focus Mode
2. Current node auto-centering / high brightness
3. Legend
4. Right panel with Summary / Evidence / Actions / Agent Context
5. Set as current focus button
6. `focus_context_pack.json`

### P1

1. Branch Comparison View
2. Decision Trace View
3. Edge types and styled edges
4. Breadcrumb navigation
5. Blocker highlighting

### P2

1. MLflow sync
2. DVC sync
3. Git branch / commit sync
4. Node search
5. Saved filter presets

---

## 16. Success Criteria

The UI is successful if:

1. Opening the page shows the current problem without manual filtering.
2. The user can identify the current active branch in under 10 seconds.
3. The user can see blockers and next actions without opening raw YAML.
4. The agent can read the generated focus context and perform a task without asking for global background.
5. Rejected/resolved branches are preserved but do not clutter the default view.
