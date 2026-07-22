# Research Cockpit 存储边界与轻量研究跟踪开发规划

**日期：** 2026-07-22
**状态：** 已确认方向，待分阶段实现
**范围：** storage layout、artifact lifecycle、Git hygiene、milestone ledger、assignment 粒度与 graph 增长控制
**关联资料：** 2026-07-22 storage/Git hygiene field feedback、`docs/plans/2026-07-19-multi-agent-research-management-framework.md`、`docs/artifact-retention-policy.md`、`docs/large-repo-hygiene.md`

## 1. 核心结论

Research Cockpit 继续承担研究协调、证据关联和决策追溯，但不应成为大型实验 payload 的仓库内默认存储，也不应把每次 shell 操作建模为 assignment 或 graph node。

目标设计同时解决两个问题：

1. 将小型控制面 truth、大型 payload 和 Git milestone projection 明确分离。
2. 只为独立负责、影响研究判断的阶段性 workstream 建立 assignment；micro change、retry、检查和重复 attempt 保留在 assignment 与 graph 之下。

实施顺序必须先阻止新增污染，再建立 inventory 和迁移能力，最后才允许物理回收。现有 project data 与 artifact 文件继续支持读取和显式更新，但不要求保留旧 CLI route。

## 2. 已确认约束

- Runtime 面向所有支持平台；平台差异必须服从同一 contract 并有跨平台测试。
- 多个 agent 继续通过独立 Git worktree 并行工作，并共享一个 canonical state root。
- 普通 worker 的成功路径不能增加命令数。
- Role-facade receipt 继续作为 verification boundary；不得恢复机械式 validate/build/smoke 流程。
- 旧 nodes、assignments、runs、gates、artifact records、manifests 和 payloads 必须可读。
- 不自动修改下游仓库的 `.gitignore`。
- 破坏性存储操作必须显式、有界、可审计，并默认不执行。
- Machine-specific absolute path 不得进入 committed documentation 或 ledger export。

## 3. 当前证据与缺口

| 范围 | 当前机制 | 已确认缺口 |
| --- | --- | --- |
| Artifact 位置 | `agent_sessions.py`、`evidence_staging.py` 和 `ingest_artifact.py` 推导 `<root>/artifacts` | State 与 payload 位置耦合 |
| Final evidence | `work close` staging、复制并完整 hash source directory | 大 evidence 会产生重复字节、完整读取和 inode 增长 |
| Record-only ingest | 不创建 graph artifact node | 仍复制 payload；record-only 不等于 reference-only |
| Retention | 已有 retention class 和 active-resource blocker | Final evidence admission 不能表达完整 lifecycle |
| Maintenance audit | 主要审计 graph artifact node 并返回详细集合 | 正常流程产生的 artifact records 与 orphan payload 没有统一 inventory；compact 输出可能无界增长 |
| Compaction | 将可降级 graph artifact node 转为 artifact record | 明确不回收 payload bytes |
| Milestone revision | `root_truth_revision` 遍历 `artifacts/**` | Handoff 成本随 payload 文件数增长，但 payload 不是 control truth |
| Git workflow | 已有 worktree 与 branch 检查 | 缺少 storage root/worktree 污染诊断和 bounded ignored/untracked summary |
| Assignment 模型 | Assignment root 是 option，cursor 从显式 experiment 开始 | Agent 仍可能把 retry 或微小修改升级为 experiment/assignment |
| Git 追溯 | Runtime state 可能全部 ignored 或 untracked | 缺少 deterministic、commit-friendly milestone ledger |

现有 retention enums、operation receipts、mutation lock、lease protection、active-run blockers、role facades 和 dry-run maintenance actions 应优先复用，不重新设计同类机制。

## 4. 目标与非目标

### 4.1 目标

- 新项目默认将 runtime state 放在 Git worktree 外。
- 新增大型 evidence 默认 reference-only，不能隐式复制。
- Cockpit-managed payload 使用显式配置的 external artifact root。
- Artifact metadata 记录 location、ownership、integrity strength、inventory、lifecycle 和 availability。
- Git 只保存 source、冻结配置、小型 project locator 和 deterministic milestone ledger。
- Maintenance audit 默认读取 bounded metadata，详细 candidate 必须分页。
- 物理回收使用 verified ownership、quarantine 和 immutable deletion manifest。
- 一个 assignment 通常对应一个独立负责的阶段性 workstream，并可汇总多次本地 attempt。
- Graph node 只表示 research knowledge 或 decision，不表示 operational activity。

