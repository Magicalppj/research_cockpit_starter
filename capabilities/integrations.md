# Installation, Worktrees, And UI

## Initialize

```sh
python -m pip install -e .
research-cockpit init --root research_cockpit --json
```

不要在 agent worktree 中初始化第二个 research data root。所有 worktree 共享 canonical `research_cockpit/` truth source。

## Worktree Session

Coordinator 使用 `coord assign` 的 `session` action，显式提供 option、objective、branch、worktree、agent id 和 assignment id。Relative worktree path 以 input file 目录解析；不要提交 machine-specific absolute path。

Git worktree 只隔离代码与实验过程。Research mutation 仍通过 canonical root 的 assignment lease、scope 和 transaction。

正常 mutation 会 piggyback lease renewal。Bundled launcher 与 `progress.json` 只维护执行进度，不会自动调用 lease heartbeat；长时间无 mutation 的任务需在 start contract 中设置足够的 `lease_seconds`，或由外部 runtime 明确安排 `work renew`，无需让 agent 在模型回合中周期性续租。

## UI

```sh
research-cockpit build --root research_cockpit
research-cockpit ui --root research_cockpit --server.port 8501
```

UI 是 researcher/coordinator surface，不是 worker startup dependency。远程服务器端口只影响网络传输，不改变 truth transaction；大图性能应通过 generated projection 与前端 profiling 分析。
