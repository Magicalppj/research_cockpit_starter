# Spec: Terminal Parent Lifecycle Guard

## Status

历史设计记录，已实施。本文中的独立 mutation 命令示例属于 0.2.x 接口，0.3.0 起以 `coord assign`、`coord decide` 和 `coord handoff` 为 public workflow；当前用法以 `docs/command-interface.md` 为准。

## Date

2026-05-29

## Objective

Research Cockpit 需要避免一种长期维护中很容易出现的状态不一致：父级 `problem` 或 `option` 已经被标记为终态，但其结构性下游仍然存在活跃工作。

目标行为：

- `problem` / `option` 进入 terminal 状态前，必须确认下游没有 active downstream work。
- 如果一条分支不再推进，应先显式关闭、暂停或 park 下游节点，再关闭父节点。
- 对历史仓库先给出 semantic warning 和 opt-in strict validation，避免一次性让旧数据无法 `validate`。
- 所有提示和失败都要是 machine-readable JSON，方便下游 agent 自动处理。

## Assumptions

- 下游关系使用现有结构图关系，也就是 parent/child/contains 这类 graph topology。
- 第一阶段只把 `problem` 和 `option` 作为会触发父级 lifecycle guard 的节点类型。
- 下游 blocker 第一阶段覆盖 `problem`、`option`、`experiment`；`decision` 是否阻塞留作开放问题。
- 现有仓库可能已经存在 terminal parent + active descendants，因此 rollout 必须渐进。
- 主层级仍保持 `option -> problem -> option -> experiment/decision`；实验之间的派生关系继续用 `derived_from` 等 metadata 表达，不把 experiment 作为主父节点。

## Lifecycle Semantics

### Parent Terminal States

第一阶段建议的父级终态集合：

```python
PARENT_TERMINAL_STATUSES = {
    "problem": {"resolved", "parked"},
    "option": {"accepted", "rejected", "paused", "parked"},
}
```

### Active Downstream Work

第一阶段建议的下游阻塞状态集合：

```python
ACTIVE_DOWNSTREAM_STATUSES = {
    "problem": {"open", "active", "blocked"},
    "option": {"open", "active", "promising"},
    "experiment": {"planned", "queued", "running"},
}
```

解释：

- `blocked problem` 仍算活跃工作，因为它还需要处理 blocker、改 scope 或被明确 park。
- `promising option` 仍算活跃工作，因为它不是终态结论。
- `done`、`failed`、`cancelled` experiment 不阻塞父节点关闭。
- `decision` 暂不作为 blocker；是否把 `proposed decision` 视为活跃下游，需要根据真实使用再决定。

## Desired User Flow

用户或 agent 在关闭父节点前先 dry-run：

```sh
research-cockpit update-status --root research_cockpit --id problem_x --status resolved --dry-run --json --show-diff
```

如果存在活跃下游，返回结构化失败：

```json
{
  "ok": false,
  "error": "terminal_parent_has_active_descendants",
  "node_id": "problem_x",
  "target_status": "resolved",
  "blocking_descendants": [
    {
      "id": "option_y",
      "type": "option",
      "status": "active",
      "path": ["problem_x", "option_y"]
    }
  ],
  "suggested_commands": [
    "research-cockpit close-branch --root research_cockpit --id problem_x --downstream-status parked --dry-run --json --show-diff"
  ]
}
```

如果确认不再推进该分支，使用显式关闭流程：

```sh
research-cockpit close-branch --root research_cockpit --id problem_x --downstream-status parked --dry-run --json --show-diff
research-cockpit close-branch --root research_cockpit --id problem_x --downstream-status parked --no-build
research-cockpit update-status --root research_cockpit --id problem_x --status resolved --no-build
```

`close-branch` 必须保守：先 dry-run，列出所有将被修改的 descendant，再允许 apply。

## Tech Stack

