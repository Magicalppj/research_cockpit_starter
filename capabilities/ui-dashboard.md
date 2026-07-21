# UI Dashboard

```sh
research-cockpit build --root <data-root>
research-cockpit ui --root <data-root> --server.port 8501
```

UI 读取 generated dashboard JSON 以获得稳定刷新速度。Projection 缺失、损坏或比 truth source 旧时，会现场构建并显示 stale warning；这不改变 truth。

大 root 可由 coordinator 在 canonical worktree 中运行 build watch。Worker 不应在每次 mutation 后 rebuild。React bundle 只在前端代码变化时重新构建，研究数据变化只需刷新 projection。

Graph selection 是 coordinator/UI state，不是 worker cursor。Worker assignment 始终以 Work Packet 为准。
