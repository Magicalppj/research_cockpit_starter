# Spec: Incremental Validation And Artifact Control

## Status

Implementation plan and current-state guide. 当前工作区已按本计划落地主要 Phase 0-5 能力；后续维护应以本文的 worker/coordinator 边界和 safety rules 为准。

## Date

2026-06-30

## Problem

Research Cockpit 的下游使用场景已经进入数千节点规模。用户在约 2,600 个节点的 RC root 中测到：`validate` 约 7.19s，`bootstrap --coordinator --json` 30s timeout，`search` 约 9.43s，`suggest-next-actions` 输出约 1.9MB，full `node-context --json` 输出约 1.2MB 且耗时约 18.55s。

这个问题的根因不是单个命令慢，而是 worker agent 在新增或编辑少量节点后，经常被引导重复执行 full-root validate、build、smoke、search、suggestion 和 full context。随着 `graph/nodes/*.yaml` 和 artifact YAML 数量增长，这些全量操作会线性或近线性放大。

Artifact 元数据也在放大问题。`ingest-artifact` 和部分实验完成路径会为普通 evidence/run output 创建 artifact graph node。artifact YAML 数量达到数千后，普通 run output 会和真正需要长期导航、决策、baseline 追踪的证据混在同一个 graph namespace 中，拖慢全图读写，也让仓库更难管理。

## Objective

建立一套可渐进落地的增量验证和 artifact 控制方案，使下游 agent 在常规小改动后只验证自己变更的节点或记录，把 full-root gate 留给 coordinator 或最终交付阶段。

目标工作模式：

- Worker agent 新增或编辑少量节点后，默认执行 changed-scope validation 和 compact context，不运行 full `build` 或 root `smoke`。
- Coordinator 在一批 worker 修改结束后，执行 full `validate`、`build`、`smoke` 作为最终安全 gate。
- `validate --changed-node` 明确报告 changed/affected 范围、实际检查项，以及是否 fallback 到 full validation。
- 普通 run output 默认进入轻量 artifact record，不再无节制增加 `graph/nodes/artifact_*.yaml`。
- 需要长期保留、参与决策、baseline、导航的证据仍可 promote 成 artifact graph node。
- 文档、command manifest、mutation output 都直接引导下游 agent 走低成本流程。

## Assumptions

- Truth source 仍然是 file-based YAML/JSON，不引入数据库。
- 现有 root、现有 artifact graph node、现有 top-level CLI command 必须保持兼容。
- Full-root validation 仍然是最终权威检查；增量验证不能取代 merge、release、final handoff 前的 full gate。
- Worker agent 的典型修改范围是一个实验、一个 option subtree，或一个新建的小工作流。
- Artifact payload 可以位于 `research_cockpit/artifacts/`、git-ignored output root，或外部稳定存储；Research Cockpit 管的是元数据和引用关系。
- `dashboards/*` 是 generated context，不是 truth source；新增 index 也必须遵守这个边界。

## Non-Goals

- 不删除 `validate`、`build`、`smoke` 或现有 artifact node 能力。
- 不把增量验证伪装成全量验证。
- 不为了速度跳过 assignment scope、mutation lock、dry-run、audit log 等安全边界。
- 不在第一阶段自动迁移所有旧 artifact node。
- 不把 generated dashboard/index 文件提升为 truth source。
- 不要求常驻 daemon、数据库或后台服务。
- 不引入一个泛化的 `update-anything` API 来替代语义清晰的 workflow command。

## Current Code Hotspots