### 4.2 非目标

- 本轮不把 YAML control state 替换成 SQLite 或其他数据库。
- 本轮不实现面向所有云厂商的通用 object-store client。
- 不把 Research Cockpit 变成每个 training step 或 sample 的 truth source。
- 不自动 commit、ignore、move 或 delete 下游仓库文件。
- 普通 closeout 不对每个 referenced large directory 计算完整 content hash。
- 不删除 external launcher 或 user-managed store 拥有的 payload。

## 5. 目标存储架构

```text
Git repository
├── .research-cockpit.yaml          # 只保存 project id 和 portable policy
├── research-ledger/                # deterministic milestone projection
├── docs/research/                  # 人工维护的计划与结论
└── configs/                        # 冻结实验配置

External state root
└── <project-id>/
    ├── graph/
    ├── assignments/
    ├── agents/
    ├── runs/
    ├── gate_results/
    ├── artifact_records/
    ├── handoffs/
    └── storage.yaml                # machine-local storage profile

External managed artifact root
└── <project-id>/                   # 只保存 Cockpit-owned payload

External launcher output
└── ...                             # 通过 URI/path 引用，不归 Cockpit 所有

Quarantine root
└── <project-id>/                   # 与 managed artifact 位于同一 filesystem
```

### 5.1 Root 语义

- Public `--root` 继续表示 state root。为每条命令改成两个 root 参数会增加 migration 和 token 成本，却不改善正确性。
- `RESEARCH_COCKPIT_ROOT` 继续解析 state root。
- `RESEARCH_COCKPIT_ARTIFACT_ROOT` 可覆盖 managed artifact root。
- `<state-root>/storage.yaml` 是正常的 machine-local artifact/quarantine policy。
- `.research-cockpit.yaml` 只保存稳定 `project_id`，不保存 absolute path，使 main checkout 与 worktrees 能发现同一个 external state。
- 所有并发 mutation agent 必须解析到同一个 state root。跨主机时使用 shared filesystem 或显式 `RESEARCH_COCKPIT_ROOT`；Git ledger 只是 milestone projection，不是同步 backend。
- Managed artifact root 可以为空。未配置时 reference mode 正常工作，managed-copy mode 返回一个 actionable error。

Managed storage 的解析优先级为：

1. 显式 init 或 migration input。
2. `RESEARCH_COCKPIT_ARTIFACT_ROOT`。
3. `<state-root>/storage.yaml`。
4. Legacy `<state-root>/artifacts`，仅用于解析既有记录。

新 managed write 不得静默回退到 legacy in-root artifact directory。

### 5.2 Truth 与 projection 边界

- State root records 是 workflow truth。
- Artifact records 是 provenance truth；payload bytes 是 evidence，不参与 mutation revision。
- Ledger files 与 dashboards 是 deterministic projection。
- `root_truth_revision` 必须排除 payload files 和 dashboards。Artifact record metadata 足以把 state revision 与 evidence identifier、integrity declaration 绑定。
- Payload availability 变化不能静默改写 research conclusion；只有显式 verification 可以更新 availability snapshot。

## 6. Artifact Contract

### 6.1 Record shape

新记录扩展现有 artifact-record contract，同时保留 legacy fields：

```yaml
record_id: record_x
experiment_id: experiment_x
run_id: run_x
artifact_kind: run_output
storage:
  mode: reference              # reference | managed | legacy
  ownership: external          # external | cockpit_managed
  uri: file:///external/output/run_x
  managed_key: null
integrity:
  level: manifest              # content | manifest | inventory | unverified
  algorithm: sha256
  digest: sha256:...
inventory:
  size_bytes: 1234
  file_count: 4
  complete: true
retention:
  class: reproducible_output
  expires_at: null
  keep_until_decision: null
availability:
  status: available            # available | missing | unknown | quarantined | deleted
  last_verified_at: null
lifecycle:
  supersedes: []
  superseded_by: null
```

Legacy `stable_path`、`manifest_path`、`links` 和 unknown fields 必须保留。位于 `artifacts/` 下的 legacy relative path 在内存中规范化为 `storage.mode: legacy`，普通 read/write 不移动它。显式指向现有 legacy record 的操作可继续执行 validated in-place update；新 record 和新 payload allocation 不得选择 legacy directory。

