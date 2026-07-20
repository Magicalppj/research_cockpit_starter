# Research Cockpit

![Research Cockpit poster](assets/research-cockpit-poster.png)

一张速览图：Research Cockpit 将 YAML/Markdown truth source、CLI、Streamlit UI 和 Agent Context 串成同一条研究状态工作流。

Research Cockpit 是一个 repo-native 的研究状态管理工具。它把研究问题、方案、实验、证据和决策记录在仓库里的 YAML/Markdown 文件中，并提供 CLI、Streamlit UI 和 agent 友好的上下文接口。

它适合这些场景：

- 你在一个代码仓库里同时推进多个 research problem、方案分支和实验。
- 你需要让人类和 coding agent 共享同一份研究状态，而不是依赖聊天记录或散落的笔记。
- 你想快速看到“当前焦点是什么、有哪些备选方案、实验证据在哪里、下一步该做什么”。

核心图谱模型是：

```text
stage -> problem -> option -> experiment -> decision
```

`artifact` 也是合法节点类型，但默认作为支持证据使用，例如结果目录、指标 JSON、review bundle、报告或其它需要独立追踪的研究产物。

![Research Cockpit UI](assets/ui.jpg)

## 5 分钟跑通 Demo

前置条件：

- Python 3.10+
- 在本仓库根目录执行命令

安装本地开发包：

```sh
python -m pip install -e .
```

验证内置 demo 数据：

```sh
research-cockpit validate --root examples/demo_research_cockpit --json
research-cockpit build --root examples/demo_research_cockpit
research-cockpit smoke --root examples/demo_research_cockpit --json --progress
```

启动 UI：

```sh
research-cockpit ui --root examples/demo_research_cockpit --server.port 8501
```

打开浏览器访问：

```text
http://localhost:8501
```

Demo 已包含一个显式 baseline：在研究图使用 React Flow 并打开 Baseline Lens，可看到 baseline 来源节点的 `SOURCE` 和默认方案节点的 `CURRENT BASELINE` 标记。
“基线 / Accepted”页面会集中展示默认 baseline、accepted options 和 accepted decisions，并生成 `set-baseline` 命令。

如果 `research-cockpit` 命令不可用，但 Python 包可以 import，可以用模块入口：

```sh
python -m research_cockpit.cli validate --root examples/demo_research_cockpit --json
python -m research_cockpit.cli ui --root examples/demo_research_cockpit --server.port 8501
```

## 用自己的仓库开始

这个仓库本身是可复用插件包。真实项目的研究状态应该放在调用方仓库的 `research_cockpit/` 目录里，而不是写进插件目录。

在你的项目仓库根目录运行：

```sh
research-cockpit init --root research_cockpit --json
research-cockpit ui --root research_cockpit --server.port 8501
```

如果你想从更完整的示例状态开始：

```sh
research-cockpit init --template demo --root research_cockpit --json
```

已知节点的 worker 启动时只读取一次 bounded execution context。CLI mutation 返回 `verified: true` 且 `additional_verification_required: false` 时直接继续；只有 compact 结果要求额外验证或发生手工 YAML 编辑时，才运行 changed-scope validate/context。不要每轮重新发现命令、全量 build 或 root smoke。

```sh
# Worker startup
research-cockpit context --root research_cockpit --id <node_id> --view execution --compact --json
# Conditional fallback only when additional verification is required
research-cockpit validate --root research_cockpit --changed-node <node_id> --json
research-cockpit context --root research_cockpit --id <node_id> --view execution --compact --json
```

`milestone handoff`（coordinator merge、release 或 research-stage closeout）前再跑全量 gate；普通 agent turn 不属于 milestone handoff：

```sh
research-cockpit validate --root research_cockpit --json
research-cockpit build --root research_cockpit
research-cockpit smoke --root research_cockpit --json --progress
research-cockpit ui --root research_cockpit --server.port 8501
```

## 你会得到什么

Research Cockpit 提供三层能力：

| 能力 | 面向谁 | 用来做什么 |
| --- | --- | --- |
| YAML/Markdown 状态文件 | 人类和 agent | 保存研究图谱、节点详情、笔记和证据引用 |
| CLI | 人类和 agent | 初始化、验证、构建 dashboard、更新状态、记录实验证据 |
| Streamlit UI | 人类 | 浏览研究图谱、筛选节点、查看详情、资源、证据和行动建议 |