- `src/research_cockpit/mutation_runtime.py`: `load_validated_state()` 会读取全部 nodes 并执行 full validation；后续应让 mutation result 返回 changed verification commands，并在必要时支持 targeted preflight。
- `src/research_cockpit/model.py`: `load_nodes()` 解析所有 `graph/nodes/*.yaml`，`validate_cockpit()` 扫描全 root；应增加 affected validation facade 和 fallback contract。
- `src/research_cockpit/commands/validate_cockpit.py`: 当前只有 full-root 语义；应增加 `--changed-node`、`--changed-file`、`--changed-record`。
- `src/research_cockpit/commands/build_dashboard.py`: 当前每次重建全部 dashboard outputs、search index、suggestions、contexts；worker 默认不应 build，后续再加 validation index 和 affected build。
- `src/research_cockpit/commands/skill_smoke_test.py`: compact smoke 仍从 full validation/read models 开始；应增加 `--scope changed`。
- `src/research_cockpit/commands/ingest_artifact.py`: 普通 run output 默认容易变成 graph node；应增加 record-only ingest。
- `src/research_cockpit/commands/complete_experiment.py`: `--evidence-path` 可生成 inline artifact node；后续应支持 artifact record，再按需 promote。
- `src/research_cockpit/resources.py` 和 `src/research_cockpit/search_index.py`: 应支持 artifact records，并区分 promoted evidence 和普通 run output。

## Target Workflows

### Worker: Known Node Edit

Worker 已知自己修改了一个节点时，只运行 changed validation 和 compact context：

```sh
research-cockpit validate --root <root> --changed-node <node_id> --json
research-cockpit context --root <root> --id <node_id> --with-bootstrap --with-artifacts --compact --json
```

如果 mutating command 支持 `--no-build`，worker 应默认使用：

```sh
research-cockpit <mutating-command> --root <root> ... --no-build --json --compact
```

该路径不应运行 full `build`、full `smoke`、full `search` 或 full `suggest-next-actions`。

### Worker: Multiple Changed Nodes

当一个操作明确改动多个节点时，命令应支持重复传入 changed node：

```sh
research-cockpit validate --root <root> --changed-node option_x --changed-node experiment_x --json
```

JSON 输出必须列出实际 affected 范围，而不是只回显入参。

### Worker: File-Based Change

如果 agent 手动编辑或批量写入文件，应使用 changed file 入口：

```sh
research-cockpit validate --root <root> --changed-file graph/nodes/experiment_x.yaml --json
research-cockpit validate --root <root> --changed-files graph/nodes/experiment_x.yaml graph/nodes/option_x.yaml --json
```

`--changed-file` 和 `--changed-files` 应接受 root-relative path。绝对路径可以支持，但输出中应规范化为 root-relative path。

### Worker: Artifact Or Run Output

普通 run output 不应默认创建 graph node：

```sh
research-cockpit ingest-artifact --root <root> --node <experiment_id> --from <path> --run-id <run_id> --record-only --json --compact --no-build
research-cockpit validate --root <root> --changed-record artifact:<record_id> --json
research-cockpit run-context --root <root> --id <run_id> --compact --json
```

只有需要长期导航或参与决策时才 promote：

```sh
research-cockpit promote-artifact-record --root <root> --id <record_id> --artifact-id <artifact_id> --link-to <node_id> --json --compact
```

### Coordinator: Final Gate

Coordinator 或最终 handoff 仍执行 full-root 检查：

```sh
research-cockpit validate --root <root> --json
research-cockpit build --root <root>
research-cockpit smoke --root <root> --json --progress
```

这是唯一应被文档称为“最终确认”的路径。Worker 的 changed validation 只能说明自己影响范围内的检查通过。

## Interface Spec

### `validate` Incremental Flags

MVP 必须支持：

```sh
research-cockpit validate --root <root> --changed-node <node_id> --json
research-cockpit validate --root <root> --changed-file <path> --json
research-cockpit validate --root <root> --changed-files <path>... --json
research-cockpit validate --root <root> --changed-record artifact:<record_id> --json
```

后续可增加同义入口：

```sh
research-cockpit validate --root <root> --scope affected --id <node_id> --json
```

规则：

- 不带 changed/scope flags 时保持 full validation 现状。
- 多个 `--changed-node` 可以重复传入。
- `--changed-file` 是单文件便捷入口；`--changed-files` 支持多文件。
- `--changed-record` 使用 `<kind>:<id>`，MVP 至少支持 `artifact:<record_id>`。
- 如果 incremental mode 需要 full fallback，命令仍可成功或失败，但 JSON 必须显示 `fallback.used_full_validation: true` 和原因。

### `validate` JSON Envelope

增量验证输出应稳定，便于 agent 消费：

