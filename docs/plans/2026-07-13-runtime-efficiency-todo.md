# Research Cockpit Runtime Efficiency TODO

## 结论

当前最值得优化的仓库代码是 interaction log 的全量解析/重写、mutation 与 compact context 的重复全量状态加载、非 node changed-file 的索引回退，以及未真正有界的 JSON 输出；这些问题均可在现有实现中定位，优先级高于引入 daemon 或泛化缓存。

## 状态

Implemented。T0-T11 已完成；本文保留初始证据、任务拆分、验收标准和最终验证记录。

## 初始输入与证据边界

外部反馈来自约 4,953 个 graph nodes、32 万行 `graph/interaction_log.yaml` 的长期运行仓库。反馈中的 wall time 是真实观察，但不是可重复 benchmark。下表记录实现前的基线证据与当时判断；当前行为和完成状态以任务勾选、checkpoints 及实现代码为准。

- **已确认**：当前代码路径直接证明存在对应开销或契约错误。
- **部分确认**：已有实现降低了一部分成本，但仍有明确缺口。
- **待测假设**：方向合理，但必须先 profile，不能直接作为重构依据。

| 反馈项 | 初始仓库证据 | 初始判断 | 优先级 |
| --- | --- | --- | --- |
| 简单 mutation 固定成本高 | `mutation_runtime.load_validated_state()` 全量 `load_nodes()` 并执行 full validation；同一 mutation 还会多次严格加载 interaction log | 已确认 | P0 |
| interaction log 随历史增长拖慢写入 | `interaction_log.append_interaction_log()` 读取全部 events、追加一项，再用 YAML 重写整份文件 | 已确认 | P0 |
| compact 输出仍过大 | `validate` 没有 `--compact`；incremental payload 默认返回完整 affected ids；`context --compact` 仍保留未统一设限的关联集合 | 已确认 | P0 |
| command metadata 与 parser 不一致 | `commands --name record-finding` 声明支持 `--compact`，但 `record-finding --help` 和 parser 均没有该 flag | 已确认、正确性缺陷 | P0 |
| run/gate changed-file 经常 fallback | validation index 已存 runs、assignments、artifact records，但 indexed path 显式对 non-node changed file 返回 fallback；gate YAML/JSON 未纳入 index | 已确认 | P1 |
| compact context 接近 25 秒 | `context` 会 full load/full validate，再执行 semantic lint；嵌套 node context 和 bootstrap 路径存在重复状态或 interaction log 读取 | 部分确认，具体占比待 profile | P1 |
| 实验收尾命令过多 | 已有批量 mutation 基础设施，但没有一次提交 run、gates、artifact record、finding 和 next action 的事务型 closeout | 已确认 | P1 |
| 长命令无阶段反馈 | 初始实现只有 `smoke --progress`；现已增加通用 stderr JSON phase reporter 并覆盖主要读写命令 | 已解决 | P2 |
| artifact record 默认选择有认知成本 | 初始实现默认创建 graph artifact；0.2.0 已切换为 record-first，graph artifact 必须显式 promotion 并记录 reason | 已解决 | P2 |
| 跨命令复用 Python 内存缓存 | CLI 每次是独立进程，普通进程内 cache 无法跨调用复用 | 方案前提不成立 | 不采用 |

## 目标与不变量

### 目标

- mutation 延迟不再随 interaction history 总长度线性增长。
- known-node compact context 和 changed-scope validation 只读取完成结果所需的 truth files。
- compact success payload 大小有硬上界，错误、warning 和 fallback reason 不丢失。
- 高频 run closeout 在一次锁持有和一次状态规划中完成。
- command discovery、`--help` 和 parser 的公开 flags 保持一致。

### 不变量

- 保留 file-based truth source，不引入必须常驻的 daemon 或数据库。
- 保留 mutation lock、stale-write detection、assignment scope、lifecycle guard、dry-run 和 show-diff。
- full validation/build/smoke 仍是 coordinator merge、release 和 final handoff 的权威 gate。
- generated index 只能加速读取；缺失、损坏或 stale 时必须安全 fallback，不能成为新的 truth source。
- 实现和测试必须跨 Windows、Linux、macOS；不得依赖 PowerShell/Bash 专用行为或平台特定文件锁语义。
- 普通 CI 验证复杂度和输出上界，严格 wall-time 预算放在 opt-in benchmark，避免慢机器产生脆弱失败。

