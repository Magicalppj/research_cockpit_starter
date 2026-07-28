# Command Interface

## Public Surface

0.3.x 只公开以下 role groups：

| Role | Actions |
| --- | --- |
| `work` | `open`, `claim`, `renew`, `release`, `start`, `record`, `close` |
| `review` | `open`, `report` |
| `coord` | `overview`, `assign`, `review`, `decide`, `handoff` |
| `maintenance` | `audit`, `repair`, `migrate`, `compact` |

保留的独立入口只有 `init`、`ui`、`validate`、`build`、`commands`、`smoke`、`search` 和 `context`。它们分别承担初始化/UI、诊断、bounded discovery 与 known-node read，不与 role mutation 重叠。

旧 command modules 可作为内部 domain adapter 存在，但不能出现在 parser、help、manifest、active docs 或 examples 中，也不能由 facade 通过 nested CLI subprocess 调用。

## Structured Inputs

复杂 mutation 使用 versioned YAML/JSON file：

- `work_start_v1`
- `work_record_v1`
- `work_close_v1`
- `review_report_v1`
- `coord_assign_v1`
- `coord_review_v1`
- `coord_decide_v1`
- `coord_handoff_v1`
- `maintenance_action_v1`

`work start`、`work record` 与 `work close` 使用 `--print-schema` 获取最小 example；`coord assign`、`coord review` 与 `coord decide` 使用 `--print-schema --action <selector>`。其他 file input 以 role capability 或 `templates/launcher/` 中的 example 为准。Mutation parser 拒绝 unknown input fields；legacy truth parser 则采用 additive round-trip，保留未知持久字段。Input 中的 relative filesystem path 以 input file directory 解析。

Coordinator action boundary 必须显式区分：

- `coord assign` input 只接受 `graph_plan` 和 `session`；schema selector `review_session` 生成 `action: session, kind: review`。Experiment session 必须提供 `experiment_id`，review session 必须提供 `producer_assignment_id`。
- `coord review` 接受带 `--assignment` 的 `assignment_result`，或不带该参数的 `promote_artifact`。
- `coord decide` 接受 `promote`、`refresh_evidence`、`update_checklist`、`accept` 和 `set_baseline`。
- Decision acceptance 是 read operation：`context --id <decision_id> --json --compact` 返回 `decision_acceptance`。

## Operation IDs

Worker、reviewer 与 coordinator mutation 必须携带稳定 `operation_id`。Canonical request 经 normalization 后计算 hash；同 id、同 hash 返回原 receipt，同 id、不同 hash 返回 `idempotency_conflict`，且不写 truth。

Canonical role workflow 对同一 assignment 或 experiment 同时最多允许一个 `queued`/`running` 且未结束的 run。Work Packet 的 `active_runs.assignment` 与 `active_runs.experiment` 是 limit 5 的 bounded references，每项包含 `run_id`、nullable `assignment_id`、`experiment_id` 与 `status`。存在本 assignment 的 active run 时只保留 `record` 与 `close`；仅目标 experiment 被其他 assignment 占用时不提供 mutation。直接重复 `work start` 返回 `active_run_blocks_start`，试图用 `coord assign` 替换 experiment session 返回 `active_run_blocks_session`。两者都在 `dependency_blockers.items` 返回 `active_run:<run_id>`，恢复动作指向该 experiment 的 execution context。

Operation receipt 与 mutation interaction event 在同一 transaction 写入。Derived operation index 只加速 lookup，不是 truth，也不创建 per-operation files。Maintenance action 是显式人工流程，默认 dry-run；只有 action contract 明确声明 stable `operation_id` 时才支持 exact retry。

## Storage Lifecycle

`work close` 与 `work record` 的 `evidence_inputs` 默认使用 reference admission：写入 URI、inventory、integrity 与 provenance，不复制 payload。`mode: managed` 必须显式指定，且要求通过 `storage.yaml` 或 `RESEARCH_COCKPIT_ARTIFACT_ROOT` 配置与 state root 分离的 external artifact root；新 write 不会落到 legacy `artifacts/`。

`maintenance migrate` 的 `action: artifact_storage` 迁移一个 legacy record，默认 dry-run；同一 stable `operation_id` 是中断后的 exact retry identity。`maintenance compact` 有两个互不替代的 action：

- `artifact`：demote 一个 graph artifact，永不删除 payload bytes。
- `artifact_gc`：对一个 verified Cockpit-managed record 执行 quarantine 或 delayed purge。dry-run 返回 `state_revision`；`execute: true` 必须提交该 revision 的 `expected_revision`，并使用稳定 `record_id`、`operation_id` 与 `phase`。

GC 与 migration 是 maintainer-only exceptional operations，不属于普通 worker 的三命令路径。完整 file examples、blocker 与恢复规则见 [0.3.1 storage boundaries](migrations/0.3.1-storage-boundaries-and-workstream-tracking.md)。

## Success Envelope

Role mutation 返回 bounded receipt，至少包含：

- operation、operation id 与 assignment scope。
- changed/entity refs/revision。
- allowed next operations 或 primary recovery action。
- `verification.status` 与 `additional_verification_required`。

`internally_verified` 且无需附加验证时，caller 必须停止，不做 read-after-write。Payload、完整 logs、full schema 与 command catalog 不进入 success envelope。

## Error Envelope

使用 `--json` 时，已解析的 role mutation input/file/domain 错误写入 stdout 的 structured envelope，stderr 保持为空；不要从 argparse 文本提取恢复动作。CLI token 本身无法解析时仍由 argparse 报 usage error。

可恢复错误包含 stable code、message、retry kind 和至多一个 primary retry command。常见 code：

- `idempotency_conflict`
- lease owner/epoch/expiry conflict
- `stale_inputs`
- dependency/review not ready
- scope overlap/out-of-scope write
- optimistic target conflict

`coord assign` 在 `create_worktree: true` 后遇到提交期 conflict（包括 active-run race）时返回 `partial_success: true`：worktree 已创建或复用，但 truth 未提交。Caller 解决 blocker 后必须以完全相同的 request 与 `operation_id` 重试；不要创建第二个 worktree 或换 id。

Exact transport retry 复用 operation id；truth、lease、input 或 request 发生变化时使用新 id。

## Verification Boundary

- Assignment mutation：domain validation + transaction recheck + internal verification。
- Manual YAML edit：changed-scope `validate`，必要时 bounded `context`。
- Milestone：单次 `coord handoff` 复用 one full validation state 完成 build/smoke/blocker check。
- Standalone full validate/build/smoke：只用于诊断。

## Compatibility

CLI compatibility 与 data compatibility 分离：0.3.x 不保留旧 route alias，但 0.2.x nodes、agents、assignments、runs、gates、artifact records/manifests、payload bytes 和 interaction history 可继续读写。普通 read/mutation 不强制整体迁移；显式 migration 必须先 dry-run 并保留 source evidence。
