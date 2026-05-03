from __future__ import annotations


APPLY_GRAPH_PLAN_EXAMPLE = """File schema v1: graph_plan_v1

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
updates:
  - id: problem_x
    fields:
      tag:
        - timeline-control
      next_actions:
        - Review first experiment result.
"""


CREATE_WORKSTREAM_EXAMPLE = """File schema v1: workstream_v1

problem:
  id: problem_x
  title: New research problem
  parent: stage_x
  status: active
  question: What should we optimize next?
active_option:
  id: option_x
  title: Active route
  status: active
  hypothesis: This route has the shortest path to signal.
experiments:
  - id: experiment_x1
    title: Run first check
    success_criteria:
      - The check produces a comparable metric.
  - id: experiment_x2
    title: Run second check
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
path: outputs/run_x
links:
  metrics: outputs/run_x/metrics.json
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
    next_actions:
      - Review aggregate result.
  - id: experiment_b
    finding: Second finding.
    confidence: strong
    outcome: positive
    metrics:
      - accuracy=0.91
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
}


def file_schema_for_script(script_name: str) -> dict[str, str]:
    return FILE_SCHEMAS[script_name]