```json
{
  "ok": true,
  "mode": "incremental",
  "root": "research_cockpit",
  "changed": {
    "nodes": ["experiment_x"],
    "files": ["graph/nodes/experiment_x.yaml"],
    "records": []
  },
  "affected": {
    "nodes": ["experiment_x", "option_x", "problem_x"],
    "runs": ["run_x"],
    "assignments": ["assign_x"],
    "artifact_records": []
  },
  "checks": [
    {"name": "node_schema", "passed": true},
    {"name": "local_references", "passed": true},
    {"name": "reverse_references", "passed": true}
  ],
  "fallback": {
    "used_full_validation": false,
    "reason": ""
  },
  "errors": [],
  "warnings": []
}
```

Full validation 输出可继续使用现有格式；如果统一 envelope，应设置 `mode: full`。
### `smoke` Changed Scope

新增 changed smoke：

```sh
research-cockpit smoke --root <root> --scope changed --id <node_id> --json --progress
```

`--scope changed` 只做：

- `validate --changed-node <node_id> --json`
- `context --id <node_id> --with-bootstrap --with-artifacts --compact --json`
- 如果目标是 run 或 artifact record，则执行对应 compact context。
- `commands --json --compact` 的轻量可用性检查。

`--scope changed` 不应调用：

- full `bootstrap --coordinator`
- full `search`
- full `suggest-next-actions`
- full `node-context` without `--compact`
- full `build`

保留：

```sh
research-cockpit smoke --root <root> --json --progress
research-cockpit smoke --root <root> --full --json --progress
```

默认 `smoke` 继续作为 coordinator/root-level compact smoke；`--full` 只用于诊断旧 full subprocess workflow。

### Mutation Output Guidance

所有 mutating command 的 compact JSON 应区分 worker verification 和 coordinator final gate：

```json
{
  "ok": true,
  "changed": {
    "nodes": ["experiment_x"],
    "records": []
  },
  "verify_commands": [
    "research-cockpit validate --root <root> --changed-node experiment_x --json",
    "research-cockpit context --root <root> --id experiment_x --with-bootstrap --with-artifacts --compact --json"
  ],
  "final_handoff_commands": [
    "research-cockpit validate --root <root> --json",
    "research-cockpit build --root <root>",
    "research-cockpit smoke --root <root> --json --progress"
  ]
}
```

规则：

- `verify_commands` 必须是 worker 可以快速执行的 changed-scope 命令。
- `final_handoff_commands` 可以包含 full checks，但不要把它们放进 worker 默认下一步。
- 如果命令影响多个节点，`verify_commands` 应列出全部 changed nodes。
- 如果命令只影响 artifact record，不要建议 graph-node context，除非 record 已 promote。

## Affected Validation Model

增量验证必须显式计算 affected scope，而不是只检查入参节点。

### Local Checks

对 changed node 可本地验证：

- 必填字段：`id`、`type`、`title`、`status`。
- node type 和 status 组合合法。
- `parent` 不指向自己。
- `children` 不包含自己。
- type-specific structured fields 合法。
- findings schema 合法。
- local artifact metadata 和 retention schema 合法。
- assignment-local fields 合法。
- baseline-local structure 合法。

### Reverse Reference Checks

对 changed node id 或 changed record id，需要检查反向引用：

- 哪些 nodes 把 changed id 放入 `children`。
- 哪些 nodes 通过 `parent` 指向 changed id。
- 哪些 nodes 使用 `linked_artifacts` 指向 changed artifact node。
- 哪些 findings 使用 `linked_artifact_records` 指向 changed artifact record。
- 哪些 decisions 引用 changed experiment/option。
- 哪些 baselines 引用 changed option/decision/artifact。
- `derived_from`、`supporting_experiments`、`contradicting_experiments`、`alternatives_considered`。
- 哪些 runs 的 `experiment_id` 指向 changed node。
- 哪些 assignments 的 `root_node`、`current_node`、`allowed_subtree.root` 指向 changed node。
- coordinator/current focus fields 是否指向 changed node。

### Ancestor And Descendant Checks

当 lifecycle/status 或 tree relation 改动时，需要检查：