关键原则：

- `research_cockpit/assignments/*.yaml` 是 assignment-scoped worker 的 cursor、next action 和状态 truth source；`research_cockpit/agents/*.yaml` 保存 agent identity 和 active assignment。
- `research_cockpit/coordinator_state.yaml` 是 coordinator/UI selection truth source。
- `research_cockpit/current_state.yaml` 是 legacy/coordinator compatibility state，也就是 coordinator/legacy compatibility layer；它不是普通 worker 的默认 cursor。
- `research_cockpit/graph/nodes/*.yaml`、`research_cockpit/runs/*.yaml`、`research_cockpit/gate_results/*.{yaml,json}` 和 `research_cockpit/artifact_records/*.yaml` 保存当前结构化事实。
- `research_cockpit/graph/interaction_events/**` 与 legacy `graph/interaction_log.yaml` 保存 append-only 操作历史，不替代当前事实。
- `research_cockpit/dashboards/*` 是生成文件；只在需要刷新生成上下文或 coordinator/milestone handoff 时运行 `research-cockpit build --root <root>`。
- 日常修改优先用 CLI 命令，避免直接手改 YAML 后破坏图谱关系。
- 同一个 data root 的写操作要顺序执行，不要并发写。

## UI 怎么用

启动后默认进入 Research Graph 页面。这里可以：

- 用 Focus 深度、当前分支、方案工作流、全局图谱切换视图范围。
- 按 node type、status、stage、focus role、workstream、blocking、missing evidence 筛选节点。
- 点击图谱节点，在右侧查看概览、证据、资源、关系、行动和 agent 上下文。
- 在右侧对当前节点收拢/展开整棵分支，或临时显示被默认隐藏的直接子节点。
- 保存常用 graph view，后续一键恢复筛选条件和分支可见性。

图谱默认使用 React Flow 和 Dagre layout。PyVis 是 legacy fallback。下游 worker 改 YAML 后先跑 changed-scope 验证；需要刷新 dashboard/UI 时，由 coordinator 或人工在 canonical root 运行 `research-cockpit build --root research_cockpit`，再在 UI 中点击 `Refresh`。如果 dashboard 缺失、损坏或比 truth source 旧，UI 会临时从 YAML/notes/runs/gates 现场重建视图并显示 stale warning；大仓库或多 agent 场景下建议在 canonical root 跑 `research-cockpit build --root research_cockpit --watch --interval 5 --json`。普通数据变化不需要重建 React bundle。

前端组件开发才需要 Node 依赖：

```sh
cd src/research_cockpit/ui/graph_component/frontend
npm install
npm run build
```

## 常用命令

| 目标 | 命令 |
| --- | --- |
| 初始化最小状态 | `research-cockpit init --root research_cockpit --json` |
| 初始化 demo 状态 | `research-cockpit init --template demo --root research_cockpit --json` |
| 全量校验数据 | `research-cockpit validate --root research_cockpit --json` |
| 检查语义陈旧状态 | `research-cockpit lint --root research_cockpit --semantic --json` |
| 生成 dashboard/context（coordinator/milestone handoff） | `research-cockpit build --root research_cockpit` |
| 诊断大图 build 性能 | `research-cockpit build --root research_cockpit --json --profile` |
| 综合维护审计 | `research-cockpit maintenance-audit --root research_cockpit --repo . --json` |
| 生成 worktree closeout 计划 | `research-cockpit worktree-closeout --root research_cockpit --repo . --worktree ../worktrees/<label> --classification discard_after_recording --dry-run --json` |
| 生成 sparse worktree 命令计划 | `research-cockpit start-agent-session --root research_cockpit --option <option_id> --label <label> --objective "..." --branch agent/<branch> --worktree ../worktrees/<label> --base main --dry-run --json --sparse --sparse-profile ml-experiment` |
| 启动 UI | `research-cockpit ui --root research_cockpit --server.port 8501` |
| 查看可用命令 | `research-cockpit commands --role worker --json --compact --summary-only` |
| 全局启动上下文 | `research-cockpit bootstrap --root research_cockpit --coordinator --json` |
| 单节点 execution context | `research-cockpit context --root research_cockpit --id <node_id> --view execution --compact --json` |
| 单节点变更校验 | `research-cockpit validate --root research_cockpit --changed-node <node_id> --json` |
| 搜索知识 | `research-cockpit search --root research_cockpit --query "keyword" --json --limit 5 --source node` |
| 单节点 smoke | `research-cockpit smoke --root research_cockpit --scope changed --id <node_id> --json --progress` |
| 全量 smoke（milestone handoff） | `research-cockpit smoke --root research_cockpit --json --progress` |