### 6.2 Storage modes

| Mode | Payload 行为 | Ownership | 默认 GC 行为 |
| --- | --- | --- | --- |
| `reference` | 记录 locator 与 bounded metadata，不复制 | External | 永不删除 |
| `managed` | 单次 stream 到 configured artifact root，再 atomic publish | Cockpit-managed | Verification 后才可能进入候选 |
| `legacy` | 解析现有 in-root payload，不隐式迁移 | Historical | 必须先显式迁移 |

Checkpoint、audio、tensor、cache、raw response 和大型 result tree 默认使用 reference mode。Managed mode 必须显式指定，仅用于 bounded review bundle 或确实需要 Cockpit ownership 的 payload。

### 6.3 Admission 与 integrity

- Evidence 超过 100 MiB 或 1,000 files 时必须声明 retention class。
- Bounded scan 达到任一阈值即可停止，并记录 lower bound 与 `inventory.complete: false`；truncated directory 不能被误判为 small。
- Reference mode 可接受 launcher 提供的 content digest 或 shard-manifest digest，无需重新读取所有 bytes。
- 未提供 digest 时，runtime 可生成 inventory digest，但必须准确标记强度，不能宣称 content-level integrity。
- Managed mode 在同一次 stream 中完成 copy 与 content hash，避免重复扫描。
- Decision-critical evidence 在 acceptance 前必须具备 content-level 或 manifest-level integrity。
- GC 必须依赖 verified integrity，不能只依赖 inventory digest。
- Symlink、junction、path traversal、source/target overlap 和 managed copy 期间 source mutation 继续被拒绝。

### 6.4 Worker intake

`work_close_v1.evidence_inputs` 与 `work_record_v1` 使用同一个 evidence contract。普通成功路径保持为：

```text
{work claim --return-packet OR work open} -> work start -> work close
```

不新增 standalone ingest、validation、build 或 smoke 命令。`work record` 仍只用于 crash recovery、shared consumption 或 long-running durable checkpoint。

Closeout 可将多次 local attempt 汇总为一个 bounded manifest，只保存 decision-relevant configuration、metrics、failure explanation、selected links 和 artifact pointers。

## 7. Git Hygiene 与 Ledger

### 7.1 Admission diagnostics

Initialization、storage configuration 和 managed evidence admission 检测：

- state、artifact、source 或 output root 是否位于 Git worktree 内；
- artifact root 是否作为完整目录被 ignore；
- source/output root 是否与 tracked source path 重叠；
- managed target 是否缺少 external root；
- payload 是否因 threshold 需要 retention 或更强 integrity。

Runtime 返回准确的 affected root 和建议 `.gitignore` fragment，但不修改仓库。Git worktree 内的大型 managed payload 默认拒绝；现有 legacy path 只返回 migration warning，不破坏读取。

默认 Git inspection 必须 bounded。对超大 ignored/untracked tree 的 exact count 只在显式 deep audit 中执行；summary 必须区分 exact count、lower bound 和 truncation。

### 7.2 Milestone ledger

`coord handoff` 复用 captured validation state，输出一个 deterministic `research_ledger_v1` projection。Ledger generation 属于正常 handoff，不增加 mandatory command。

每个 ledger 只包含：

- milestone id、kind、timestamp 和 state revision；
- accepted decisions、final findings 和 effective baselines；
- code/config/data revisions；
- primary metrics 与 gate outcomes；
- reviewed artifact ids、portable URIs 和 integrity digests；
- review 与 provenance references。

Local absolute paths、leases、polling state、receipts、dashboards、temporary attempts 和完整 artifact inventories 必须排除。相同 handoff exact retry 应能重建缺失 ledger，而不重复 validation 或 mutation truth。

## 8. 轻量 Assignment 与 Graph 模型

### 8.1 Tracking levels

| 层级 | 记录位置 | 典型内容 | 新 assignment/node |
| --- | --- | --- | --- |
| Operational | Git、launcher output、local logs | code edit、doc fix、progress read、smoke、retry | 否 |
| Evidence | 现有 assignment closeout 或 artifact record | selected metrics、final config、meaningful failure | 否 |
| Workstream | Assignment | 独立负责的阶段性交付 | 只建 assignment |
| Research knowledge | Graph | 新问题、option、hypothesis test、formal decision | 仅语义变化时建 node |

