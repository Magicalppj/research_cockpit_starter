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

Launcher heartbeat 在模型上下文外运行；正常 mutation 也会 piggyback lease renewal。Agent 不需要周期性调用 renewal。

## UI

```sh
research-cockpit build --root research_cockpit
research-cockpit ui --root research_cockpit --server.port 8501
```

UI 是 researcher/coordinator surface，不是 worker startup dependency。远程服务器端口只影响网络传输，不改变 truth transaction；大图性能应通过 generated projection 与前端 profiling 分析。