## 常见工作流

### 1. 创建一个新研究分支

适合从一个 problem 开始，创建 active option、planned experiments 和 follow-up options：

```sh
research-cockpit create-workstream --print-schema
research-cockpit create-workstream --root research_cockpit --file workstream.yaml --dry-run --json --show-diff
research-cockpit create-workstream --root research_cockpit --file workstream.yaml --no-build --json --compact
# 仅当 additional_verification_required 为 true 时验证 reported changed scope；全量 gate 只在 milestone handoff 运行。
```

`create-workstream` 不会自动改变全局 focus、暂停旧方案或删除旧分支。follow-up option 应使用 `open` 状态；文件输入里的 option `planned` 会被规范化为 `open`。

如果后续实验继承某个 worktree/option 的结论并会展开成多步探索，优先用 child workstream：在 `workstream.yaml` 里把 `problem.parent` 设为父 option id，并用 `problem.derived_from` 记录来源实验或方案。这样节点图会形成 `option -> problem -> option -> experiment` 的子树，而不是把所有后续实验平铺在同一层。

### 2. 执行并事务化收尾一个实验

Assignment-scoped worker 的默认路径是“领取/打开、创建并启动、一次收尾”，共不超过 3 次 CLI；不需要单独续租、ingest final payload、分别更新实验状态、记录 finding、创建 follow-up 或移动 cursor。`start.yaml` 使用 `work_start_v1`：

```sh
research-cockpit work start --root research_cockpit --assignment <assignment_id> --file start.yaml --json --compact
research-cockpit work close --root research_cockpit --assignment <assignment_id> --file closeout.yaml --json --compact
```

`work start` receipt 的 `entities.run_id` 替代手工 run id。`closeout.yaml` 使用 `work_close_v1`，控制字段取自当前 packet；final payload 可通过 `evidence_inputs` 同步 staging，无 payload 时省略该块：

```yaml
schema_version: work_close_v1
agent_id: agent_x
lease_id: lease_x
lease_epoch: 1
operation_id: op_close_x
input_revision: input-v1:x
run:
  id: run_x
  status: completed
experiment:
  status: done
  result_summary: "..."
finding:
  statement: "..."
  confidence: medium
  outcome: mixed
assignment_result:
  outcome: mixed
  summary: "..."
  delivery:
    git_commit: null
    changed_files: []
    tests:
      status: passed
      summary: "..."
  proposals: []
evidence_inputs:
  source: ../worktree/.agent_runs/run_x
  title: Final run evidence
  summary: "..."
  links:
    metrics: outputs/metrics.json
next_experiment:
  id: experiment_x_followup
  title: "..."
  next_action: "..."
```

`evidence_inputs`、`next_experiment` 均可省略；若证据已提前 ingest，则省略 `evidence_inputs` 并改用 `artifact_record.existing_record_id`，两者不可同时出现。`work close` 会原子写入 run、gate、finding、Evidence Bundle、experiment、cursor 和 lease transition。成功 receipt 已内部验证，不要重复 validate/context。完整模板见 `templates/launcher/work_close.example.yaml`。

兼容命令仍保留，但不是默认链路：无 active lease 的旧 run 可用 `complete-run`；没有 run record 的单实验可用 `complete-experiment`；真正的多实验批处理可用 `complete-experiments`。`create-followup-experiment`、`set-cursor` 和 `close-current-experiment` 只用于显式恢复/兼容场景。


### Reviewer 两步审核

Reviewer 不再单独读取 producer context：

```sh
research-cockpit review open --root research_cockpit --assignment <review_assignment_id> --json --compact
research-cockpit review report --root research_cockpit --assignment <review_assignment_id> --file review.yaml --json --compact
```

`review open` 一次返回 reviewer Work Packet、精确 producer result revision 和 bounded evidence links。`review report` 只写 reviewer assignment；coordinator 再用 `coord review --assignment <producer_assignment_id> --file verdict.yaml` 更新 producer review metadata，不改写 producer/reviewer Evidence Bundle。