## 暂定性能预算

以下目标沿用实际使用反馈，Phase 0 完成后根据同一 fixture 的 baseline 修订：

| 操作 | 目标 |
| --- | ---: |
| known-node compact context，warm P95 | <= 2 秒 |
| 单节点或单 run metadata mutation，warm P95 | <= 3 秒 |
| fresh changed-scope validation，warm P95 | <= 3 秒 |
| affected index refresh | <= 5 秒 |
| run closeout，含 2 个 gates、1 个 artifact record、1 个 finding | <= 8 秒 |
| compact success stdout | < 20 KB |

Cold 与 warm 数据必须分别报告。任何性能改动都要保留 before/after 数据，并验证语义等价。

## 依赖顺序

```text
T0 baseline/profile
  ├─ T1 CLI contract consistency
  ├─ T2 bounded compact contracts
  ├─ T3 interaction event store
  │    ├─ T4 shared snapshot and compact read path
  │    └─ T5 indexed mutation preflight
  ├─ T6 validation index v2
  │    └─ T5 indexed mutation preflight
  └─ T7 transaction primitive
       └─ T8 run closeout

T9 progress reporting can follow T0 instrumentation.
T10 artifact default migration follows T1 and T8.
T11 documentation/release checks close every phase.
```

## TODO

### P0: 建立可重复证据

#### [x] T0.1 扩展大型 fixture

**目的：** 让 fixture 同时覆盖 5,000 nodes、4,000 artifact records、runs/gates/assignments 和约 30 万条 interaction events；当前 generator 把 interaction log 初始化为空，无法复现最主要的增长维度。

**验收条件：**

- fixture 参数可独立控制 node、artifact record、run、gate 和 interaction event 数量。
- 同一 seed 生成确定性数据，完整 root 能通过 full validation。
- 生成过程不写入 canonical demo 或仓库跟踪目录。

**验证：**

```sh
python dev/scripts/generate_large_cockpit_fixture.py --root .test_tmp/runtime_5000 --nodes 5000 --artifacts 4000 --interaction-events 300000 --force
research-cockpit validate --root .test_tmp/runtime_5000 --json
```

**依赖：** 无。

**可能涉及：** `dev/scripts/generate_large_cockpit_fixture.py`、`tests/test_scripts.py`。

**规模：** M。

#### [x] T0.2 新增 runtime benchmark 与阶段 profile

**目的：** 分别测量 context、node-context、mutation、changed validation、index refresh、interaction append 和 closeout，避免继续用以 dashboard build 为中心的 benchmark 推断 runtime 热点。

**验收条件：**

- 每个操作报告 cold/warm wall time、CPU time、stdout/stderr bytes、读写文件数量和写入 bytes。
- 内部 profile 至少区分 graph load、full validation、semantic lint、sidecar scans、interaction log load/validate/append、lock wait 和 serialization。
- benchmark 使用 Python API/CLI，兼容三个目标平台；peak RSS 仅在可移植采集器可用时报告，不作为必填字段。
- 输出包含机器与 Python 版本，但不提交本机绝对路径或 benchmark 结果。

**验证：**

```sh
python dev/scripts/benchmark_runtime.py --root .test_tmp/runtime_5000 --cold-runs 10 --warm-runs 30 --json
python -B -m unittest tests.test_scripts -k benchmark_runtime
```

**依赖：** T0.1。

**可能涉及：** `dev/scripts/benchmark_runtime.py`、`src/research_cockpit/mutation_runtime.py`、`src/research_cockpit/commands/context.py`、`tests/test_scripts.py`。

**规模：** M。

### P0: 修复公开契约和输出成本

#### [x] T1 修复 command registry/parser 漂移