- affected ancestors 的 terminal parent guard。
- assignment allowed-subtree 是否仍覆盖 changed node 和相关 descendants。
- focus path connectivity。
- affected option subtree 的 workstream summary 是否仍能生成。

### Full Fallback Conditions

以下情况必须 fallback 到 full validation，或返回明确错误：

- 缺少 validation index，且当前实现无法证明反向引用完整。
- validation index schema 不兼容。
- node id 被修改。
- 删除 node 或 artifact record，且无法证明没有外部引用。
- `graph/edges.yaml` 变更。
- `current_state.yaml`、`coordinator_state.yaml`、assignments、agents、runs、gate results 出现 broad changes，且没有 changed target。
- duplicate id 检查无法从 index 或 full scan 证明。
- 用户启用 strict lifecycle 检查，但 index 不能解析 descendants。

Fallback 不是失败，但必须可见：

```json
{
  "fallback": {
    "used_full_validation": true,
    "reason": "validation_index_missing"
  }
}
```

## Validation Index

### Location

新增 generated index：

```text
research_cockpit/dashboards/validation_index.json
```

该文件属于 generated context，不是真相源。删除它不能导致 root 损坏，只会让 changed validation fallback 到 full scan。

### Schema

建议 schema：

```json
{
  "schema_version": "validation_index_v1",
  "root_fingerprint": "sha256...",
  "generated_at": "2026-06-30T00:00:00Z",
  "nodes": {
    "experiment_x": {
      "file": "graph/nodes/experiment_x.yaml",
      "file_hash": "sha256...",
      "type": "experiment",
      "status": "running",
      "parent": "option_x",
      "children": [],
      "out_refs": {
        "linked_artifacts": ["artifact_x"],
        "linked_artifact_records": ["artifact_record_x"],
        "derived_from": []
      }
    }
  },
  "reverse_refs": {
    "artifact_x": [
      {"node_id": "experiment_x", "field": "linked_artifacts"}
    ],
    "artifact_record_x": [
      {"node_id": "experiment_x", "field": "findings.linked_artifact_records"}
    ]
  },
  "runs_by_experiment": {
    "experiment_x": ["run_x"]
  },
  "assignments_by_node": {
    "option_x": ["assign_x"]
  },
  "artifact_records_by_node": {
    "experiment_x": ["artifact_record_x"]
  }
}
```

### Update Policy

Phase 1 可以先不依赖 index，仍读取全量节点，但输出 changed/affected/fallback contract。这能先稳定接口和测试。

Phase 2 再使用 `validation_index.json` 避免读取未变更 node YAML。

更新规则：

- Full `build` 写入完整 index。
- Full `validate --json --write-index` 可以显式刷新 index；默认 full validate 不应偷偷写 generated file。
- Mutating command 只有在 truth-source 写入成功后，才可以 patch index。
- 如果 patch index 失败，mutation 不应回滚业务写入，但必须返回 warning，建议运行 full `build`。
- Index 中所有 path 使用 root-relative path。

## Build Strategy

### Phase 1 Policy

先不实现 incremental build。更直接的收益来自不让 worker 每次 build。

Agent-facing docs 和 mutation output 必须明确：

- Worker 小改动后不跑 `build`。
- Coordinator 批量合并或最终交付前跑 `build`。
- UI/dashboard 可能暂时不是最新，但 truth-source YAML 已经更新，可用 changed context 验证。

### Future `build --affected`

后续可增加：

```sh
research-cockpit build --root <root> --affected --id <node_id> --json
```

该命令只能声称刷新它实际更新的 generated outputs，例如 `validation_index.json`、changed node 的 search index entries、linked resource rows、affected option workstream summary、changed assignment view rows。

如果不能保证全 dashboard 最新，JSON 必须返回 `full_dashboard_refreshed: false`。长期可以把 generated outputs 拆成更细粒度文件，例如 `dashboards/nodes/<node_id>.json`、`dashboards/options/<option_id>_workstream.json` 和 `dashboards/validation_index.json`。

不要在 Phase 1 同时拆 dashboard outputs。先修正 worker workflow，避免大改动叠加。

## Artifact Control Model

### Rule

区分两类 evidence metadata：