### 3. 提前持久化 incremental evidence

仅当 streaming/intermediate output 必须在 close 前可恢复或供其他 agent 使用时，才增加 standalone ingest：

```sh
research-cockpit ingest-artifact --root research_cockpit --assignment <assignment_id> --node experiment_x --from ../worktrees/agent_x/.agent_runs/run_x --run-id run_x --agent agent_x --link metrics=metrics.json --json --compact --no-build
# 只有 durable graph evidence 才 promotion：
research-cockpit promote-artifact-record --root research_cockpit --id artifact_experiment_x_run_x --artifact-id artifact_experiment_x_run_x_promoted --link-to experiment_x --promotion-reason "用于 decision 或 baseline 的长期证据" --json --compact
```

`ingest-artifact` 会复制 payload 并创建轻量 artifact record；close 时通过 `artifact_record.existing_record_id` 引用。final-only payload 应直接使用 `work_close_v1.evidence_inputs`，省掉此命令。只有 durable graph evidence 才 promotion，并必须提供 `--promotion-reason`。

`smoke` 默认使用 compact 检查路径，避免在大图仓库里生成完整 `bootstrap`、`suggest-next-actions` 和 `node-context` JSON。大 root 下使用 `--progress` 把阶段进度输出到 stderr；仅在诊断旧完整子进程流程时加 `--full`。

### 4. 跟踪长任务 run / gate

长实验仍使用同一主流程；把 launcher、command、progress file、resources 和 retention 写入 `start.yaml` 的 `run` mapping。只有运行后状态或 metadata 变化时才增加 `update-run`，不要为了轮询重复写入：

```sh
research-cockpit work start --root research_cockpit --assignment <assignment_id> --file start.yaml --json --compact
research-cockpit update-run --root research_cockpit --assignment <assignment_id> --id run_x --status running --progress-file artifacts/experiment_x/run_x/progress.json --no-build
research-cockpit work close --root research_cockpit --assignment <assignment_id> --file closeout.yaml --json --compact
```

`work close` 负责 terminal run、gate metadata、finding、Evidence Bundle、experiment、可选 follow-up、assignment 和 final payload。阻塞性 preflight 才提前记录；普通 gate/final evidence 直接随 close 提交。仅 legacy 状态恢复使用 `complete-run --id`，仅确需读取运行状态时调用 `run-context`。
### 5. 显式创建或关联 artifact

```sh
research-cockpit create-artifact --print-schema
research-cockpit create-artifact --root research_cockpit --file artifact.yaml --dry-run --json --show-diff
research-cockpit create-artifact --root research_cockpit --id artifact_x --title "Result bundle" --status done --path artifacts/experiment_x/run_x --link metrics=artifacts/experiment_x/run_x/metrics.json --link-to experiment_x --no-build
research-cockpit link-artifact --root research_cockpit --artifact artifact_x --to option_x --no-build
```

Artifact 的 `path` 和 `links` 会按原样存储。生成的 resource rows 会包含解析路径、解析基准和文件是否存在等信息。

### 6. 收尾一个方案工作流

只有当 close-out 状态明确时再 finalize：

```sh
research-cockpit finalize-workstream --print-schema
research-cockpit finalize-workstream --root research_cockpit --file finalize.yaml --dry-run --json --show-diff
research-cockpit finalize-workstream --root research_cockpit --file finalize.yaml --json --compact
```

`finalize-workstream` 不会创建 artifact、接受 decision、暂停旧分支、删除节点或编造 next actions。

### 7. 把已采纳成果设为默认 baseline

`accepted option/decision` 是历史事实，`baseline` 是某个节点及其下游 agent 默认继承的工作前提。多个方案都被接受后，不要把全部 accepted history 塞进每个 agent 上下文；由上游节点或用户显式选择默认 baseline：

```sh
research-cockpit set-baseline --root research_cockpit --node problem_x --option option_x --decision decision_x --artifact artifact_bundle_x --reason "Default for follow-up experiments." --dry-run --json --show-diff
research-cockpit set-baseline --root research_cockpit --node problem_x --option option_x --decision decision_x --artifact artifact_bundle_x --no-build
research-cockpit set-baseline --root research_cockpit --node problem_x --clear --no-build
```