**目的：** 先修复 `record-finding --compact` 的可复现错误，再建立覆盖所有公开 commands 的自动一致性检查。第一步不要求一次性用 declarative schema 重写全部 argparse parser。

**验收条件：**

- manifest 的每个 `supported_flags` 都出现在对应 `--help` 并能被 parser 识别。
- manifest 不再声明 parser 未实现的 `--compact`、`--dry-run`、`--no-build` 或 `--show-diff`。
- `record-finding --json --compact` 输出有效的 compact mutation envelope。
- 新命令若未通过 registry/help/parser contract test，不能进入 release check。

**验证：**

```sh
python -B -m unittest tests.test_scripts -k command_flag_contract
python -m research_cockpit.cli commands --json --compact --name record-finding
python -m research_cockpit.cli record-finding --help
```

**依赖：** T0.2 提供输出基线，但正确性修复可先行。

**可能涉及：** `src/research_cockpit/commands/list_agent_commands.py`、`src/research_cockpit/commands/record_finding.py`、`src/research_cockpit/commands/_runtime.py`、`tests/test_scripts.py`。

**规模：** M。

#### [x] T2 定义真正有界的 compact schema

**目的：** 将 compact 从“少一些字段”提升为稳定、有大小上界的公共 contract。

**验收条件：**

- `validate --compact` 默认只返回 changed/affected counts、checks、errors、warnings、fallback 和 index 状态。
- 完整 affected ids 仅通过 `--include-affected-ids` 或显式输出文件返回。
- `context` 与 `node-context` 发布新的 compact schema version；所有列表均有固定 limit、总数和 omitted count。
- compact context 保留 target、parent problem/option、assignment cursor、success criteria/metrics 摘要、latest findings、key artifacts、first next action 和 context boundary warning。
- 5k fixture 的成功 payload 小于 20 KB；error、warning、fallback reason 不因 compact 丢失。
- `--compact` 同时使用无缩进 JSON serialization。

**验证：**

```sh
python -B -m unittest tests.test_scripts -k compact_contract
python dev/scripts/benchmark_runtime.py --root .test_tmp/runtime_5000 --operation validate_changed --operation context_compact --json
```

**依赖：** T0.1、T1。

**可能涉及：** `src/research_cockpit/commands/validate_cockpit.py`、`src/research_cockpit/commands/context.py`、`src/research_cockpit/commands/node_context.py`、`src/research_cockpit/node_onboarding.py`、`src/research_cockpit/commands/_runtime.py`、`src/research_cockpit/commands/list_agent_commands.py`、`tests/test_scripts.py`。

**规模：** 拆为两个 M：validate contract；context contracts。

### P0: 解除 interaction history 的线性写放大

#### [x] T3.1 引入 append-only interaction event backend

**目的：** 用可流式校验、可读取尾部、按 UTC 日期或大小分片的 JSONL event segments 替代“每次读取并重写整份 YAML”的写路径。

**验收条件：**

- 新 event append 在 mutation lock 内完成，追加成本不随历史 event 总数线性增长。
- writer 使用 flush 和 fsync；异常或截断行可被严格校验发现，不能静默跳过。
- reader 支持 streaming full validation 和 bounded tail read，不必把全部 events 放入内存。
- 分片命名、轮转和排序规则不依赖平台文件系统特性。
- legacy `graph/interaction_log.yaml` 在迁移期保持只读兼容。

**验证：**

```sh
python -B -m unittest tests.test_model -k interaction
python -B -m unittest tests.test_scripts -k interaction
python dev/scripts/benchmark_runtime.py --root .test_tmp/runtime_5000 --operation interaction_append --json
```

**依赖：** T0.2。

**可能涉及：** `src/research_cockpit/interaction_log.py`、`src/research_cockpit/mutation_runtime.py`、`src/research_cockpit/paths.py`、`tests/test_model.py`、`tests/test_scripts.py`。

**规模：** M。

#### [x] T3.2 增加 dry-run first 的 interaction log migration

**目的：** 将 legacy YAML events 安全迁移到新分片格式，并保留可审计、可回滚的兼容路径。

