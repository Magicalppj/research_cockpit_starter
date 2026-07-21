# Research Cockpit Agent Rules

## Source Of Truth

- `research_cockpit/agents/*.yaml`、`assignments/*.yaml`、`coordinator_state.yaml`、`current_state.yaml`、`graph/nodes/*.yaml`、`graph/interaction_events/**`、`runs/*.yaml`、`gate_results/*`、`artifact_records/*.yaml` 和 `handoffs/*.yaml` 是结构化 truth/audit state。
- `assignments/*.yaml` 是 worker-local cursor 与 next-action source；`coordinator_state.yaml` 是 coordinator/UI selection state；`current_state.yaml` 仅用于 legacy/coordinator compatibility。
- `artifacts/*` 是长期 evidence payload；`artifact_records/*.yaml` 是轻量 metadata/provenance。
- `graph/interaction_log.yaml` 在 segmented manifest 存在后是 immutable legacy prefix；只能通过 `maintenance migrate` 或 `maintenance repair` 处理 interaction backend。
- `dashboards/*` 是 generated projection。普通 worker verification 不需要 build。
- Markdown notes 只是 supporting records，不能作为 current state truth。

## Plugin Boundary

- Runtime code 位于 `src/research_cockpit/`，public workflow 只通过 `research-cockpit` CLI。
- 项目研究状态属于调用仓库的 `research_cockpit/`，不能写入 plugin package。
- 内部模块边界见 `docs/internal-architecture.md`；0.3.0 cutover 见 `docs/migrations/0.3.0-cli-cutover.md`。

## Read Order

只选择一条 startup path：

1. Review assignment: `research-cockpit review open --root <data-root> --assignment <review_id> --json --compact`。
2. Worker assignment: `research-cockpit work open --root <data-root> --assignment <assignment_id> --json --compact`；polling 复用 `--since <revision>`。
3. Known node without assignment: `research-cockpit context --root <data-root> --id <node_id> --view execution --json --compact`。
4. Global triage: `research-cockpit coord overview --root <data-root> --json --compact --limit 20`。
5. Additional context: bounded `search --limit 5 --source node`。
6. Missing operation only: `research-cockpit commands --role <role> --name <command> --json --compact`。

`execution` view 的 `node.parent` 是 graph 结构父级；目标为 experiment 时，创建 session 所需的 `option_id` 取 `node.parent.id`。`effective_baseline.option` 只是比较基线，可能不是该 experiment 的父 option。

不要在普通 startup 中组合多条 context 命令或 broad discovery。工作目录不可靠时使用 absolute `--root`。

## Write Rules

- 优先使用 canonical role facade；不要调用内部 command modules。
- Coordinator 创建 experiment session 时必须显式绑定该 option 下的 `experiment_id`；创建 review session 时必须绑定已完成且待审的 `producer_assignment_id`。
- Unclaimed packet 使用一次 `work claim --return-packet`，直接复用返回 packet。
- `work_start_v1` 从 packet 复制 agent、lease、`input_revision`，并将 `cursor.current_node` 作为显式 `experiment_id`；若该节点不是 experiment，停止并让 coordinator 修正 assignment。
- Claimed assignment 以 `work start` 开始；正常 mutation 与 launcher heartbeat 自动续租，`work renew` 仅用于 recovery。
- Closeout 使用一次 `work close --file <closeout.yaml>`；不要拆成多个 run/experiment/cursor 操作。
- 仅在缺少 closeout contract 时运行 `work close --print-schema --json --compact`；`finding.confidence` 只接受 `weak`、`medium` 或 `strong`。
- Final payload 放入 `work_close_v1.evidence_inputs`。仅在 close 前必须 durable 时使用 `work record`。
- Coordinator 用 `coord assign` 创建/更新 graph 或 assignment session，用 `coord decide` 处理 decision/baseline，用 `coord review` 应用 review metadata。
- 每个 mutating role request 必须有稳定 `operation_id`；只有 exact retry 才能复用。
- 新 branch proposal 不会自动创建 assignment；由 coordinator 评估后再 assign。
- Maintenance action 默认 dry-run；显式执行需 `execute: true`，compaction 每次只处理一个 eligible artifact。

## Verification

修改本仓库代码时只选择一层开发验证：`python dev/scripts/run_test_profile.py <fast|precommit|full> --json --compact --progress`。普通编辑使用 `fast` 并以 `--extra-test <test_id>` 加入受影响测试；提交前使用 `precommit`；merge/release 才使用 `full`。高层 profile 已包含低层覆盖，不要串行重复运行。详细边界见 `docs/testing-strategy.md`。

以下规则针对调用仓库中的 research state 验证，与上述 plugin 代码测试 profile 不混用。

Role-facade receipt 若报告 `internally_verified` 且 `additional_verification_required: false`，不要追加验证。

只有 manual known-node YAML edit 或 receipt 明确要求时才运行 changed scope：

```sh
research-cockpit validate --root <data-root> --changed-node <node_id> --json
research-cockpit context --root <data-root> --id <node_id> --view execution --json --compact
```

Coordinator merge、release 或 stage closeout 只运行：

```sh
research-cockpit coord handoff --root <data-root> --file <handoff.yaml> --json --compact --progress
```

不要在 handoff 前重复 standalone full validate/build/smoke。Standalone 命令只用于诊断；single-node smoke 使用 `--scope changed --id <node_id>`。

## Environment

- 可设置 `RESEARCH_COCKPIT_ROOT` 作为默认 data root。
- 若入口缺少依赖，从 plugin root 运行 `python -m pip install -e .`。
- Markdown 使用 UTF-8；不要提交本机用户名、absolute worktree path、virtualenv path 或 machine-specific interpreter path。