`context` 和 `node-context` 会解析并输出 `effective_baseline`：当前节点显式 baseline 优先，其次继承上游 baseline，再 fallback 到 problem 的 `current_best_option` / `resolved_by`，最后在当前焦点上下文中 fallback 到 `current_state.current_option`。UI 的“基线 / Accepted”页面用于集中查看默认 baselines、accepted options 和 accepted decisions，并生成设置 baseline 的命令；该页面按每个 problem 自己的 baseline 或 current best 展示，不把全局 `current_option` 当作所有 problem 的默认 baseline。

研究图的 Baseline Lens 会在 Focus 和方案工作流视图里标记当前默认 baseline、来源节点和选中节点使用的 baseline；完整 accepted history 仍在“基线 / Accepted”页面查看，避免节点图过载。

## 给 Agent 的最短上下文路径

有 `assignment_id` 的下游 worker，优先读取 assignment-scoped handoff：

```sh
research-cockpit work open --root research_cockpit --assignment <assignment_id> --compact --json
```
有 `review_assignment_id` 的 reviewer 使用独立入口；不要再执行 worker packet 或 producer context 命令：

```sh
research-cockpit review open --root research_cockpit --assignment <review_assignment_id> --compact --json
```

正常 reviewer handoff 必须已分配 agent 和 active lease，因而默认只需 `review open -> review report` 两次调用。


已知节点 id 时，优先用一个命令读取 bounded execution context。首次响应会返回 `revision`；重复轮询传入 `--since`，无变化时只返回最小 revision 响应：

```sh
research-cockpit context --root research_cockpit --id <node_id> --view execution --compact --json
research-cockpit context --root research_cockpit --id <node_id> --view execution --since <revision> --compact --json
```

不知道节点 id、需要全局 triage 时：

```sh
research-cockpit bootstrap --root research_cockpit --coordinator --json
```

需要完整方案子树、实验摘要和证据计数时：

```sh
research-cockpit option-workstream-context --root research_cockpit --id <option_id> --compact --json
```

选择命令面时先用 role-scoped command discovery：

```sh
research-cockpit commands --role worker --json --compact --summary-only
research-cockpit commands --role coordinator --json --compact --name start-agent-session
research-cockpit commands --role maintainer --surface maintenance --json --compact --summary-only
research-cockpit commands --role coordinator --json --compact --name create-workstream
research-cockpit artifact create --help
```

不要在已知节点任务里重复串联 `bootstrap`、生成 context packs 和 `node-context`。直接用 `context` 更短、更稳定。

## 数据结构

项目 data root 结构：

```text
research_cockpit/
  agents/                   # agent identity and active assignment
  assignments/              # worker-local cursor, next actions, and status
  coordinator_state.yaml    # coordinator/UI selection
  current_state.yaml        # legacy/coordinator compatibility
  graph/nodes/
  graph/edges.yaml
  graph/graph_views.yaml
  graph/interaction_log.yaml  # immutable legacy prefix after migration
  graph/interaction_events/   # manifest and generation-sharded JSONL events
  runs/                     # run/job execution records
  gate_results/             # standard gate metadata records and recorded gate JSON
  artifact_records/         # lightweight evidence metadata for ordinary run output
  artifact_migrations/      # artifact demotion audit reports
  notes/
  artifacts/                # 长期 evidence/result bundles
  dashboards/               # build 生成
```

节点状态按类型校验：

| Node type | Valid statuses |
| --- | --- |
| `stage` | `planned`, `active`, `blocked`, `done` |
| `problem` | `open`, `active`, `blocked`, `resolved`, `parked` |
| `option` | `open`, `active`, `promising`, `rejected`, `accepted`, `paused`, `parked` |
| `experiment` | `planned`, `queued`, `running`, `done`, `failed`, `cancelled` |
| `decision` | `proposed`, `accepted`, `superseded`, `rejected` |
| `artifact` | `draft`, `planned`, `active`, `done`, `superseded`, `deprecated`, `archived` |

注意：

- `promising` 只用于已有正向信号但还没完成比较、实验或 decision gate 的 `option`。
- 不要直接手改 YAML 把 decision 设置为 `accepted`，请用 `research-cockpit accept-decision`。
- `baseline` 不是节点类型；它是写在 stage/problem/option/experiment 上的默认引用，用 `set-baseline` 维护。
- 如果 legacy `interaction_log.yaml` 损坏，先用 `repair-interaction-log` 预览和修复；有效但过大的 legacy log 使用 `migrate-interaction-log --dry-run` 后再 `--execute`，不要手改已激活的 event backend。