### 8.2 Assignment 创建规则

仅当以下三个条件同时满足时创建 assignment：

```text
(independent agent/worktree OR durable cross-session handoff OR independent review)
AND (stage deliverable OR decision-relevant evidence)
AND (no active assignment already covers the scope)
```

Task size 不是充分条件。一个很小的 concurrent write 可能需要 ownership isolation；一个很大的 serial effort 若已由 active workstream 覆盖，也不应新建 assignment。

一个 assignment 通常至少产出以下一项：

- reviewable stage deliverable；
- decision-relevant evidence；
- 改变 confidence 的 finding；
- accepted/rejected option 或 baseline recommendation；
- independent review verdict。

如果 closeout 不会产生其中任何一项，通常说明该工作被过度跟踪。

### 8.3 Assignment 生命周期

- 默认单位是“一个 independent stage workstream 与 owner 一个 assignment”，不是一个 command 或 attempt 一个 assignment。
- 同一 research contract 下的 code edit、retry、seed、parameter adjustment、preflight 和 repeated local execution 复用现有 assignment。
- 现有 option-level `root_node` 继续作为 ownership boundary；显式 experiment cursor 指向当前 broad hypothesis test。
- 一个 Cockpit run 可以汇总多个 launcher attempts。普通 attempt 保留在外部，只有影响结论或必须防丢失时才进入 Cockpit evidence。
- 只有 stage deliverable 可 review 或明确放弃时才 close assignment。
- 只有显式 review gate 才创建 review assignment。
- Parallel workstreams 需要 non-overlapping ownership scope；不能用一个 stage-wide assignment 串行化本可独立推进的 agents。

### 8.4 Node 创建规则

- 新 `problem`：出现现有问题无法涵盖的新 research question。
- 新 `option`：出现需要独立比较或 decision 的 candidate approach。
- 新 `experiment`：hypothesis、protocol 或 success criteria 的变化可能改变 research judgment。
- 新 `decision`：需要 formal acceptance、rejection、baseline change 或 stage closeout。

Documentation-only edit、progress read、mechanical retry、seed repeat、format fix、launcher invocation、ordinary preflight，以及被成功 retry 取代的同 protocol failure，都不创建 node。

`coord assign` 应接受紧凑的 structured `tracking_reason`，例如 `parallel_ownership`、`durable_handoff`、`independent_review` 或 `stage_deliverable`。缺失或矛盾的 justification 返回 non-blocking `granularity_warning`；不能把自然语言 keyword heuristic 作为 correctness mechanism。

### 8.5 关键信息保留

减少 assignment 和 node 不能丢失 decision evidence。Final closeout 保留：

- objective 与 bounded scope；
- final code/config/data revision；
- primary metrics 与 gate result；
- selected artifact pointers 与 integrity；
- meaningful negative evidence 与 failure explanation；
- finding、confidence、review requirement 与 next decision。

Intermediate commands、repeated health checks、被替代的 retry outputs 和 unchanged polling snapshots 应有意省略。

## 9. Maintenance 模型

### 9.1 Unified inventory

Inventory 必须覆盖 artifact records、graph artifacts、managed payload directories 和 managed-store orphans。普通读取使用 recorded metadata 或 incremental inventory index，不在每次 audit 时递归扫描全部 payload。

默认 audit 返回：

- state/artifact bytes 与 file counts，并标记 exact/truncated；
- 按 storage mode、ownership、retention、integrity 和 availability 聚合的 record counts；
- Git worktree 与 ignore risk；
- active assignment/run 与 protected-path counts；
- `must_keep`、`can_migrate`、`can_quarantine` 和 `needs_review` 聚合；
- 至多一个 bounded candidate page 和 cursor。

Compact response budget 为 16 KiB。详细 candidate 必须通过 `--limit`、cursor、id 或 classification 请求。

### 9.2 Physical lifecycle

Physical cleanup 与 graph-node compaction 分离：

```text
plan -> verify -> quarantine -> purge
```