- Runtime code: `src/research_cockpit/`
- CLI: `research-cockpit` 和 `python -m research_cockpit.cli`
- Tests: `unittest`，测试目录为 `tests/`
- Truth source: 项目仓库内的 `research_cockpit/graph/nodes/*.yaml`
- UI: Streamlit dashboard，入口在 `src/research_cockpit/ui/`

## Commands

基础验证：

```sh
python -m unittest discover -s tests
python dev/scripts/run_skill_release_check.py --json --skip-mutating
```

示例 data root 验证：

```sh
research-cockpit validate --root examples/demo_research_cockpit
research-cockpit build --root examples/demo_research_cockpit
research-cockpit smoke --root examples/demo_research_cockpit --json
research-cockpit lint --root examples/demo_research_cockpit --semantic --json
```

候选 strict validation：

```sh
research-cockpit validate --root research_cockpit --strict-lifecycle --json
```

候选分支关闭命令：

```sh
research-cockpit close-branch --root research_cockpit --id <problem_or_option_id> --downstream-status parked --dry-run --json --show-diff
research-cockpit close-branch --root research_cockpit --id <problem_or_option_id> --downstream-status parked --no-build
```

## Project Structure

建议实现位置：

- `src/research_cockpit/lifecycle_guards.py`: 共享 lifecycle guard 逻辑。
- `src/research_cockpit/model.py` / `graph_core.py`: 复用现有 topology，不重复写遍历。
- `src/research_cockpit/commands/lint_semantic.py`: 第一阶段 warning。
- `src/research_cockpit/commands/update_status.py`: 直接状态变更 guard。
- `src/research_cockpit/commands/finalize_workstream.py`: 工作流 finalization guard。
- `src/research_cockpit/commands/apply_graph_plan.py`: 批量状态变更 guard。
- `src/research_cockpit/commands/close_branch.py`: 候选安全批量关闭命令。
- `tests/test_model.py` / `tests/test_scripts.py`: helper 和 CLI 覆盖。
- `capabilities/node-management.md`、`capabilities/troubleshooting.md`: agent-facing 说明。

## Code Style

核心逻辑应是纯函数，不直接写 YAML。命令层负责 JSON 格式、diff 和 mutation。

示例结构：

```python
def active_descendant_blockers(
    nodes: dict[str, ResearchNode],
    parent_id: str,
    target_status: str,
) -> list[dict[str, object]]:
    parent = nodes[parent_id]
    if target_status not in PARENT_TERMINAL_STATUSES.get(parent.type, set()):
        return []

    topology = GraphTopology.from_nodes(nodes)
    blockers = []
    for descendant_id in topology.descendants(parent_id):
        descendant = nodes[descendant_id]
        active_statuses = ACTIVE_DOWNSTREAM_STATUSES.get(descendant.type, set())
        if descendant.status in active_statuses:
            blockers.append({
                "id": descendant.id,
                "type": descendant.type,
                "status": descendant.status,
                "path": topology.safe_path(descendant.id),
            })
    return blockers
```

约定：

- lifecycle 常量集中定义。
- guard helper 返回结构化数据，不抛命令层文案。
- 不在每个 command 中各写一套递归。
- 不在 guard helper 里执行 mutation。

## Testing Strategy

### Unit Tests

覆盖：

- 直接 active child 阻止 terminal parent。
- 嵌套 active descendant 阻止 terminal ancestor。
- terminal experiment descendant 不阻止 parent closure。
- `blocked problem` 和 `promising option` 是 blocker。
- 没有 active downstream work 的 `accepted option` 可以通过。
- 缺失 parent 和 cycle 行为沿用现有 topology validation。

### CLI Tests

覆盖：

- `lint --semantic --json` 报告 `terminal_parent_has_active_descendants`。
- `update-status --dry-run --status resolved` 在存在 blocker 时返回 guard failure。
- `finalize-workstream` 不允许 option 仍有活跃下游时进入 `accepted` / `rejected`。
- `apply-graph-plan --dry-run` 捕捉 batch input 中的非法 terminal transition。
- `close-branch --dry-run --show-diff` 列出影响节点但不写文件。
- `close-branch --no-build` 只更新符合规则的 active downstream descendants。