- Artifact graph node: 存在于 `graph/nodes/artifact_*.yaml`，用于长期证据、决策证据、baseline、导航入口，只用于 promoted evidence。
- Artifact record: 存在于 `artifact_records/*.yaml`，用于普通 run output、logs、metrics、可再生成产物，是 worker 默认路径。

### Sidecar Layout

MVP 推荐一实验一文件，减少文件数量和 merge 冲突的折中较好：

```text
research_cockpit/artifact_records/<experiment_id>.yaml
```

Schema：

```yaml
schema_version: artifact_records_v1
experiment_id: experiment_x
records:
  artifact_record_experiment_x_run_001:
    record_id: artifact_record_experiment_x_run_001
    run_id: run_001
    title: "Run 001 output bundle"
    status: available
    path: artifacts/experiment_x/run_001
    links:
      metrics: artifacts/experiment_x/run_001/metrics_summary.json
      report: artifacts/experiment_x/run_001/report.md
    artifact_kind: run_output
    retention:
      class: reproducible_output
      reason: "Metrics summary is preserved; raw payload can be regenerated."
    created_at: "2026-06-30"
    updated_at: "2026-06-30"
    promoted_artifact_id: null
```

如果单个 experiment 文件过大，再引入 sharding：

```text
research_cockpit/artifact_records/<experiment_id>/<yyyy-mm>.yaml
```

不要在 MVP 中默认 sharding，除非测试证明单文件不可控。

### Artifact Record Commands

新增或扩展：

```sh
research-cockpit ingest-artifact --root <root> --node <experiment_id> --from <dir> --run-id <run_id> --record-only --json --compact --no-build
research-cockpit artifact-records --root <root> --experiment <experiment_id> --json --compact
research-cockpit promote-artifact-record --root <root> --id <record_id> --artifact-id <artifact_id> --link-to <node_id> --dry-run --json --show-diff
research-cockpit compact-artifacts --root <root> --dry-run --json --show-diff
research-cockpit compact-artifacts --root <root> --id <artifact_id> --execute --json --show-diff
```

Compatibility rule：

- `ingest-artifact` 现有默认行为先保持不变。
- 新增 `--record-only`，文档把它设为 worker 默认建议。
- 是否未来把 `ingest-artifact` 默认改为 record-only，需要单独兼容阶段和明确 migration note。

Future optional commands, not part of the landed MVP, can add `create-artifact-record` and `update-artifact-record` if manual sidecar-only metadata editing becomes necessary. For now, use `ingest-artifact --record-only`, `artifact-records`, `promote-artifact-record`, and `compact-artifacts`.

### Finding Link Model

保留现有 graph artifact link：

```yaml
linked_artifacts:
  - artifact_promoted_summary
```

新增 record link：

```yaml
linked_artifact_records:
  - artifact_record_experiment_x_run_001
```

不要把 artifact node id 和 artifact record id 混在同一个字段中。这样 validator、UI、context 和 migration 都能清楚区分。

### Promotion Rules

Artifact record 应 promote 成 graph artifact node，当它满足任一条件：

- 被 accepted decision 引用。
- 属于 explicit baseline。
- 是 strong finding 的关键证据。
- 是需要长期人工浏览的 review bundle。
- 是下游训练/评估必须保留的 final checkpoint。
- `retention.class` 明确为 `evidence_critical`。

保持 record-only，当它是 raw generated samples、temporary metrics/logs、reproducible output、disposable cache、intermediate checkpoint，或普通 gate output 且结果已经摘要到 run/gate records。

### Compaction And Migration

新增 dry-run audit：

```sh
research-cockpit compact-artifacts --root <root> --dry-run --json --show-diff
```

显式执行只允许单个 `can_demote` artifact：

```sh
research-cockpit compact-artifacts --root <root> --id <artifact_id> --execute --json --show-diff
```

分类：

- `must_keep_node`: accepted decision、baseline、strong finding、explicit evidence-critical retention。
- `can_demote`: 普通 run output，有 path/links，且没有 durable graph-level dependency。
- `needs_review`: retention metadata 缺失或引用关系不明确。
- `cannot_demote`: malformed，或被不支持的外部关系引用。

安全 demotion 顺序：