- Plan 为 dry-run，并绑定 revision。
- Verify 重查 ownership、active references、retention、integrity、external copy 和 source availability。
- Quarantine 在同一 artifact filesystem 内 atomic rename managed payload，并更新 availability。
- Purge 在配置的 delay 后执行，不持有 state mutation lock，并且可 resume。
- 每个 transition 写入 immutable operation/deletion manifest。
- 每次 operation 只处理一个 artifact 或一个显式 bounded batch。

Reference 与 legacy payload 在显式 migration 建立 Cockpit ownership 前永不物理删除。

### 9.3 现有项目迁移

迁移顺序固定为：

1. 冻结新的 in-root managed write。
2. Snapshot active assignments、runs、resources 与 protected paths。
3. 建立 resumable legacy artifact records、graph artifacts 和 orphan payload inventory。
4. 先补 retention、inventory、locator、integrity 和 ownership metadata，不移动 bytes。
5. 通过 same-filesystem rename 或 cross-filesystem copy-and-verify externalize 选定 payload。
6. Integrity verification 成功后才 atomic switch record locator。
7. 输出 milestone ledger。
8. Quarantine 已验证且无 active reference 的 legacy payload。
9. Retention delay 到期后才 purge。

Cross-filesystem move 不是 atomic。Copy/hash 在 state lock 外进行，短 state transaction 只 publish verified locator。Publish 失败时必须留下可被 maintenance recovery 发现的 staging/orphan record。

## 10. Command 与 Context Budget

- Normal worker：最多三条命令，即 packet acquisition/open 加 `start/close`。
- Claimed worker 的 unchanged reopen：一次 bounded `work open --since`。
- Active workstream 内的 micro edit：零个新 assignment command，零个新 graph node。
- Final evidence：包含在 `work close`，不默认 standalone ingest。
- Milestone ledger：包含在 `coord handoff`，不默认 standalone export。
- Audit：一条 bounded summary command；只有 maintainer 主动选择 classification 时才取 page。
- GC 与 migration 是 exceptional maintenance operations，不进入 worker playbook。

## 11. 实施计划

### Phase 1：阻止新增增长并统一语义

#### Task 1.1：增加 storage layout resolver

- **说明：** 以一个跨平台 resolver 统一 state、managed artifact、quarantine、legacy 和 project locator path。
- **验收：** 现有 `--root` 与 `RESEARCH_COCKPIT_ROOT` 继续有效；新项目可解析 external state root；未配置 managed root 时 managed write 失败。
- **验证：** Unit tests 覆盖 explicit/env/profile/legacy precedence，以及 Windows/POSIX path forms。
- **预计文件：** `src/research_cockpit/paths.py`、新增 `src/research_cockpit/storage_layout.py`、`src/research_cockpit/cli.py`、focused tests。
- **依赖：** 无。

#### Task 1.2：从 state revision 与 session defaults 移除 payload

- **说明：** Handoff revision 只依赖 control truth，并用 storage-policy summary 替代 hard-coded stable artifact path。
- **验收：** Legacy artifact directory 增加 100,000 files 不增加 control revision traversal；packet 不再引导 agent 把 output 写回 data root。
- **验证：** Instrumented tests 证明 artifact directory 未被遍历；现有 handoff idempotency tests 通过。
- **预计文件：** `src/research_cockpit/milestone_handoffs.py`、`src/research_cockpit/agent_sessions.py`、focused tests。
- **依赖：** Task 1.1。

#### Task 1.3：发布 lightweight tracking rules

- **说明：** 在 schema 改动前，先把 Section 8 的 assignment/node 规则同步到 active skill/playbooks。
- **验收：** Worker/coordinator startup 明确复用 workstream、禁止 operational node，且不新增 normal command。
- **验证：** Role-playbook/document contract tests 与 instruction-size budget 通过。
- **预计文件：** `SKILL.md`、`AGENTS.md`、`capabilities/coordinator-loop.md`、`capabilities/worker-loop.md`、focused tests。
- **依赖：** 无。

### Checkpoint 1

- `fast` profile 加 affected tests 通过。
- Root revision 不再随 artifact payload count 增长。
- Blind coordinator flow 对 documentation-only micro change 不创建 assignment。

### Phase 2：Reference-first artifact intake

#### Task 2.1：扩展 artifact-record metadata

