# Troubleshooting

## Validate First

```sh
research-cockpit validate --root <data-root> --json
research-cockpit validate --root <data-root> --strict-lifecycle --json
```

局部 manual edit 使用 `--changed-node`、`--changed-file` 或 `--changed-record`。不要在普通 assignment mutation 后默认 full validate。

## Interaction Health

获取 schema 并创建 dry-run plan：

```sh
research-cockpit maintenance repair --print-schema
research-cockpit maintenance repair --root <data-root> --file repair.yaml --json --compact
research-cockpit maintenance migrate --print-schema
research-cockpit maintenance migrate --root <data-root> --file migration.yaml --json --compact
```

Repair 处理 legacy document shape；migration 原子激活 segmented backend 并保留 legacy prefix。不要手改 active JSONL generation 或 manifest。

## Smoke

```sh
research-cockpit smoke --root <data-root> --scope changed --id <node_id> --json --progress
research-cockpit smoke --root <data-root> --full --json --progress
```

默认 root smoke 是 compact diagnostic；`--full` 只用于明确诊断 canonical subprocess workflow。Milestone gate 使用 `coord handoff`，不要预先重复 smoke/build/full validate。

## Recovery

- Operation timeout: exact request 重试同一 operation id。
- Request content changed: 使用新 operation id。
- Stale input: reopen bounded packet/snapshot。
- Lease conflict: 停止写入并交还 coordinator。
- Maintenance uncertainty: 保持 `execute: false` 并检查 target/diff。