**验收条件：**

- migration dry-run 报告 event count、目标 segments、重复 ids、顺序异常和 checksum，不写文件。
- execute 使用 staging directory，验证 count/order/content 后再原子切换 active format。
- migration 不删除 legacy YAML；只有显式 archive 操作可以移动它。
- 新旧 reader 对相同历史返回相同 event 顺序和 recent tail。

**验证：**

```sh
research-cockpit migrate-interaction-log --root .test_tmp/runtime_5000 --dry-run --json
python -B -m unittest tests.test_scripts -k migrate_interaction_log
```

**依赖：** T3.1。

**可能涉及：** `src/research_cockpit/commands/migrate_interaction_log.py`、`src/research_cockpit/command_registry.py`、`src/research_cockpit/commands/list_agent_commands.py`、`tests/test_scripts.py`。

**规模：** M。

#### [x] T3.3 消除同一命令中的重复 log 解析

**目的：** mutation preflight、lock 内 recheck、append 和 compact context 共用 event-store metadata/tail API，不再重复 full parse。

**验收条件：**

- 普通 mutation 不读取 legacy/full event history；只验证 active append target 和必要 metadata。
- `recent_interactions(limit=N)` 只读取足以返回 N 条记录的尾部 segments。
- context/node-context 不再为 recent events 和 warnings 分别解析同一历史。
- loader-count tests 固定最大调用次数，防止回归。

**验证：**

```sh
python -B -m unittest tests.test_scripts -k interaction_loader_count
python dev/scripts/benchmark_runtime.py --root .test_tmp/runtime_5000 --operation mutation --operation context_compact --json
```

**依赖：** T3.1。

**可能涉及：** `src/research_cockpit/mutation_runtime.py`、`src/research_cockpit/node_onboarding.py`、`src/research_cockpit/commands/node_context.py`、`src/research_cockpit/commands/context.py`。

**规模：** S-M。

### P1: 缩小 graph 与 sidecar 读取范围

#### [x] T4 复用单次 root snapshot 并建立 compact read fast path

**目的：** 先消除一次进程内的重复 full load，再让 known-node compact context 使用 validation/topology index 只加载 target、ancestor 和有界关联记录。

**验收条件：**

- `context --with-bootstrap` 在同一进程中复用 nodes/current/edges，不再次 full load/full validate。
- compact path 不默认执行全局 semantic lint；改为 bounded local warnings 或读取 fresh generated summary，并明确 freshness。
- index fresh 时，未关联 node YAML 不被逐个解析；index stale 时返回明确 fallback 信息。
- full context 保持现有完整语义，compact schema 遵守 T2。

**验证：**

```sh
python -B -m unittest tests.test_scripts -k context_loader_count
python -B -m unittest tests.test_scripts -k context_compact
python dev/scripts/benchmark_runtime.py --root .test_tmp/runtime_5000 --operation context_compact --json
```

**依赖：** T2、T3.3、T6.1。

**可能涉及：** `src/research_cockpit/commands/context.py`、`src/research_cockpit/commands/node_context.py`、`src/research_cockpit/commands/agent_bootstrap.py`、`src/research_cockpit/node_onboarding.py`、`src/research_cockpit/validation_index.py`。

**规模：** 拆为两个 M：snapshot reuse；index fast path。

#### [x] T5 为高频 mutation 增加 indexed targeted preflight

**目的：** 避免每次修改一个 run 或 node 都通过 `load_validated_state()` 解析全部 graph 并执行 full validation。

**验收条件：**

- 提供 target-aware mutation state loader，按命令声明所需 node、ancestor、reverse refs、run、assignment、gate 和 artifact records。
- index fresh 时只解析目标及安全检查所需文件；index 缺失或 stale 时保守 full fallback。
- lock 内仍比较所有 planned truth files 的 before state/signature，保留 stale-write detection。
- 首批覆盖 `update-run`、`complete-run`、`record-finding`、`update-node-fields`、`ingest-gate-result` 和 record-only `ingest-artifact`。
- targeted 与 full preflight 对同一非法 mutation 给出等价拒绝结果。