- **说明：** 增加 normalized storage、integrity、inventory、availability 和 lifecycle fields，同时保留 legacy records 与 unknown fields。
- **验收：** Legacy record round-trip 不移动或重写 payload bytes；新 record 可区分 external 与 Cockpit ownership。
- **验证：** Compatibility fixtures 覆盖 legacy stable path、new reference 与 touched-file upgrade。
- **预计文件：** `src/research_cockpit/artifact_records.py`、`src/research_cockpit/model.py`、`src/research_cockpit/retention.py`、focused tests。
- **依赖：** Task 1.1。

#### Task 2.2：实现 reference intake

- **说明：** Final 与 incremental evidence 默认使用 bounded reference admission。
- **验收：** Reference closeout 只写 metadata，不复制 payload；truncated scan 强制 retention；operation retry 不虚构 content integrity。
- **验证：** Test 在读取超出 bounded inventory 的 payload 时失败；closeout/retry/concurrency tests 通过。
- **预计文件：** `src/research_cockpit/evidence_staging.py` 或替代 intake module、`src/research_cockpit/assignment_results.py`、`src/research_cockpit/assignment_records.py`、focused tests。
- **依赖：** Task 2.1。

#### Task 2.3：实现显式 managed intake

- **说明：** 在一次 stream 中完成 copy/hash，写入 configured artifact root，再通过 atomic rename 与 state transaction publish。
- **验收：** Managed payload 默认不能指向 Git worktree；state commit 失败不留下不可见 payload；exact retry 可复用 verified staged content。
- **验证：** Cross-platform copy、source mutation、symlink/junction、collision、retry 和 rollback tests 通过。
- **预计文件：** 新 managed-store module、`src/research_cockpit/commands/ingest_artifact.py`、mutation integration、focused tests。
- **依赖：** Tasks 1.1、2.1。

### Checkpoint 2

- `precommit` profile 通过。
- Large reference evidence 不产生 duplicate payload。
- Legacy payload 继续可读并可显式更新。
- Normal worker flow 仍不超过三条命令。

### Phase 3：Unified audit 与 Git hygiene

#### Task 3.1：建立 incremental artifact inventory

- **说明：** Index artifact records、graph artifacts、managed payloads 与 managed orphans，避免每次 audit 扫描 payload tree。
- **验收：** Inventory 准确区分 exact/lower-bound statistics，并保护 active paths。
- **验证：** Synthetic large-record/orphan fixtures 覆盖 incremental update、stale index recovery 和 bounded scan。
- **预计文件：** 新 inventory module、`src/research_cockpit/maintenance.py`、validation/index integration、focused tests。
- **依赖：** Task 2.1。

#### Task 3.2：增加 bounded Git hygiene summary 与 pagination

- **说明：** 默认完整集合改为 aggregate counts、risk classification、cursor pages 和 optional deep Git counts。
- **验收：** Compact output 不超过 16 KiB；报告 root/artifact worktree overlap 与 ignore coverage；不修改 `.gitignore`。
- **验证：** Temporary Git repos 覆盖 tracked、untracked、ignored、nested worktree 和 truncated cases。
- **预计文件：** `src/research_cockpit/commands/maintenance_role_audit.py`、`src/research_cockpit/commands/maintenance_audit.py`、`src/research_cockpit/maintenance.py`、focused tests。
- **依赖：** Task 3.1。

### Phase 4：External initialization 与 commit-friendly ledger

#### Task 4.1：增加 portable project locator 与 external init

- **说明：** 通过 portable project id 初始化 machine-local state，同时保留 legacy in-repository discovery。
- **验收：** 新 Git project 默认不在 worktree 内创建 runtime data；所有 worktrees 解析同一 canonical state；locator 不包含 absolute path。
- **验证：** Init tests 在 temporary main/worktree repositories 上覆盖支持平台。
- **预计文件：** `src/research_cockpit/cli.py`、`src/research_cockpit/paths.py`、template/locator files、focused tests。
- **依赖：** Task 1.1。

#### Task 4.2：在 handoff 中生成 deterministic ledger

- **说明：** 复用 handoff validation state 生成 bounded、path-portable milestone ledger。
- **验收：** 同一 revision 重复导出 byte-stable；不含 local path/runtime state；exact retry 可重建 projection 而不重跑 gates。
- **验证：** Golden ledger、path hygiene、idempotency 和 handoff command-count tests 通过。
- **预计文件：** 新 ledger module、`src/research_cockpit/milestone_handoffs.py`、`src/research_cockpit/commands/coord_handoff.py`、focused tests。
- **依赖：** Tasks 2.1、4.1。