1. 创建 artifact record。
2. 在安全时把 experiment 的 `linked_artifacts` 改为 `linked_artifact_records`；findings、decision、baseline 引用仍进入 `must_keep_node` 或 `needs_review`。
3. 执行 full validation。
4. 删除 eligible artifact node。
5. 写 migration report，确保可审计。

Demotion 永远不删除 payload files。Payload cleanup 必须是另一个显式 maintenance action。

## Implementation Plan

### Phase 0: Baseline Measurement

Goal: 在优化前记录大型 root 的成本，避免凭感觉优化。

Tasks:

- [x] 扩展或新增 large-root fixture generator，支持 5k graph nodes 和 4k artifact-like records。
- [x] 增加 worker edit flow benchmark，记录 mutation `--no-build`、full validate、build、compact smoke、changed context 的耗时和输出大小。
- [x] 将用户已观测的 2,600-node root 数据作为参考，不把它当作硬编码测试阈值。

Acceptance:

- 生成的 fixture 能通过当前 full `validate`。
- Benchmark JSON 包含每个子步骤的 duration、stdout bytes、stderr bytes、exit code。
- 文档中记录 baseline，并说明机器差异。

Verify:

```sh
python dev/scripts/generate_large_cockpit_fixture.py --root .test_tmp/perf_5000 --nodes 5000 --artifacts 4000
research-cockpit validate --root .test_tmp/perf_5000 --json
python dev/scripts/benchmark_build.py --root .test_tmp/perf_5000 --runs 1 --json
```

### Phase 1: Worker Workflow Contract

Goal: 不依赖 index，先让 worker 不再被引导跑全量流程。

Tasks:

- [x] 增加 `validate --changed-node`、`--changed-file`、`--changed-files`、`--changed-record` CLI contract。
- [x] 初始实现可以内部 full-load，但 JSON 必须使用 incremental envelope，并报告 affected/fallback。
- [x] 增加 `smoke --scope changed --id <id>`，只执行 changed validation 和 compact context。
- [x] 更新 compact mutation result 的 `verify_commands` 和 `final_handoff_commands`。
- [x] 更新 `commands --json --compact` manifest，使 agent 能发现 changed-scope checks。
- [x] 更新 agent-facing docs，明确 worker/coordinator 分工。

Acceptance:

- Worker 小改动后的建议命令不包含 full `build` 或 root `smoke`。
- `validate --changed-node` 输出 `mode: incremental`。
- 如果当前实现仍执行 full scan，输出必须显示 fallback 或 implementation note，不能假装完全增量。
- `smoke --scope changed` 不运行 full search/suggestions/bootstrap。

Verify:

```sh
python -B -m unittest tests.test_scripts.ScriptBehaviorTests -k changed
python -B -m unittest tests.test_scripts.ScriptBehaviorTests -k smoke
python -m unittest discover -s tests
python dev/scripts/run_skill_release_check.py --json --skip-mutating
git diff --check
```

### Phase 2: Validation Index

Goal: 让 changed validation 真正避免读取未变更 node YAML。

Tasks:

- [x] 新增 `ValidationIndex` model/parser。
- [x] Full `build` 写入 `dashboards/validation_index.json`。
- [x] `validate --changed-node` 优先使用 fresh index 计算 affected scope。
- [x] 当 index 缺失、stale、schema 不兼容时，明确 fallback。
- [x] 增加 loader-count 或 benchmark 测试，证明未变更 node 不被逐个解析。

Acceptance:

- Fresh index 存在时，changed validation 只读取 changed/affected truth files。
- 修改 node 文件后，file hash mismatch 能触发 fallback 或 targeted reread。
- JSON 中包含 index freshness/fallback 信息。
- 删除 index 后，命令仍正确，只是变慢并报告 fallback。

Verify:

```sh
research-cockpit build --root .test_tmp/perf_5000 --json
research-cockpit validate --root .test_tmp/perf_5000 --changed-node <node_id> --json
python -m unittest discover -s tests
python dev/scripts/run_skill_release_check.py --json --skip-mutating
git diff --check
```

Performance budget after Phase 2:

- 5k node + 4k artifact-record fixture 中，`validate --changed-node` 目标低于 500 ms。
- `smoke --scope changed` 目标低于 1.5s。
- 这些预算是工程目标，不应让测试在慢机器上脆弱失败；自动测试可使用较宽阈值或只验证复杂度行为。

### Phase 3: Affected Build

Goal: 为需要 dashboard 局部刷新的场景提供明确但保守的入口。

Tasks:

- [x] 增加 experimental `build --affected --id <node_id> --json`。
- [x] 明确它刷新哪些 generated outputs。
- [x] 如果 full dashboard 未刷新，JSON 必须设置 `full_dashboard_refreshed: false`。
- [x] 只在必要时拆分 per-node/per-option generated files。

Acceptance:

- `build --affected` 不声称 full build。
- UI/context 读取不会把 partial generated outputs 当成全量最新。
- Full `build` 仍能重建全部 dashboard outputs。

Verify:

```sh
python -B -m unittest tests.test_scripts.ScriptBehaviorTests -k build
python -m unittest discover -s tests
python dev/scripts/run_skill_release_check.py --json --skip-mutating
git diff --check
```

### Phase 4: Artifact Record-First Path

Goal: 普通 run output 不再增加 graph node 数量。

Tasks:

- [x] 新增 `artifact_records` schema、loader、validator。
- [x] 新增 `ingest-artifact --record-only`。
- [x] 新增 `linked_artifact_records` validation。
- [x] 新增 `artifact-records` 查询命令。
- [x] 新增 `promote-artifact-record`。
- [x] 更新 resources/search/context，使 record-only evidence 可见但不伪装成 graph node。

Acceptance:

- `ingest-artifact --record-only` 不增加 `graph/nodes/*.yaml` 数量。
- Artifact record 能被 experiment/run/finding 引用并通过 validation。
- Promote 后产生 artifact graph node，并写回 `promoted_artifact_id`。
- 现有 artifact node root 继续通过 validation。

Verify:

```sh
python -B -m unittest tests.test_scripts.ScriptBehaviorTests -k artifact_record
python -B -m unittest tests.test_scripts.ScriptBehaviorTests -k ingest_artifact
python -m unittest discover -s tests
python dev/scripts/run_skill_release_check.py --json --skip-mutating
git diff --check
```

### Phase 5: Artifact Compaction And Migration

Goal: 让已有数千 artifact nodes 的 root 可以逐步变干净。

Tasks:

- [x] 新增 `compact-artifacts --dry-run`。
- [x] 实现 `must_keep_node`、`can_demote`、`needs_review`、`cannot_demote` 分类。
- [x] 实现安全 demotion execution，但默认要求 explicit flag。
- [x] 写 migration report，不删除 payload files。
- [x] 更新 maintenance audit，报告 artifact-node 增长、record-only candidates、缺失 retention metadata、大 payload warnings。

Acceptance:

- Dry-run 无写入。
- Demotion 后 full validation 通过。
- 任何 payload file 都不会被删除。
- 不明确的 artifact node 进入 `needs_review`，不能自动 demote。

Verify:

```sh
research-cockpit compact-artifacts --root .test_tmp/perf_5000 --dry-run --json --show-diff
python -B -m unittest tests.test_scripts.ScriptBehaviorTests -k compact_artifacts
python -m unittest discover -s tests
python dev/scripts/run_skill_release_check.py --json --skip-mutating
git diff --check
```

## Testing Strategy

### Unit Tests

必须覆盖：

- changed node 到 affected scope 的计算。
- reverse reference lookup。
- fallback reason。
- artifact record schema。
- finding `linked_artifact_records`。
- record promotion。
- artifact demotion classification。
- compact mutation `verify_commands`。

### Integration Tests

必须覆盖：

- `validate --changed-node` 对普通 summary 修改通过。
- `validate --changed-node` 对非法 status 失败。
- `validate --changed-node` 对缺失 reference 能报错或 fallback。
- 删除被引用 node 时，能报错或 fallback，并输出清晰原因。
- `smoke --scope changed` 不运行 full suggestions/search/bootstrap。
- `ingest-artifact --record-only` 不创建 graph artifact node。
- `promote-artifact-record` 创建 graph artifact node 并可 link。
- `compact-artifacts --dry-run` 无写入。