**验证：**

```sh
python -B -m unittest tests.test_scripts -k targeted_preflight
python -B -m unittest tests.test_scripts -k mutation_conflict
python dev/scripts/benchmark_runtime.py --root .test_tmp/runtime_5000 --operation mutation --json
```

**依赖：** T0.2、T3.3、T6.1。

**可能涉及：** `src/research_cockpit/mutation_runtime.py`、`src/research_cockpit/validation_index.py`、上述高频 command modules、`tests/test_scripts.py`。

**规模：** 先做基础设施 M，再按 command 分多个 S。

#### [x] T6.1 发布 validation index v2

**目的：** 让 run、gate result、artifact record 和 assignment changed-files 真正进入 indexed incremental path。

**验收条件：**

- index 建立 `file -> entity -> experiment/node` 映射，覆盖 `runs/*.yaml`、`gate_results/*.yaml`、所引用的 gate JSON、`artifact_records/*.yaml` 和 `assignments/*.yaml`。
- 当前 `validation_index_non_node_changed_file` fallback 对已知实体不再发生。
- changed gate payload 能映射到 gate record、run、experiment 和 ancestor scope。
- incremental validation 与 full validation 对目标错误集合的判断一致。

**验证：**

```sh
python -B -m unittest tests.test_scripts -k validation_index
python -B -m unittest tests.test_scripts -k changed_gate
python -B -m unittest tests.test_scripts -k changed_run
```

**依赖：** T0.1、T2。

**可能涉及：** `src/research_cockpit/validation_index.py`、`src/research_cockpit/commands/validate_cockpit.py`、`src/research_cockpit/gate_result_records.py`、`src/research_cockpit/artifact_records.py`、`tests/test_scripts.py`。

**规模：** M。

#### [x] T6.2 增量维护 generated validation index

**目的：** worker mutation 后让 index 保持可用，避免下一次命令必须先运行 `build --affected`。

**验收条件：**

- mutation commit 后可按 changed files patch index；patch 失败不回滚 truth mutation，但必须标记 index stale 并给出恢复命令。
- index patch 不持有 mutation lock 执行昂贵 full rebuild。
- interrupted patch 通过临时文件和 atomic replace 保证 index 文件不会半写。
- `build --affected` 仍可作为显式恢复入口，但不再是普通 mutation 的必经步骤。

**验证：**

```sh
python -B -m unittest tests.test_scripts -k incremental_index_update
python dev/scripts/benchmark_runtime.py --root .test_tmp/runtime_5000 --operation mutation_then_validate --json
```

**依赖：** T6.1、T5 基础设施。

**可能涉及：** `src/research_cockpit/validation_index.py`、`src/research_cockpit/mutation_runtime.py`、`src/research_cockpit/commands/build_dashboard.py`。

**规模：** M。

### P1: 合并高频实验收尾事务

#### [x] T7 扩展通用 mutation transaction primitive

**目的：** 支持一次事务规划并提交多份 YAML/JSON/text、staged artifact payload 和一组 interaction events，为 run closeout 提供零部分写入保证。

**验收条件：**

- transaction 在写入前完成全部 schema、scope、reference、path 和 lifecycle preflight。
- lock 只获取一次；任一写入或 event append 失败时恢复所有 truth files，并清理 staged payload。
- transaction 结果明确区分 `changed`、`partial_success`、`rolled_back` 和 recovery commands。
- 保持现有单文件 `finish_mutation()` 调用兼容，逐步迁移而非一次重写所有 commands。

**验证：**

```sh
python -B -m unittest tests.test_scripts -k mutation_transaction
python -B -m unittest tests.test_scripts -k rollback
```

**依赖：** T3.1、T5 基础设施。

**可能涉及：** `src/research_cockpit/mutation_runtime.py`、`src/research_cockpit/storage.py`、`tests/test_scripts.py`。

**规模：** M。

#### [x] T8 扩展 `complete-run` 的结构化 closeout 模式

**目的：** 复用现有 `complete-run`/`run complete` 入口，避免再增加一个功能重叠的 top-level command；一次处理 run completion、artifact record、多个 gates、finding 和 next action。

