from __future__ import annotations


APPLY_GRAPH_PLAN_EXAMPLE = """File schema v1: graph_plan_v1

Supported top-level sections:
- nodes: create new nodes.
- updates: update existing or newly-created nodes.

nodes[*] supported keys:
- id, type, title: required.
- parent, status, summary: optional.
- fields: optional supported node field mapping.

updates[*] supported keys:
- id: required.
- status: optional validated node status. Use accept-decision for decision accepted.
- fields: optional supported node field mapping.

updates[*].fields and nodes[*].fields supported scalar fields:
- title, summary, question, hypothesis, evidence_summary, result_summary
- priority, order, rank
- owner, handoff_context

Supported boolean fields:
- ready_for_agent

Supported list append fields:
- tags, success_criteria, metrics, pros, cons, next_actions
- supporting_experiments, contradicting_experiments, supporting_decisions
- linked_artifacts, alternatives_considered, derived_from
- depends_on, blocked_by

Notes:
- priority is coarse urgency. Use high/medium/low for dispatchable work; existing critical values remain readable.
- order or rank is for stable sequencing, e.g. order: p2.2 or rank: "020".
- owner, ready_for_agent, depends_on, blocked_by, and handoff_context are experiment assignment fields.

nodes:
  - id: problem_x
    type: problem
    title: New problem
    parent: stage_x
    status: active
    fields:
      question: What should we test?
      current_best_option: option_x
  - id: option_x
    type: option
    title: Active option
    parent: problem_x
    status: active
  - id: experiment_x
    type: experiment
    title: First check
    parent: option_x
    status: queued
    fields:
      priority: high
      order: p2.2
      owner: agent_audio
      ready_for_agent: true
      depends_on:
        - option_x
      blocked_by: []
      handoff_context: Run the first check and record one finding.
updates:
  - id: problem_x
    fields:
      tag:
        - timeline-control
      next_actions:
        - Review first experiment result.
  - id: experiment_x
    status: queued
    fields:
      linked_artifact:
        - artifact_x
"""


CREATE_WORKSTREAM_EXAMPLE = """File schema v1: workstream_v1

problem:
  id: problem_x
  title: New research problem
  parent: stage_x
  status: active
  summary: Scope the next research branch.
  question: What should we optimize next?
  hypothesis: A narrower branch will reduce command count.
  tags:
    - workflow
  next_actions:
    - Run the first planned experiment.
active_option:
  id: option_x
  title: Active route
  status: active
  summary: Try the shortest route to evidence.
  hypothesis: This route has the shortest path to signal.
experiments:
  - id: experiment_x1
    title: Run first check
    success_criteria:
      - The check produces a comparable metric.
    metrics:
      - command_count
  - id: experiment_x2
    title: Run second check
    success_criteria:
      - The check can be reviewed without reading full node YAML.
followup_options:
  - id: option_followup_x
    title: Follow-up route
    status: open

Note: option nodes do not have a stored planned status. In graph plan and
workstream input files, option status planned is accepted as an alias for open.
"""


CREATE_ARTIFACT_EXAMPLE = """File schema v1: artifact_v1

id: artifact_x
title: Result bundle
status: done
summary: Collected outputs and metrics.
path: artifacts/experiment_x/run_x
links:
  metrics: artifacts/experiment_x/run_x/metrics.json
  review: notes/review.md
link_to:
  - experiment_x
  - option_x
"""


COMPLETE_EXPERIMENTS_EXAMPLE = """File schema v1: experiment_completion_v1

defaults:
  confidence: medium
  outcome: mixed
  artifact_ids:
    - artifact_shared
experiments:
  - id: experiment_a
    finding: First finding.
    result_summary: First summary.
    evidence:
      path: outputs/experiment_a
      links:
        metrics: outputs/experiment_a/metrics.json
  - id: experiment_b
    finding: Second finding.
    confidence: strong
    outcome: positive
    metrics:
      - accuracy=0.91
"""


FINALIZE_WORKSTREAM_EXAMPLE = """File schema v1: finalize_workstream_v1

option: option_x
status: accepted
problem_status: resolved
stage_status: done
summary_file: notes/options/option_x_summary.md
summary_target: report
artifacts:
  - artifact_x
sync_focus: false
report: true
agent: researcher
locale: en
"""


FILE_SCHEMAS = {
    "apply_graph_plan.py": {
        "file_schema": "graph_plan_v1",
        "example_file": APPLY_GRAPH_PLAN_EXAMPLE,
        "schema_command": "research-cockpit apply-graph-plan --print-schema",
    },
    "create_workstream.py": {
        "file_schema": "workstream_v1",
        "example_file": CREATE_WORKSTREAM_EXAMPLE,
        "schema_command": "research-cockpit create-workstream --print-schema",
    },
    "create_artifact.py": {
        "file_schema": "artifact_v1",
        "example_file": CREATE_ARTIFACT_EXAMPLE,
        "schema_command": "research-cockpit create-artifact --print-schema",
    },
    "complete_experiments.py": {
        "file_schema": "experiment_completion_v1",
        "example_file": COMPLETE_EXPERIMENTS_EXAMPLE,
        "schema_command": "research-cockpit complete-experiments --print-schema",
    },
    "finalize_workstream.py": {
        "file_schema": "finalize_workstream_v1",
        "example_file": FINALIZE_WORKSTREAM_EXAMPLE,
        "schema_command": "research-cockpit finalize-workstream --print-schema",
    },
}


def file_schema_for_script(script_name: str) -> dict[str, str]:
    return FILE_SCHEMAS[script_name]
