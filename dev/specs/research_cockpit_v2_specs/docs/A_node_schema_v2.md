# Research Cockpit Node Schema v2

This schema upgrades the cockpit from a generic project graph into a research-oriented decision graph.

Core goals:

1. Human can instantly see current research focus.
2. Agent can load only the focused context and still act correctly.
3. Every branch has status, evidence, blockers, next actions, and decision history.
4. Solved / rejected branches remain searchable but do not clutter the default focus view.

---

## 1. Core Concepts

The graph has six first-class node types:

```text
Stage -> Problem -> Option -> Experiment -> Decision
                       -> Artifact
```

Recommended node types:

| Type | Meaning | Example |
|---|---|---|
| `stage` | high-level research phase | Text Encoder Redesign |
| `problem` | active research question or bottleneck | Event-level text control is weak |
| `option` | candidate solution branch | FLAN-T5-XL + CLAP |
| `experiment` | empirical validation of an option | exp_042_flan_t5_clap |
| `decision` | ADR-style accepted/proposed conclusion | Adopt FLAN-T5 + CLAP |
| `artifact` | dataset, checkpoint, figure, manifest, code branch | Audio Edit Dataset v2_150 |

---

## 2. Required Node Fields

Every node must contain:

```yaml
id: string
type: stage | problem | option | experiment | decision | artifact
title: string
status: string
summary: string
```

Recommended additional fields:

```yaml
priority: low | medium | high | critical
parent: string | null
children: list[string]
tags: list[string]
created_at: YYYY-MM-DD
updated_at: YYYY-MM-DD
owner: self | agent | external | none
```

---

## 3. Status Vocabulary

### Stage

```yaml
planned
active
blocked
done
```

### Problem

```yaml
open
active
blocked
resolved
parked
```

### Option

```yaml
open
active
promising
accepted
rejected
paused
parked
```

### Experiment

```yaml
planned
queued
running
done
failed
cancelled
```

### Decision

```yaml
proposed
accepted
superseded
rejected
```

### Artifact

```yaml
draft
active
deprecated
archived
```

---

## 4. Research-Specific Fields

### 4.1 Focus Fields

These fields control focus mode, graph filtering, and agent context.

```yaml
focus_priority: 0-100
focus_role: current | parent | sibling | child | historical | unrelated
show_in_focus: true | false | auto
focus_depth_hint: 0 | 1 | 2 | 3
```

Usage:

- `focus_priority`: higher means more important to show in focus view.
- `focus_role`: computed automatically when possible.
- `show_in_focus`: manually force show/hide.
- `focus_depth_hint`: overrides default neighbor expansion.

---

### 4.2 Evidence Fields

Options and decisions need evidence metadata.

```yaml
evidence_strength: none | weak | medium | strong
evidence_summary: string
supporting_experiments: list[string]
contradicting_experiments: list[string]
supporting_decisions: list[string]
```

Recommended interpretation:

| Evidence Strength | Meaning |
|---|---|
| `none` | hypothesis only |
| `weak` | one small experiment or anecdotal result |
| `medium` | one or more relevant experiments, not fully conclusive |
| `strong` | repeated or decisive evidence |
| `rejected` | evidence argues against the option |

---

### 4.3 Problem Fields

Problem node recommended fields:

```yaml
question: string
impact: low | medium | high | critical
blocking: true | false
current_best_option: string | null
resolved_by: string | null
blockers: list[string]
next_actions: list[string]
success_criteria: list[string]
```

Example:

```yaml
id: problem_event_text_weak
type: problem
title: Event-level text control is weak
status: active
priority: critical
question: Why does the model ignore fine-grained old/new event text?
impact: critical
blocking: true
current_best_option: option_flan_t5_clap
success_criteria:
  - Replace event text changes the generated audio in the target region.
  - Remove old_text reduces CLAP similarity to old_text.
  - Keep regions remain stable.
```

---

### 4.4 Option Fields

Option node recommended fields:

```yaml
hypothesis: string
decision_state: open | promising | accepted | rejected | paused
pros: list[string]
cons: list[string]
implementation_steps: list[string]
expected_outcome: string
failure_modes: list[string]
rejection_reason: string | null
```

Example:

```yaml
id: option_flan_t5_clap
type: option
title: FLAN-T5-XL token branch + CLAP anchor
status: active
decision_state: promising
evidence_strength: weak
hypothesis: >
  FLAN-T5 improves token-level event understanding, while CLAP improves
  audio-semantic alignment for old/new edits.
implementation_steps:
  - Generate FLAN-T5-XL timeline feature cache.
  - Add CLAP anchor projection to EventStateInitializer.
  - Run FLAN-T5-only and FLAN-T5+CLAP ablations.
```

---

### 4.5 Experiment Fields

Experiment node recommended fields:

```yaml
dataset: string
backbone: string
code_branch: string | null
config_path: string | null
run_id: string | null
metrics: list[string]
result_summary: string | null
outcome: positive | negative | mixed | inconclusive | null
linked_artifacts: list[string]
started_at: YYYY-MM-DD | null
finished_at: YYYY-MM-DD | null
```