**验收条件：**

- 新增 `complete-run --file <closeout.yaml>` 和 `--print-schema`，保留现有 flags 兼容。
- closeout schema 支持 run status、artifact record、多个 gate records/payload refs、finding、experiment/assignment next action。
- dry-run 返回完整 diff/plan，不写文件；执行时一次锁、一次 target state load。
- 任一步 preflight 失败时零 truth mutation、零 interaction event、零残留 artifact staging。
- 4 个并发 worker 压力测试不丢 event、不覆盖彼此文件；同一 target 冲突时一个成功、其他明确 stale failure。
- 5k/300k fixture 的 warm P95 目标 <= 8 秒。

**验证：**

```sh
research-cockpit complete-run --root .test_tmp/runtime_5000 --file closeout.yaml --dry-run --json --compact --show-diff
python -B -m unittest tests.test_scripts -k complete_run_closeout
python dev/scripts/benchmark_runtime.py --root .test_tmp/runtime_5000 --operation run_closeout --json
```

**依赖：** T1、T2、T6.1、T7。

**可能涉及：** `src/research_cockpit/commands/complete_run.py`、`src/research_cockpit/commands/file_schemas.py`、`src/research_cockpit/mutation_runtime.py`、run/gate/artifact/finding domain modules、`tests/test_scripts.py`。

**规模：** 拆为 schema/planner M 和 transaction integration M。

### P2: 可观察性与默认行为

#### [x] T9 增加通用 CLI phase progress

**目的：** 让超过 2 秒的命令可区分 lock wait、load、validate、apply、event append 和 index update，而不破坏 JSON stdout。

**验收条件：**

- `--progress` 统一写 stderr，stdout 只保留最终 JSON/human result。
- phase event 包含名称、elapsed time；lock wait 只报告等待时间，不泄露其他 agent 的 command 内容。
- TTY 可默认显示，非 TTY/机器调用默认关闭；显式 flag 始终优先。
- 首批覆盖 context、node-context、mutation commands、build 和 smoke。

**验证：**

```sh
python -B -m unittest tests.test_scripts -k cli_progress
python dev/scripts/benchmark_runtime.py --root .test_tmp/runtime_5000 --operation context_compact --progress --json
```

**依赖：** T0.2 的 phase naming；可与 T4-T8 并行接入。

**可能涉及：** 新增 `src/research_cockpit/cli_progress.py`，以及 command runtime、mutation lock 和主要命令模块。

**规模：** 基础设施 S，逐命令接入多个 S。

#### [x] T10 将 experiment artifact ingest 迁移为 record-first 默认

**目的：** 降低 graph artifact node 增长和 agent 判断成本，但避免无提示破坏现有脚本。

**验收条件：**

- 版本决策已落定：在 0.2.0 直接切换为 record-first 默认，并通过 migration note 明确标记 compatibility break；不虚构此前存在 runtime warning。
- 新增显式 `--promote`；`--record-only` 在兼容期保留。
- experiment target 默认产生 artifact record；创建 graph node 必须显式 promotion，并记录 promotion reason。
- 非 experiment target 不静默改变语义；要求显式模式或走 `create-artifact`。
- command manifest、help、SKILL 和 capability 文档都能让 agent 在一次 discovery 内选对模式。

**验证：**

```sh
python -B -m unittest tests.test_scripts -k ingest_artifact_default
python -B -m unittest tests.test_scripts -k promote_artifact_record
python dev/scripts/run_skill_release_check.py --json --skip-mutating
```

**依赖：** T1、T8；默认翻转需要明确 release/version 决策。

**可能涉及：** `src/research_cockpit/commands/ingest_artifact.py`、`src/research_cockpit/commands/promote_artifact_record.py`、command registry/manifest、相关 docs 和 tests。

**规模：** M，分两个 release 阶段。

#### [x] T11 同步文档、recipes 与 release guard

**目的：** 防止新能力落地后，下游 agent 仍走多命令、全量或 graph-artifact-first 的旧路径。