### Performance Tests

建议新增 benchmark，不建议把严格毫秒阈值放进普通 unit test。普通 CI 可验证复杂度行为，例如 loader call count、输出大小上限、是否调用 full subprocess。

Benchmark commands:

```sh
research-cockpit validate --root .test_tmp/perf_5000 --changed-node <id> --json
research-cockpit smoke --root .test_tmp/perf_5000 --scope changed --id <id> --json --progress
research-cockpit ingest-artifact --root .test_tmp/perf_5000 --node <experiment_id> --from <dir> --record-only --json --compact --no-build
```

### Release Checks

每个 phase 落地前至少运行：

```sh
python -m unittest discover -s tests
python dev/scripts/run_skill_release_check.py --json --skip-mutating
git diff --check
```

涉及 CLI manifest、AGENTS、SKILL 或 capabilities 时，必须补相应文档测试或 release check 覆盖。

## Documentation Updates

实现时必须同步：

- `AGENTS.md`: worker read/write/verification path 改为 changed validation；full gate 只留给 coordinator/final handoff。
- `SKILL.md`: 避免启动时链式运行 bootstrap/search/suggest/full context；给出 changed-scope 默认流程。
- `capabilities/experiment-tracking.md`: artifact record-first ingest 和 promotion rules。
- `capabilities/maintenance.md`: `compact-artifacts` 和 retention audit。
- `docs/large-repo-hygiene.md`: artifact graph node 的使用边界。
- `docs/artifact-retention-policy.md`: record-only、promote、demote 的策略。
- `docs/internal-architecture.md`: 如果新增 `artifact_records.py`、`incremental_validation.py`，补模块边界。

文档措辞必须直接告诉下游 agent：

- 已知 node id 时，先用 changed validation。
- 不要因为编辑一个节点就跑 full build/smoke。
- artifact run output 默认用 record-only。
- 最终交付前才由 coordinator 跑 full validate/build/smoke。

## Developer Guardrails

Always:

- 保持 full validation 是最终权威。
- 在 JSON 中明确 `mode`、affected scope、fallback。
- 把 validation index 放在 `dashboards/`。
- 把 artifact records 放在 graph nodes 之外。
- 保留现有 artifact node 行为。
- 更新 `commands --json --compact`。
- 为每个新 command 写 dry-run 或 no-write 测试。

Ask first:

- 改变 `ingest-artifact` 默认行为。
- 对真实 root 自动 demote artifact nodes。
- 引入 sharded artifact record layout。
- 把 index 放到 `dashboards/` 之外。
- 删除或重命名现有命令。

Never:

- 把 incremental validation 说成 whole-root valid。
- 在 compaction 中删除 payload files。
- 隐藏 full fallback。
- 让 generated dashboard/index 成为 truth source。
- 为小改动建议 full search、full suggestions、full node-context。

## Success Criteria

- Worker 编辑一个节点后，有明确的低成本验证路径，不需要 full `build` 或 root `smoke`。
- Mutating command 输出的 `verify_commands` 默认指向 changed validation。
- Coordinator final gate 仍保留 full `validate`、`build`、`smoke`。
- Incremental validation 能报告 changed/affected/fallback。
- Phase 2 后，changed validation 不再读取所有 node YAML。
- Artifact record-only ingest 能保存 run evidence，但不增加 graph node 数。
- Artifact compaction 能先 dry-run 分类，再安全 demote。
- 旧 artifact node root 保持兼容。
- Agent-facing docs 不再引导下游 agent 走低效全量流程。

## Open Questions

- `ingest-artifact` 是否最终改成默认 record-only，还是长期保持 graph node 默认？
- Artifact records 是一实验一文件，还是一记录一文件更适合多 agent merge？
- `validate --changed-node` 是否应默认 patch `validation_index.json`，还是只由 `build` 写 index？
- Record-only ingest 缺省 retention class 应该是 `reproducible_output` 还是要求用户显式提供？
- `complete-experiment --evidence-path` 是否在兼容阶段改为默认 artifact record？
- UI 是否默认隐藏 record-only artifacts，还是在单独 Resources tab 中展示？