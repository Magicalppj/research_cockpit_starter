# Assignment, Run, And Evidence Tracking

## Truth Model

- Assignment 是 concurrency、scope、lease、dependency 与 review boundary。
- Run 保存一次 execution lifecycle；gate 保存结构化 criterion result。
- Finding 保存 bounded research conclusion。
- Artifact record 保存 location、ownership、integrity、inventory、retention、lifecycle、availability、links 与 provenance；payload 默认保持 external reference，只有显式 managed mode 才写入 configured external artifact root，`artifacts/` 仅保存 legacy payload。
- Assignment result 聚合 delivery、tests、evidence refs、proposal 和 result revision。

Worker 不分别管理这些文件。`work open` 读取 assignment packet，`work start` 创建 run，`work record` 提前持久化必要 evidence，`work close` 原子写入最终 bundle。完整流程见 `capabilities/worker-loop.md` 和 `capabilities/experiment-cycle.md`。

Coordinator 通过 `coord overview` 查看 ready/active/stale/review states，通过 `coord assign` 创建 packet，通过 `coord review` 应用 review metadata。不要从 coordinator UI focus 推断 worker cursor。

0.2.x run、gate、artifact record 和 assignment 在 canonical facade mutation 后必须保留 unknown fields、payload bytes 与 provenance refs；普通读写不触发全量迁移。
Final payload 使用 `work_close_v1.evidence_inputs`；默认 reference admission 不复制 bytes，只有显式 managed mode 才 copy/hash 到 external root。只有提前 durability 才使用 `work record`。成功 receipt 的 `additional_verification_required: false` 表示不再追加 validate、context、build 或 smoke。
Launcher-owned progress、gate 和 manifest 文件约定见 `docs/launcher-output-conventions.md`；它们只在 record/close 时进入 canonical truth。
