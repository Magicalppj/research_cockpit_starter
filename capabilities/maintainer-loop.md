# Maintainer Loop

Maintainer 处理 repository/data-root health，不参与普通 assignment execution。

## Audit First

```sh
research-cockpit maintenance audit --root <data-root> --repo <repo-root> --json --compact
```

Audit 汇总 active assignments/runs、worktree/branch candidates、large artifact candidates 与 dashboard performance warnings。只选择一个具体问题继续，不在一个 mutation 中混合多类维护。

## Execute One Action

```sh
research-cockpit maintenance repair --print-schema
research-cockpit maintenance migrate --print-schema
research-cockpit maintenance compact --print-schema
```

结构化输入统一为 `maintenance_action_v1`，默认 `execute: false`。先检查 dry-run result 与 diff，再将同一计划改为 `execute: true`。具体 action 和 safety boundary 见 `capabilities/maintenance.md`。

Maintenance route 不承诺 operation-id idempotency；因此执行前必须确认 root、target 和 plan 未变化。执行后只运行 result 指定的 bounded verification。
