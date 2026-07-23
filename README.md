# Research Cockpit

Research Cockpit 是面向多 agent 并行研究的本地状态与证据管理插件。它把研究组合、assignment、run、review、decision 和 artifact provenance 保存在调用项目解析到的 state root（Git 项目默认 external state，`research_cockpit/` 仍可作为 legacy root）中，并通过一个按角色划分的 CLI 提供有界上下文和事务写入。

## 安装

```sh
python -m pip install -e .
research-cockpit init --project-id <project-id> --json
```

在 Git worktree 中省略 `--root` 会创建 external state，并只在仓库写入 portable `.research-cockpit.yaml` locator。`research-cockpit init --root research_cockpit` 是显式 legacy/in-place mode。运行时应显式传入 `--root`，或设置 `RESEARCH_COCKPIT_ROOT`。结构化输入使用 UTF-8 YAML/JSON 文件，避免依赖 shell-specific quoting。

## 最短工作流

已分配 worker 的默认路径只有三次 Research Cockpit 调用：

```sh
research-cockpit work open --root research_cockpit --assignment <assignment_id> --json --compact
research-cockpit work start --root research_cockpit --assignment <assignment_id> --file start.yaml --json --compact
research-cockpit work close --root research_cockpit --assignment <assignment_id> --file closeout.yaml --json --compact
```

`work_start_v1` 必须复制 packet 的 agent、lease、`input_revision`，并显式将 `cursor.current_node` 写为 `experiment_id`。该节点不是 experiment 时不要猜测目标，应由 coordinator 修正 assignment。

一个 assignment 对应一个 independently owned stage workstream，而不是每次 edit、retry、seed、parameter adjustment 或 local attempt。相同 research contract 的这些操作复用当前 assignment；只有独立 owner、durable handoff、independent review 或可能改变 research judgment 的阶段性交付才创建新的 assignment 或 graph node。

只有缺少输入契约时才运行 `work start --print-schema --json --compact` 或 `work close --print-schema --json --compact`；schema discovery 不是正常流程中的额外调用。`work_close_v1.finding.confidence` 只接受 `weak`、`medium` 或 `strong`。

最终 evidence 应优先放入 `work_close_v1.evidence_inputs`。只有 evidence 必须在 close 前持久化时才增加：

```sh
research-cockpit work record --root research_cockpit --assignment <assignment_id> --file record.yaml --json --compact
```

成功 mutation 若返回 `verification.status: internally_verified` 且 `additional_verification_required: false`，不要再运行 validate、context、build 或 smoke。

## Coordinator

```sh
research-cockpit coord overview --root research_cockpit --json --compact --limit 20
research-cockpit coord assign --print-schema --action graph_plan
research-cockpit coord assign --print-schema --action session
research-cockpit coord assign --print-schema --action review_session
research-cockpit coord assign --root research_cockpit --file assignment.yaml --json --compact
research-cockpit coord review --root research_cockpit --assignment <producer_assignment_id> --file verdict.yaml --json --compact
research-cockpit coord decide --print-schema --action promote
research-cockpit coord decide --print-schema --action set_baseline
research-cockpit coord decide --root research_cockpit --file decision.yaml --json --compact
```

`coord assign` 接受 `graph_plan` 或 `session` 动作。Experiment session 必须指定 option 下的 `experiment_id`；review session 必须指定已完成且待审的 `producer_assignment_id`。`coord decide` 统一 decision promotion、evidence refresh、checklist update、acceptance 和 effective baseline 变更。每个 mutating coordinator 请求必须使用稳定且唯一的 `operation_id`。

从已知 experiment 创建 session 时，先读取 bounded execution context，并将 `node.parent.id` 用作 `option_id`；不要使用可能指向其他比较对象的 `effective_baseline.option.id`。

里程碑合并、发布或阶段关闭只运行一个 gate：

```sh
research-cockpit coord handoff --print-schema
research-cockpit coord handoff --root research_cockpit --file handoff.yaml --json --compact --progress
```

它在一个 revision 上复用一次 full validation，并完成 build、compact smoke 和 lifecycle blocker 检查。不要在此前重复运行 standalone full validate、build 或 smoke。

默认情况下，任何 `queued`、`active` 或 `blocked` assignment 都会阻止 handoff；只有经过明确风险接受后才能在 handoff input 中允许 `unresolved_blockers`。

## Reviewer

```sh
research-cockpit review open --root research_cockpit --assignment <review_assignment_id> --json --compact
research-cockpit review report --root research_cockpit --assignment <review_assignment_id> --file review.yaml --json --compact
```

Reviewer 不自行创建 assignment。Coordinator 在 producer close 后通过 `coord assign --print-schema --action review_session` 生成输入并创建 review session，再把 assignment id 交给 reviewer。

