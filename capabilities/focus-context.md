# Bounded Context

## Preferred Reads

已知 worker assignment：`work open`。

已知 review assignment：`review open`。

全局 assignment triage：

```sh
research-cockpit coord overview --root <data-root> --json --compact --limit 20
```

已知 node 但无 assignment：

```sh
research-cockpit context --root <data-root> --id <node_id> --view execution --json --compact
```

`context` 支持 revision polling；已知 node 不应再追加 broad search 或 dashboard context pack。

`node.parent` 是目标的直接 graph 父级。Experiment session 的 `option_id` 使用 `node.parent.id`；`effective_baseline.option` 表示比较基线，不保证是结构父级。

## Search

```sh
research-cockpit search --root <data-root> --query "keyword" --source node --limit 5 --json
```

保持 source 与 limit 有界。Artifact payload、完整 interaction history 和无关 runs 不进入默认 model context。

## Command Discovery

```sh
research-cockpit commands --role <role> --name <command> --json --compact
```

只有缺少一个 operation contract 时使用。普通 startup 不加载完整 manifest；不要根据 suggestion 自动执行 mutation。