### Compatibility Tests

现有 demo data 必须继续通过普通验证和 build：

```sh
research-cockpit validate --root examples/demo_research_cockpit
research-cockpit build --root examples/demo_research_cockpit
research-cockpit smoke --root examples/demo_research_cockpit --json
```

## Boundaries

Always:

- 先 semantic lint warning 和 opt-in strict validation，再考虑默认 hard validation。
- 所有 mutating command 保留 `--dry-run` / `--show-diff`。
- JSON 输出必须包含 blocker id、status、type、path。
- 复用现有 topology helper。

Ask first:

- 新增 YAML 字段，例如 `allow_active_descendants`。
- 把 lifecycle strictness 改成默认 `validate` 行为。
- 把 `proposed decision` 纳入 active downstream blocker。
- 在 `close-branch` 以外做自动批量状态改写。

Never:

- 关闭父节点时静默修改 descendant 状态。
- 用 `accepted` / `resolved` 掩盖仍在推进的 active branch。
- 只在 UI 中隐藏 lifecycle 问题，CLI 和 agent context 必须能读到。
- 没有迁移路径就让历史仓库默认 validate 失败。

## Implementation Plan

### Phase 1: Shared Guard and Semantic Warning

新增共享 guard helper，并接入 semantic lint。

Acceptance:

- `lint --semantic --json` 报告 terminal parent 下的 active downstream blockers。
- warning 包含 parent id、parent status、descendant id、descendant status、path。
- 普通 `validate` 行为不变。

Verify:

```sh
python -m unittest tests.test_model tests.test_scripts
research-cockpit lint --root examples/demo_research_cockpit --semantic --json
```

### Phase 2: Opt-In Strict Validation

增加 `validate --strict-lifecycle`。

Acceptance:

- strict mode 在存在 blockers 时失败。
- normal mode 保持兼容。
- error 使用和 semantic lint 一致的结构化 guard detail。

Verify:

```sh
python -m unittest tests.test_model tests.test_scripts
research-cockpit validate --root examples/demo_research_cockpit --strict-lifecycle --json
```

### Phase 3: Guard Mutating Status Commands

把 guard 接入实际会改变父节点 terminal 状态的命令。

候选命令：

- `update-status`
- `finalize-workstream`
- `apply-graph-plan`
- `accept-decision`，仅当它间接改变 parent option/problem lifecycle 时才纳入。

Acceptance:

- dry-run 在 mutation 前展示 guard failure。
- 非 dry-run 拒绝非法 terminal transition。
- 不改变父节点到 terminal 状态的命令不受影响。

Verify:

```sh
python -m unittest tests.test_scripts
python dev/scripts/run_skill_release_check.py --json --skip-mutating
```

### Phase 4: Safe Branch Closing Workflow

新增保守的 `close-branch` 工作流，用于关闭父节点前处理活跃下游。

初始行为：

- 输入 root id 必须是 `problem` 或 `option`。
- `--downstream-status parked` 作为默认安全目标。
- option descendants 后续可支持 `paused|parked|rejected`，默认仍用 `parked`。
- `planned|queued|running` experiments 默认只 warning；只有显式 `--include-experiments` 才允许改为 `cancelled`，因为 running experiment 可能还涉及外部进程。

Acceptance:

- dry-run 按 node type 分组列出所有拟修改状态。
- 非 dry-run 只写 dry-run 列出的 eligible descendants。
- 支持 `--json`、`--compact`、`--no-build`、`--show-diff`。
- 成功后输出下一步 parent status command。

Verify:

```sh
python -m unittest tests.test_scripts
research-cockpit close-branch --root .test_tmp/sample/research_cockpit --id problem_x --downstream-status parked --dry-run --json --show-diff
```

### Phase 5: Documentation and Agent Guidance