Reviewer 只写自己的 review assignment；producer result 与 Evidence Bundle 不被改写。Coordinator 再通过 `coord review` 应用 verdict metadata。

## Maintenance

先审计，再执行一个有界动作：

```sh
research-cockpit maintenance audit --root research_cockpit --repo . --json --compact
research-cockpit maintenance repair --print-schema
research-cockpit maintenance migrate --print-schema
research-cockpit maintenance compact --print-schema
```

`maintenance_action_v1` 默认 `execute: false`。`action: artifact` 的 graph demotion 每次只处理一个 `can_demote` artifact，且不删除 payload 文件。`action: artifact_gc` 只回收一个 verified Cockpit-managed record：先 dry-run 取得 revision，再 quarantine，最后 delayed purge；legacy 和 external evidence 不在其范围内。

## 有界读取

已知 assignment 时只使用对应 role facade。已知 node 但没有 assignment 时使用：

```sh
research-cockpit context --root research_cockpit --id <node_id> --view execution --json --compact
```

缺少局部信息时使用 bounded search：

```sh
research-cockpit search --root research_cockpit --query "keyword" --source node --limit 5 --json
```

只有缺少某个 operation contract 时才查询单条 manifest：

```sh
research-cockpit commands --role <role> --name <command> --json --compact
```

普通启动不要运行 broad discovery，也不要把多个 context 命令串联起来。

## 诊断与 UI

Standalone `validate`、`build` 和 `smoke` 是诊断入口，不是普通 worker closeout 步骤：

```sh
research-cockpit validate --root research_cockpit --changed-node <node_id> --json
research-cockpit smoke --root research_cockpit --scope changed --id <node_id> --json --progress
research-cockpit build --root research_cockpit --json --profile
research-cockpit ui --root research_cockpit --server.port 8501
```

UI 优先读取 fresh dashboard projection；缺失或 stale 时会现场构建并显示 warning。大 root 可由 coordinator 在 canonical worktree 中运行 build watch，worker 不负责刷新 dashboard。

## 数据边界

- `storage.yaml`、`agents/*.yaml`、`assignments/*.yaml`、`coordinator_state.yaml`、`current_state.yaml`、`graph/nodes/*.yaml`、`graph/interaction_events/**`、`runs/*.yaml`、`gate_results/*`、`artifact_records/*.yaml` 和 `handoffs/*.yaml` 是结构化 truth/audit state。
- `coordinator_state.yaml` 保存 coordinator/UI selection；`current_state.yaml` 仅用于 legacy/coordinator compatibility。
- Evidence 默认 reference-only；`artifact_records/` 保存 location、ownership、integrity、inventory、retention、lifecycle 与 availability。配置的 external artifact root 承载新的 Cockpit-managed payload，`artifacts/` 只作为 legacy payload location 继续可读。
- `dashboards/` 是可重建 projection，不是 truth source。
- Markdown notes 是长文本 supporting records，不用于推断 current state。
- Worktree 隔离代码与实验过程；canonical data root 仍是共享研究状态边界。

## 0.3.0 Cutover

0.3.0 只公开当前 role-based CLI，不提供旧命令 alias。0.2.x 及更早保存的 nodes、assignments、runs、gates、artifact records/manifests、payload bytes 和 interaction history仍可直接读取并继续写入；未知字段会 round-trip 保留。

升级与替换表见 [0.3.0 CLI migration](docs/migrations/0.3.0-cli-cutover.md)；state、evidence、legacy migration 与 managed GC 见 [0.3.1 storage boundaries](docs/migrations/0.3.1-storage-boundaries-and-workstream-tracking.md)。内部模块保留仅用于复用 domain behavior，不构成 public CLI。

## 开发验证

选择与当前阶段对应的一层，不要依次运行三层：

```sh
# 日常编辑反馈，目标 30 秒内
python dev/scripts/run_test_profile.py fast --json --compact --progress

# 提交前 integration + read-only release gate，目标 60 秒内
python dev/scripts/run_test_profile.py precommit --json --compact --progress

# merge 或 release 前完整门禁
python dev/scripts/run_test_profile.py full --json --compact --progress

git diff --check
```

受影响测试不在默认 fast/precommit 集合时，重复传入 `--extra-test <module_or_test_id>`。成功时 runner 只输出有界摘要，失败时才返回截断后的 stdout/stderr tail。

分层原则与维护规则见 [testing strategy](docs/testing-strategy.md)。架构边界见 [internal architecture](docs/internal-architecture.md)，CLI envelope 与 schema 规则见 [command interface](docs/command-interface.md)。
