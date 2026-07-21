# Graph State And Projection

`graph/nodes/*.yaml` 保存持久 research knowledge；assignment、run、gate、artifact record 和 interaction event 保存 activity/provenance。`dashboards/` 是可重建 projection。

Coordinator graph mutation 使用 `coord assign` 的 `graph_plan` action。Worker 不通过 generic graph mutation 扩大 assignment scope。

## Shape And Status

新研究方向的 canonical hierarchy 是 `stage -> problem -> option -> experiment`，decision 通常是 option 的子节点。由已有 option 派生的新分支可以使用 `option -> problem -> option -> experiment/decision`；不要为了满足固定深度复制同一 research concept。

| Type | Valid status |
| --- | --- |
| `stage` | `planned`, `active`, `blocked`, `done` |
| `problem` | `open`, `active`, `blocked`, `resolved`, `parked` |
| `option` | `open`, `active`, `promising`, `rejected`, `accepted`, `paused`, `parked` |
| `experiment` | `planned`, `queued`, `running`, `done`, `failed`, `cancelled` |
| `decision` | `proposed`, `accepted`, `superseded`, `rejected` |
| `artifact` | `draft`, `planned`, `active`, `done`, `superseded`, `deprecated`, `archived` |

`coord assign --print-schema --action graph_plan` 返回可直接编辑的最小合法 hierarchy example。状态迁移仍受 node lifecycle 与 decision facade 约束；合法枚举不代表任意跳转都可执行。

## Diagnostics

```sh
research-cockpit validate --root <data-root> --json
research-cockpit validate --root <data-root> --changed-node <node_id> --json
research-cockpit build --root <data-root> --json --profile
```

Manual YAML edit 后使用 changed-scope validation。Role-facade internal verification 成功后不重复运行。

Interaction history 在 segmented manifest 激活后由 immutable legacy prefix 与 JSONL generations 组成。不要直接编辑任一 backend；使用 `maintenance repair` 或 `maintenance migrate` 的 reviewed plan。

Mutation planning 在锁外进行；canonical root 只在短 transaction commit 期间串行。Conflict 必须 reread bounded packet/snapshot 后以新请求处理，不能覆盖他人结果。
