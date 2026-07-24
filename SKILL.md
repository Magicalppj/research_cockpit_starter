---
name: research-cockpit
description: Manage bounded multi-agent research assignments, evidence, review, decisions, and milestone handoffs.
---

# Research Cockpit

Research Cockpit 的默认 ownership 单位是一个 independently owned stage workstream；assignment-scoped Work Packet 只承载需要独立 owner 或 durable handoff 的阶段工作，而不是每次操作。先选择一个角色，只读取对应 playbook，然后执行一条 startup path。

## Role Router

- Worker: 读取 `capabilities/worker-loop.md`。
- Reviewer: 读取 `capabilities/reviewer-loop.md`。
- Coordinator: 读取 `capabilities/coordinator-loop.md`。
- Maintainer: 读取 `capabilities/maintainer-loop.md`；仅在执行具体动作时再读 `capabilities/maintenance.md`。

## Startup

Worker：

```sh
research-cockpit work open --root <data-root> --assignment <assignment_id> --json --compact
```

Reviewer：

```sh
research-cockpit review open --root <data-root> --assignment <review_assignment_id> --json --compact
```

Coordinator：

```sh
research-cockpit coord overview --root <data-root> --json --compact --limit 20
```

已知 node、但没有 assignment 时，使用 `context --view execution --compact`。不要把 role startup 与其他 broad context 读取串联。

## Invariants

1. Worker mutation 必须绑定 assignment，并使用 packet 中的 agent、lease、epoch 和 input revision；`work start` 还必须显式使用 packet `cursor.current_node` 对应的 experiment id。
2. 每个 mutating facade 请求使用稳定 `operation_id`；只对完全相同的请求复用它。
3. `work start` 原子创建 run 并启动 experiment；`work close` 原子提交 run、finding、result、cursor、lease 和 optional evidence。
4. `work record` 只用于 close 前必须 durable 的增量 evidence。
5. Coordinator 创建 experiment/review assignment；reviewer 不修改 producer truth，coordinator 负责应用 review、decision 和 baseline。
6. 成功 receipt 已 internal-verified 时立即停止，不追加 validate/context/build/smoke。
7. Dashboard 是 projection；artifact payload 与 provenance 不能被 projection 替代。
8. 不直接编辑 interaction backend，也不通过通用 YAML patch 绕过 domain validation。

## Progressive Disclosure

缺少局部 context 时运行 bounded `search --limit 5`。缺少一个 operation contract 时运行：

```sh
research-cockpit commands --role <role> --name <command> --json --compact
```

不要在已知 role/assignment 的普通路径加载完整 command catalog、完整 graph、artifact payload 或其他角色 playbook。

## Version Boundary

Public CLI 只维护 0.3.0 canonical role surface，不提供旧 route alias。Legacy project data 和 artifact 继续读写兼容；迁移说明位于 `docs/migrations/0.3.0-cli-cutover.md`。
