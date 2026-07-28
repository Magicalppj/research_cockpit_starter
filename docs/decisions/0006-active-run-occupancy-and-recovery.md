# ADR-0006: Active-run occupancy 使用 bounded projection 与提交期差量守卫

## Status

Accepted

## Date

2026-07-28

## Context

Worker 需要在 Work Packet 中直接识别可继续的 run，而 coordinator 与 `work start` 必须阻止同一 assignment 或 experiment 的第二个 active run。若只在 preflight 检查，会留下并发 race；若在全局 mutation lock 内再次调用 `load_runs()`，则 run 数量增长会延长所有 mutation 的锁占用。`coord assign` 还可能在 Git worktree 已创建后才发现提交冲突。

## Decision

- `queued`/`running` 且 `finished_at` 为空的 run 参与 occupancy；同一 assignment 或 experiment 最多一个 active run。
- Work Packet 返回 limit 5 的 `active_runs.assignment` 与 `active_runs.experiment`，每项包含 run identity、owner assignment、experiment 与 status。
- Work Packet 只在 run file set 与 size/mtime signature 仍匹配时使用 validation-index projection，否则回退 truth scan。
- Start/session preflight 在锁外读取完整 run truth，并记录文件 signature。提交期在锁内只解析 snapshot 后新增或变化的 run 文件，再检查目标 assignment/experiment；full validation 独立检查全局重复状态。
- `create_worktree: true` 后发生 domain conflict 时不删除 worktree。Structured receipt 标记 `partial_success: true`、说明 worktree side effect，并要求以相同 request 和 `operation_id` 重试。

## Alternatives Considered

### 在锁内重新解析全部 runs

语义直接，但锁占用随 run 数线性增长，并阻塞无关 mutation。拒绝。

### 只依赖 validation index

读取快，但 index 是 derived projection，不能作为提交冲突的唯一 truth。拒绝。

### 每个 assignment 使用独立锁

无法单独覆盖 experiment 跨 assignment 冲突，还会引入多锁排序与恢复复杂度。留待并发测量证明必要后另立 ADR。

## Consequences

- 常见提交路径仍需枚举 run filenames/signatures，但不在全局锁内解析未变化 YAML。
- 手工制造的既有重复 active runs 会使 full validation 失败，需先显式恢复 truth。
- Work Packet public contract 新增 required `active_runs` 字段；0.3.1 caller 必须按 bounded collection 处理。
- Worktree partial success 不是已提交 assignment；caller 不得换 operation id 重建同一 session。