### Phase 5：落实 workstream-level tracking

#### Task 5.1：增加 structured assignment justification

- **说明：** 在 coordinator assignment receipt 中增加 compact tracking reason 与 deterministic granularity warning。
- **验收：** Missing justification 不产生 hidden mutation；review assignment 保持显式；自然语言种类不影响 correctness。
- **验证：** Coordinator contract tests 覆盖 valid reasons、warnings、retry 和 existing assignment data。
- **预计文件：** `src/research_cockpit/coordinator_operations.py`、`src/research_cockpit/commands/coord_assign.py`、role contracts、focused tests。
- **依赖：** Task 1.3。

#### Task 5.2：一个 closeout 支持 aggregated attempts

- **说明：** 一个 broad experiment/assignment 可汇总 selected external attempts，无需为每次 attempt 新建 node 或 assignment。
- **验收：** 一个 assignment 能保留多次 attempt 的 final 与 meaningful negative evidence；retry 和 ordinary check 创建零 graph node。
- **验证：** End-to-end worker fixture 执行多次 attempt，最终只产生一个 assignment、一个 broad experiment 和一个 bounded evidence bundle。
- **预计文件：** closeout contract/model、assignment result handling、work-packet projection、focused tests。
- **依赖：** Tasks 2.1、2.2。

### Checkpoint 5

- Blind downstream agent 不接受额外上下文，也能从 skill 学会 granularity rules。
- 一个 serial workstream 即使包含多次 edit/retry，仍只创建一个 assignment。
- Parallel non-overlapping workstreams 保持独立 lease 与 worktree。

### Phase 6：Legacy migration 与 managed GC

#### Task 6.1：增加 resumable storage migration

- **说明：** 对一个 legacy artifact 或 bounded batch 执行 inventory、externalize、verify 和 atomic relink。
- **验收：** 默认 dry-run；active paths 被排除；same-filesystem rename 与 cross-filesystem copy 使用不同 verified path；中断后可 resume。
- **验证：** Migration tests 注入 copy、hash、publish 和 retry failure，不丢失 source evidence。
- **预计文件：** 新 migration module、maintenance action contract/CLI、artifact records、focused tests。
- **依赖：** Phases 2、3。

#### Task 6.2：增加 quarantine-first GC

- **说明：** 仅对 Cockpit-managed payload 实现 revision-bound plan、verify、quarantine 和 delayed purge。
- **验收：** Active、must-keep、unresolved、external、legacy 和 weak-integrity artifact 均不能 purge；immutable manifest 记录每个 transition。
- **验证：** Safety-property、traversal、symlink/junction、open-file、crash recovery 和 idempotency tests 通过。
- **预计文件：** 新 GC module、maintenance action contract/CLI、mutation/runtime support、focused tests。
- **依赖：** Tasks 3.1、6.1。

### Phase 7：Documentation 与 release

#### Task 7.1：同步 active 与 migration documentation

- **说明：** Behavior 落地后更新 source-of-truth、architecture、retention、large-repository、launcher、CLI 和 migration docs。
- **验收：** Active doc 不再推荐 repository-local bulk storage、per-attempt assignment 或 full audit discovery。
- **验证：** Documentation contract tests 与 release instruction budget 通过。
- **预计文件：** `AGENTS.md`、`SKILL.md`、`README.md`、`docs/internal-architecture.md`、migration/retention docs。
- **依赖：** 所有 behavior phases。

### Final checkpoint

- 只运行一次 `full` test profile，不先串行运行低层 profiles。
- 在 temporary downstream Git repository 中运行一次 context-free subagent acceptance test。
- 验证 old fixture data 可 read、update、audit 和 migrate，且不会 implicit payload movement。
- Review generated ledger 与 deletion manifests 的 path、token 和 privacy hygiene。

## 12. Test Strategy

### 12.1 Unit 与 contract tests

- Storage root precedence 与 locator portability。
- Legacy artifact-record normalization 与 round-trip preservation。
- Integrity-level truthfulness 与 threshold handling。
- Assignment/node granularity decision rules。
- Cursor pagination 与 response-size budget。
- GC ownership 与 blocker invariants。