实现后更新 agent-facing 文档。

Acceptance:

- `capabilities/node-management.md` 说明 terminal parent lifecycle guard。
- `capabilities/troubleshooting.md` 说明 semantic warning 和修复命令。
- `SKILL.md` 在 branch finalization guidance 中引用 `close-branch`。
- `research-cockpit commands --json --compact` 包含新命令 metadata。

Verify:

```sh
python dev/scripts/run_skill_release_check.py --json --skip-mutating
```

## Task Breakdown

- [x] Task 1: 添加 lifecycle guard helper。
  - Acceptance: helper 能识别 terminal parent transition 的 blockers。
  - Verify: `python -m unittest tests.test_model`
  - Files: `src/research_cockpit/lifecycle_guards.py`, `tests/test_model.py`

- [x] Task 2: 添加 semantic lint warning。
  - Acceptance: `lint --semantic --json` 报告 `terminal_parent_has_active_descendants`。
  - Verify: `python -m unittest tests.test_scripts`
  - Files: `src/research_cockpit/commands/lint_semantic.py`, `tests/test_scripts.py`

- [x] Task 3: 添加 `validate --strict-lifecycle`。
  - Acceptance: strict mode fail，normal mode 兼容。
  - Verify: `python -m unittest tests.test_model tests.test_scripts`
  - Files: `src/research_cockpit/model.py`, `src/research_cockpit/cli.py`, `tests/test_model.py`, `tests/test_scripts.py`

- [x] Task 4: 接入 status-changing commands。
  - Acceptance: 有 blockers 时 terminal parent transition 在写入前失败。
  - Verify: `python -m unittest tests.test_scripts`
  - Files: `src/research_cockpit/commands/update_status.py`, `src/research_cockpit/commands/finalize_workstream.py`, `src/research_cockpit/commands/apply_graph_plan.py`, `src/research_cockpit/commands/accept_decision.py`, `src/research_cockpit/commands/promote_decision.py`, `src/research_cockpit/commands/create_workstream.py`, `tests/test_scripts.py`

- [x] Task 5: 添加 `close-branch` dry-run。
  - Acceptance: dry-run 列出拟修改 descendants 和下一步命令。
  - Verify: `python -m unittest tests.test_scripts`
  - Files: `src/research_cockpit/commands/close_branch.py`, `src/research_cockpit/cli.py`, `tests/test_scripts.py`

- [x] Task 6: 添加 `close-branch` mutation。
  - Acceptance: 只修改 eligible descendants，支持 `--no-build` 和 compact JSON。
  - Verify: `python -m unittest tests.test_scripts`
  - Files: `src/research_cockpit/commands/close_branch.py`, `tests/test_scripts.py`

- [x] Task 7: 更新 docs 和 command discovery。
  - Acceptance: agent-facing docs 和 commands manifest 都能指导后续 agent 使用。
  - Verify: `python dev/scripts/run_skill_release_check.py --json --skip-mutating`
  - Files: `SKILL.md`, `capabilities/node-management.md`, `capabilities/troubleshooting.md`, command manifest code/tests

## Success Criteria

- agent 不能误把仍有活跃下游的 branch 标为终态。
- 研究者可以通过 dry-run 审计并显式停止一条分支。
- 历史仓库不会在未 opt-in 的情况下被破坏。
- lifecycle guard 输出可被下游 agent 机械解析。
- 实现完成后 full test suite 和 release check 通过。

## Open Questions

- `accepted option` 如果作为 baseline 派生出 active child problem，是否允许保留 active descendant？
- `proposed decision` 是否应该算 active downstream blocker？
- `validate --strict-lifecycle` 未来是否应成为默认 `validate` 行为？

## Resolved Decisions

- `close-branch` 默认不修改 `planned|queued|running` experiments。只有调用者确认外部 run/job 已停止或明确放弃后，才允许用 `--include-experiments` 将这些 active experiments 改为 `cancelled`。