## 仓库结构

```text
src/research_cockpit/       # Python runtime, CLI, model, Streamlit UI
capabilities/               # Agent-facing workflow docs
templates/                  # data-root templates
examples/demo_research_cockpit/
schemas/
docs/
tests/
SKILL.md
```

推荐阅读：

- `docs/repo-layout.md`: 仓库布局和模块地图。
- `docs/internal-architecture.md`: 内部模块边界和依赖规则。
- `docs/decisions/0001-layered-plugin-architecture.md`: layered plugin architecture rationale。
- `docs/decisions/0002-canonical-artifact-store-for-worktrees.md`: 为什么 worktree 输出要 ingest 到 canonical artifact store。
- `docs/decisions/0003-run-and-gate-sidecar-records.md`: 为什么 run/job 与 gate 记录作为实验旁路状态而不是图节点。
- `docs/migrations/0.2.0-record-first-artifact-ingest.md`: record-first 默认值、显式 promotion 和升级边界。
- `docs/launcher-output-conventions.md`: `run_record.txt`、`progress.json`、`gate_result.json` 和 `artifact_manifest.json` 约定。
- `docs/plans/2026-05-28-dashboard-build-performance.md`: 大图 dashboard build/profile 优化结果和后续增量构建计划。
- `capabilities/ui-dashboard.md`: Streamlit UI、React Flow 图谱和刷新行为。
- `capabilities/graph-state.md`: 图谱状态、saved views 和 interaction log。
- `capabilities/experiment-cycle.md`: 普通实验的启动、可选 ingest 和事务化 closeout。
- `capabilities/experiment-tracking.md`: 高级 gate、finding、artifact、retention 和 workstream 流程。
- `capabilities/node-management.md`: 节点创建、批量图谱计划和 lifecycle cleanup。

## 并行 Agent 和 Worktree

并行 agent 可以用 git worktrees 隔离代码和实验输出，但 Research Cockpit 状态只写入主仓库的 canonical `research_cockpit/` root。Worktree 是可删除的执行沙盒；长期研究记录必须先沉淀到 canonical root。

规则：

- Worktree 里做代码改动、运行实验、保存本地输出。
- Final output 通过 `work_close_v1.evidence_inputs` 一次复制到 canonical store；只有必须在 close 前 durable 的 incremental/streaming output 才单独运行 `ingest-artifact`。只有 durable evidence 需要图节点时才显式 promotion 并提供 reason。
- 不在 worktree 里 `research-cockpit init`，也不把 worktree-local path 当作长期 `--evidence-path`。
- `work close --file` 会完成或推进 assignment cursor；只有显式恢复才单独使用 `set-cursor`。全局 `set-focus` 和 lifecycle cleanup 由 coordinator 处理，`set-agent-focus` 只保留旧兼容场景。

删除 worktree 前检查：

1. 有价值的 final run directory 已随 `work close` 保存，或 earlier durable output 已 ingest，并能从 Evidence Bundle refs 定位。
2. Final output 已通过 `work_close_v1.evidence_inputs` 保存，或 earlier ingest 的 `record_id` 已通过 `artifact_record.existing_record_id` 关联；需要 durable graph evidence 时已显式 promotion 并记录 reason。
3. 有用代码已 merge/cherry-pick 或保存 patch。
4. 需要继承的正向结果已记录 decision 或 `set-baseline`。
5. closeout 的 compact 结果已内部验证，或已按要求完成 changed-scope 检查；只有同时发生 coordinator merge、release 或 research-stage milestone handoff 时才运行 full validate/build/smoke。

更多细节见 `capabilities/experiment-tracking.md`、`capabilities/integrations.md` 和 ADR-0002。

## 开发者验证

从插件根目录运行：

```sh
python -m unittest discover -s tests
research-cockpit smoke --root examples/demo_research_cockpit --json --progress
python dev/scripts/run_skill_release_check.py --json --skip-mutating
python dev/scripts/run_agent_usability_check.py --json
python dev/scripts/run_subagent_forward_check.py --json
git diff --check
```

如果只改文档，至少运行：

```sh
git diff --check
python dev/scripts/run_skill_release_check.py --json --skip-mutating
```
