# Reviewer Loop

## Open

Reviewer 不创建自己的 assignment。Coordinator 在 producer close 且 review 状态为 pending 后，使用 `research-cockpit coord assign --print-schema --action review_session` 获取 input，并以 `producer_assignment_id` 创建 review session。

Reviewer 收到 `review_assignment_id` 后只执行：

```sh
research-cockpit review open --root <data-root> --assignment <review_assignment_id> --json --compact
```

Review Packet 已包含 producer result revision、bounded Evidence Bundle、acceptance criteria、changed files 与验证摘要。不要再打开 producer worker packet，也不要修改 producer assignment、run、node 或 artifact record。

## Review

检查：

- result 是否满足 objective、success criteria 和 input revision。
- evidence refs、artifact provenance、tests 与 changed files 是否一致。
- conclusion 是否超出 evidence strength。
- proposal 是否属于 same-scope follow-up 或 new branch。
- 是否存在 correctness、scope、rollback 或 data compatibility 风险。

## Report

```sh
research-cockpit review report --root <data-root> --assignment <review_assignment_id> --file review.yaml --json --compact
```

`review_report_v1` 写入 reviewer assignment 自己的 verdict、summary、findings、inspected evidence 和 validation performed。每次请求使用稳定 `operation_id`，并绑定 packet 的 producer/result revisions。

Reviewer report 不重写 producer Evidence Bundle。Coordinator 通过 `coord review` 的 `assignment_result` action 将 verdict metadata 应用到 producer assignment；artifact promotion 使用独立的 `promote_artifact` action，decision 或 baseline 仍由 `coord decide` 独立处理。