### 12.2 Integration tests

- Reference closeout 不复制 payload。
- Managed copy 单次 hash 并支持 rollback。
- 多个 Git worktrees 共享 state。
- Handoff ledger generation 不遍历 payload。
- Multi-attempt closeout 只使用一个 assignment。
- Migration 与 quarantine exact retry。

### 12.3 Performance tests

- Instrument filesystem traversal，不创建数百 GB fixture。
- 使用包含数千 records 和 bounded placeholder files 的 synthetic fixture。
- 断言 state revision 与 default audit 不访问 payload roots。
- 分别记录 audit output bytes、command count、file opens、stat calls 和 wall time。
- Duration threshold 在不同平台只作 observational signal；no-scan 和 bounded-output 是 mandatory structural assertion。

## 13. Acceptance Budgets

| 关注点 | 必须满足 |
| --- | --- |
| New-project Git status | 100 个普通 experiment 后仍为零 Cockpit runtime files |
| Reference evidence | 复制 payload bytes 为零 |
| Managed evidence admission | 显式 external root；超过 100 MiB 或 1,000 files 时声明 retention |
| State revision | 不遍历 artifact payload roots |
| Audit compact output | 不超过 16 KiB |
| Audit detail | 通过 cursor 分页且必须显式请求 |
| Worker command count | 普通 assigned flow 不超过三条命令 |
| Micro changes | Active workstream 内新 assignment 和 graph node 均为零 |
| Retry behavior | Research contract 不变时不创建 option/problem/experiment |
| Ledger | Deterministic、bounded、不包含 machine-local absolute path |
| GC | 不删除 external、active、must-keep、legacy 或 unverified payload |

## 14. 风险与缓解

| 风险 | 影响 | 缓解措施 |
| --- | --- | --- |
| Reference source 在 closeout 后变化 | Evidence 无法复现 | Integrity levels、supplied manifests、availability verification |
| External path 不可移植 | Collaborator 无法解析 payload | Logical managed keys、portable remote URI、ledger 排除 local file path |
| Cross-filesystem copy 部分成功 | 产生 duplicate/orphan bytes | Staging marker、hash verification、publish transaction、resumable cleanup |
| Assignment 过粗导致 agent 串行 | 并行吞吐下降 | 每个 independent workstream 使用 non-overlapping option scope |
| Assignment 长期开启 | Context 或 lease stale | Compact polling、automatic lease renewal、stage deliverable close criteria |
| Audit 重建 storage bottleneck | Maintenance 不可用 | Incremental inventory、bounded scan、summary default、deep mode explicit |
| GC 过早删除 evidence | 不可恢复的研究损失 | Managed ownership only、blockers、quarantine delay、immutable manifest |
| Ledger 与 state 分叉 | Git review 误导 | 从 revision-bound handoff deterministic generation，并支持 exact regeneration |
| Compatibility logic 永久膨胀 | 维护成本提高 | 隔离 legacy resolver/migration；new write 不使用 legacy path |

## 15. 并行开发边界

Task 2.1 固定 artifact contract 后，可以在独立 worktree 中并行三条线：

- **A 线：** storage layout、reference/managed intake、init、legacy migration。
- **B 线：** inventory、bounded audit、Git hygiene、GC planning。
- **C 线：** ledger、assignment granularity、active documentation、acceptance harness。

`artifact_records.py`、`paths.py`、role envelopes 和 maintenance action schemas 等 shared contract 必须先确定再并行编辑。GC execution 等待 A/B 线完成；最终 blind acceptance 等待三线合并。

## 16. Definition Of Done

- 新项目默认不在 Git worktree 中保存 runtime state 或 artifact payload。
- 旧 project data 与 artifact payload 继续可读，并可显式迁移。
- Reference-only 成为普通 evidence path；managed copy 必须显式选择。
- Bounded audit 可查看 artifact inventory、Git risk、retention、integrity 与 availability。
- Milestone handoff 输出普通流程所需的唯一 Git-facing research ledger。
- Assignment creation 遵守三条件规则，不由 micro change 或 attempt 触发。
- Graph growth 反映新 research concept 与 decision，不反映 operational activity。
- Quarantine-first GC 只回收 verified Cockpit-managed payload。
- Cross-platform tests、final full profile 和 context-free downstream acceptance test 全部通过。