Example:

```yaml
id: exp_042_flan_t5_clap
type: experiment
title: FLAN-T5-XL + CLAP event branch
status: planned
parent: option_flan_t5_clap
dataset: dataset_v2_150
backbone: ltx23_audio_branch
metrics:
  - replace_following
  - remove_success
  - overlap_preserve
  - local_semantic_contrast
success_condition:
  - Beats Gemma baseline on replace_following.
  - Does not degrade keep-region preserve score.
```

---

### 4.6 Decision Fields

Decision node recommended fields:

```yaml
decision_status: proposed | accepted | superseded | rejected
derived_from: list[string]
supporting_experiments: list[string]
alternatives_considered: list[string]
consequences: list[string]
next_required_actions: list[string]
```

Example:

```yaml
id: decision_flan_t5_clap
type: decision
title: Adopt FLAN-T5-XL + CLAP for event branch
status: proposed
decision_status: proposed
derived_from:
  - option_flan_t5_clap
supporting_experiments:
  - exp_041_flan_t5_only
  - exp_042_flan_t5_clap
alternatives_considered:
  - option_gemma_baseline
  - option_umt5_clap
consequences:
  - Regenerate timeline feature cache.
  - Modify Semantic Ribbon initializer.
  - Update edit-program inference builder.
```

---

### 4.7 Artifact Fields

Artifact node recommended fields:

```yaml
artifact_type: dataset | checkpoint | figure | manifest | code | paper | note
version: string
path: string | null
links: dict
health: valid | stale | missing | unknown
```

Example:

```yaml
id: dataset_v2_150
type: artifact
artifact_type: dataset
title: Audio Edit Dataset v2_150
status: active
health: valid
summary: 150 editable classes, 7.2k atoms, 20k+ edit pairs, 10s scenes.
links:
  spec: datasets/v2_150.yaml
  figure: figures/dataset_pipeline_v2_150.png
```

---

## 5. Edge Schema

Edges should be explicit. Store either in node fields or in `graph/edges.yaml`.

Recommended edge fields:

```yaml
from: string
to: string
type: contains | candidate_for | validates | supports | contradicts | resolves | depends_on | blocks | supersedes | produces
label: string | null
strength: weak | medium | strong | null
```

Recommended edge semantics:

| Edge Type | Meaning |
|---|---|
| `contains` | stage contains problem |
| `candidate_for` | option is candidate solution for problem |
| `validates` | experiment validates option |
| `supports` | experiment supports decision |
| `contradicts` | experiment contradicts option |
| `resolves` | decision resolves problem |
| `depends_on` | node depends on artifact / task |
| `blocks` | node blocks another node |
| `supersedes` | decision or option replaces old one |
| `produces` | experiment produces artifact |

---

## 6. Current State Schema

`research_cockpit/current_state.yaml` should be the source of truth for the active work context.

```yaml
current_stage: stage_text_encoder
current_problem: problem_event_text_weak
current_option: option_flan_t5_clap
current_focus_node: problem_event_text_weak
current_focus_path:
  - stage_text_encoder
  - problem_event_text_weak
  - option_flan_t5_clap

focus_mode:
  default_depth: 2
  hide_statuses:
    - rejected
    - parked
    - archived
  show_resolved: false
  show_rejected: false
  show_parked: false

current_hypothesis: >
  Event-level old/new text control can be improved by using FLAN-T5-XL token
  features plus CLAP audio-semantic anchors in the Semantic Ribbon branch.

open_risks:
  - Need train/inference parity for FLAN-T5 + CLAP timeline features.

next_actions:
  - Regenerate timeline feature cache with FLAN-T5-XL token features.
```

---

## 7. Agent Context Fields

Each node may include an optional `agent_context` field:

```yaml
agent_context:
  include: true
  role: focus | background | evidence | implementation | blocker
  key_files:
    - path/to/file.py
  key_questions:
    - What must be decided?
  next_action_hint: string
```

This allows the agent context builder to include only the minimal relevant nodes.

---

## 8. Recommended Default UI Mapping

### Node Shape = Type

| Type | Shape |
|---|---|
| stage | large rounded rectangle |
| problem | diamond |
| option | rounded rectangle |
| experiment | ellipse |
| decision | hexagon |
| artifact | database/card |

### Fill Color = Status

| Status | Color |
|---|---|
| active/running | warm yellow |
| planned/open | light blue |
| promising | light green |
| accepted/done/resolved | green |
| rejected/failed | soft red |
| parked/paused | gray |
| proposed | light purple |

### Border = Focus

| Border | Meaning |
|---|---|
| thick border | current focus path |
| double border | selected node |
| glow / highlight | current focus node |
| dashed border | historical / hidden when collapsed |

---

## 9. Implementation Notes

- Keep YAML nodes small.
- Put long reasoning into Markdown notes.
- Generate graph JSON and agent context JSON automatically.
- Never let the agent silently modify accepted decisions.
- Every rejected branch should have `rejection_reason`.
- Every accepted decision should have `consequences`.