**验收条件：**

- `AGENTS.md`、`SKILL.md` 和 capabilities 只保留一条默认 startup/read/closeout/verification 路径。
- worker recipe 使用 bounded context、structured run closeout、changed validation；full gate 只用于 coordinator/final handoff。
- interaction migration、legacy compatibility、fallback 和 artifact promotion 边界有直接说明。
- release check 验证 command examples 可解析、flags 与 manifest 一致、compact schema/version 和 stdout byte budget。
- 由无会话上下文的 subagent 仅凭文档完成 fixture 上的 worker closeout 测试，并记录误用点。

**验证：**

```sh
python dev/scripts/run_skill_release_check.py --json --skip-mutating
python dev/scripts/run_agent_usability_check.py --json
python -m unittest discover -s tests
git diff --check
```

**Reader test 记录（2026-07-13）：** 无会话上下文的 subagent 仅凭仓库文档在独立 fixture 上完成默认 artifact record ingest、structured closeout、changed-scope validation 和 compact context；其发现的旧链接、manifest flags、legacy interaction 描述、assignment 参数及 Windows 深路径/UTF-8 问题均已修复。自动 usability guard 复现同一路径，但不声称替代真实 reader review。

**依赖：** 每个实现任务完成时增量同步；T8-T10 完成后做最终 reader test。

**可能涉及：** `AGENTS.md`、`SKILL.md`、`README.md`、`capabilities/experiment-tracking.md`、`capabilities/graph-state.md`、`capabilities/troubleshooting.md`、`docs/internal-architecture.md`、release/usability scripts。

**规模：** M。

## Checkpoints

### Checkpoint A: P0 契约与日志写路径

- [x] 5k/300k fixture 和 runtime baseline 可重复生成。
- [x] registry/help/parser 100% 一致。
- [x] compact success payload 小于 20 KB。
- [x] interaction append 不再重写全部历史，且并发 event 不丢失。
- [x] full test suite 和 release check 通过。

### Checkpoint B: P1 增量读写与 closeout

- [x] known-node context 和高频 mutation 在 fresh index 下不解析未关联 node files。
- [x] run/gate/artifact/assignment changed-file 不再无条件 full fallback。
- [x] structured closeout 零部分写入并满足暂定性能预算。
- [x] incremental 与 full validation 的错误判断一致。

### Checkpoint C: P2 可用性与迁移

- [x] 长命令 stderr progress 不污染 JSON stdout。
- [x] artifact record-first 的 0.2.0 breaking-change 边界、显式兼容 flag 和 migration note 已明确。
- [x] 无上下文 agent reader test 不再选择低效工作流。

## 暂不进入实现范围

- **常驻 daemon 或数据库：** 会改变部署和 truth-source 模型；现有问题可先通过 append-only storage、on-disk index 和事务合并解决。
- **仅增加进程内全局 cache：** CLI 进程间不能复用，且容易产生 stale state；只允许在单次命令内复用显式 snapshot。
- **为性能放松安全检查：** assignment scope、lifecycle、mutation lock 和 stale-write detection 是约束，不是优化对象。
- **立即用一个 schema 生成所有 argparse parser：** 长期可评估，但当前先用自动 contract tests 消除漂移，避免大范围 CLI 重写阻塞 P0 热点。
- **严格毫秒阈值进入普通 CI：** 普通 CI 检查 loader count、复杂度路径、输出 bytes 和语义；wall-time regression 由固定 fixture 的 opt-in benchmark 判断。

## 待决策项

- interaction segments 的轮转阈值采用 UTC 月份、固定 bytes，还是两者组合；决定前应比较 tail read、迁移和 Git diff 行为。
- artifact record-first 已决定在 0.2.0 翻转；`--record-only` 作为显式兼容写法保留，旧的隐式 graph-node 行为不保留。
- compact context 中 latest findings/key artifacts 的默认 limit；无论取值如何，schema 必须提供 total 和 omitted count。
- runtime benchmark 是否引入可选 `psutil` 采集 peak RSS；不得让其成为 plugin runtime dependency。
