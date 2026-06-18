# Research Cockpit Skill 功能优化方案

## 目标读者

本文面向 `research-cockpit` skill 维护者，用于总结在大型音频实验仓库中长期使用 Research Cockpit 后暴露出的流程缺口，并提出可落地的 skill 文档、CLI、schema、dashboard 和维护工作流改进建议。

本文不是当前仓库的实验结论文档，也不要求立即修改现有实验节点。它的目标是帮助 skill 作者把真实使用中的清理、归档、多 agent 协作和仓库性能经验沉淀成通用能力。

## 背景

当前 `research-cockpit` skill 已经较好覆盖了以下能力：

- 使用 canonical `research_cockpit/` 作为研究状态源。
- 通过 `assignment_id` 和 `assignment_cursor` 支持多 agent 并行推进。
- 使用 CLI 更新节点、run、artifact、finding、decision，避免直接手改 YAML。
- 使用 `close-branch`、`finalize-workstream`、`migrate-terminal-next-actions` 管理节点生命周期。
- 在删除 worktree 前要求保存有用 artifact 和 finding。
- 明确不应把 worktree-local `research_cockpit/` 当作长期状态源。

但在一个包含大量训练、推理、评估、试听 bundle、dataset cache、checkpoint、临时 worktree 和 nested repo 的长期 ML 仓库中，单纯记录实验状态还不够。实际痛点主要集中在：

- `outputs/`、`data/`、`research_cockpit/artifacts/` 和 `.worktrees/` 体积快速膨胀。
- 临时 `codex/*` branch 和 worktree 长期堆积，导致维护成本和 Git watcher 成本上升。
- 实验结束后，哪些 checkpoint/cache/output 可以删、哪些必须保留，往往只能靠人工回忆。
- 失败实验不一定完全无价值，可能包含可继承的 dataset builder、eval、launcher 或 bugfix。
- 多 agent 同时运行时，清理任务需要知道哪些路径仍被训练、推理或 caption server 使用。
- Research Cockpit dashboard 和 repo watcher 容易被大量 generated files 拖慢。

核心改进方向是：把 cleanup、retention、branch/worktree lifecycle 和 repository hygiene 从事后人工判断，前移到 run、artifact、worktree、assignment 的标准生命周期中。

## 设计原则

### 1. Research state 和 heavy artifact 分离

RC 应保存研究状态、结论、证据索引、关键 summary 和小型可审阅 bundle。大规模 raw outputs、precompute cache、optimizer state、bulk generated audio 不应该默认进入 `research_cockpit/artifacts/`。

### 2. 结论必须可追溯，payload 不一定永久保存

一个实验结论需要保留：

- code commit 或 branch/ref
- config copy
- manifest path
- metric summary
- launcher 或重跑命令
- 关键 listening bundle 或少量代表样本

但不一定需要保留：

- 全量中间 generation
- 可重新生成的 precompute cache
- 每个 intermediate checkpoint
- optimizer/scheduler state，除非明确计划 resume

### 3. 临时分支必须有终点

`codex/*`、`agent/*` 和临时 worktree 适合作为隔离执行环境，不适合作为长期知识库。长期有价值但暂不进入 main 的路线应转为 `research/*` branch。负面实验应记录结论并只保留可复用代码。

### 4. 多 agent 场景下，cleanup 是受控变更

清理磁盘、删除 worktree、删除 branch、移动大目录前，必须先确认：

- RC 中结论和证据已记录。
- 相关 run 是否仍在 `running` 或 `queued`。
- 是否存在 live process 使用该路径、GPU、port 或 output root。
- 对应 worktree 和 nested repo 是否 clean。

### 5. Watcher performance 是功能需求

在大型实验仓库中，Git watcher 和 IDE watcher 性能直接影响 agent 可用性。skill 应把 sparse worktree、ignored generated dirs、external artifact root、dashboard build profile 纳入推荐流程，而不是事后优化。

## 建议一：增加 Worktree Closeout 工作流

### 当前缺口

现有 skill 已经要求“删除 worktree 前 ingest artifact 并 complete experiment”，但缺少完整判断流程：

- 这个 worktree 对应哪个 assignment、option、experiment？
- 是否还有 live run 正在使用？
- nested `TangoFlux` repo 是否有未提交改动？
- worktree 上的代码是正向可合并、负面无价值，还是需要转为长期 `research/*` branch？
- worktree 删除后，对应临时 branch 是否可以删除？

### 建议补充到 skill 文档

建议新增章节：`Worktree Closeout and Cleanup`。

推荐流程：

1. 定位 worktree 对应的 RC assignment、option、experiment、run。
2. 读取 `agent-session-context` 或 `option-workstream-context`，确认节点状态。
3. 检查 outer repo 和 nested repo 的 dirty state。
4. 检查是否有 live process 使用 worktree 路径、output root、GPU 或 port。
5. 分类代码改动：
   - `merge_to_main`: 已验证、通用、不会破坏 baseline。
   - `preserve_as_research_branch`: 有长期研究价值，但尚不适合 main。
   - `extract_partial`: 只保留通用脚本、eval、dataset builder、bugfix。
   - `discard_after_recording`: 失败实验专用代码，无复用价值。
6. 记录 finding、artifact、run completion 和 next action。
7. 删除 worktree。
8. 删除已 merged 或已转移价值的临时 branch。
9. 执行 validate/build/smoke。

### 建议 CLI

可以新增只读审计命令：

```sh
research-cockpit worktree-audit \
  --root research_cockpit \
  --repo /path/to/repo \
  --include-nested TangoFlux \
  --json
```

输出建议包含：

```yaml
worktrees:
  - path: /repo/.worktrees/example
    branch: codex/example
    head: abc123
    assignment_id: assign_x
    active_nodes:
      - experiment_x
    run_statuses:
      - run_id: run_x
        status: completed
    outer_dirty: false
    nested_dirty:
      TangoFlux: false
    process_hints:
      path_in_cmdline: []
      gpu_processes: []
      listening_ports: []
    recommended_action: remove_worktree_delete_temp_branch
    blockers: []
```

可以新增执行辅助命令，但默认先做 dry-run：

```sh
research-cockpit worktree-closeout \
  --root research_cockpit \
  --worktree /repo/.worktrees/example \
  --classification preserve_as_research_branch \
  --research-branch research/example \
  --dry-run \
  --json \
  --show-diff
```

该命令不应直接强删文件。它更适合输出安全 checklist、需要用户确认的 shell commands、RC mutation plan 和 blockers。

## 建议二：增加 Branch Lifecycle 策略

### 当前缺口

RC 现在关注 research graph，但没有显式管理 Git branch 生命周期。长期使用后，临时 branch 数量会成为负担。尤其在 nested repo 中，outer repo 和 `TangoFlux` repo 各自都有大量 `codex/*` branch，容易拖慢工具和增加认知成本。

### 建议分支类型

| 类型 | 用途 | 生命周期 |
| --- | --- | --- |
| `main` | 稳定可用 baseline | 只接收已验证的通用改动 |
| `codex/*` | 单次任务或临时实验 | 实验 closeout 后删除或合并 |
| `agent/*` | RC assignment 派生工作 | assignment 结束后关闭 |
| `research/*` | 长期研究路线 | 可长期维护，不要求立即进 main |
| `archive/*` | 只读历史保留 | 尽量少用，优先依赖 Git history 和 RC evidence |

### 建议 CLI

```sh
research-cockpit branch-audit \
  --root research_cockpit \
  --repo /path/to/repo \
  --base main \
  --include-nested TangoFlux \
  --json
```

输出分类：

- checked out by worktree
- merged into main
- unmerged with RC evidence
- unmerged without RC evidence
- candidate for `research/*`
- candidate for deletion
- nested branch mismatch

### 建议 skill 文档规则

- 不要因为实验结果失败就直接丢弃整个 branch。先判断是否有可复用代码。
- 正向但不稳定的研究路线应转为 `research/*`，不要继续挂在 `codex/*`。
- 对已 merged、非 checked-out、无 active RC node 的 `codex/*`，优先 `git branch -d`，不要直接 `-D`。
- 批量删除 branch 后建议运行 `git pack-refs --all --prune`。

## 建议三：引入 Artifact Retention Class

### 当前缺口

现有 artifact 主要记录路径和链接，但没有表达“这个 payload 是否应该长期保存”。导致后续清理时需要重新判断每个目录的价值。

### 建议字段

建议 artifact、run 或 evidence block 支持以下字段：

```yaml
retention:
  class: evidence_critical
  reason: "Contains metric summary and portable listening bundle used by finding_003."
  delete_after: null
  reusable: true
  regenerate_command: scripts/experiments/example/run_eval.sh
  depends_on_for_future_training: false
  keep_files:
    - metrics_summary.json
    - index.html
    - comparison_data.json
  disposable_patterns:
    - "raw_generations/**"
    - "optimizer.bin"
    - "scheduler.bin"
```

### 建议 class

| class | 含义 | 默认处理 |
| --- | --- | --- |
| `evidence_critical` | 支撑 finding/decision 的关键证据 | 保留 |
| `portable_review_bundle` | HTML、音频、JSON 组成的小型试听包 | 保留，可归档 |
| `final_checkpoint` | 可复现实验指标的最终或最佳 ckpt | 保留 |
| `resume_state` | optimizer/scheduler 等恢复训练状态 | 仅在近期 resume 时保留 |
| `reproducible_output` | launcher 可重跑得到的大输出 | 可清理，保留摘要和命令 |
| `disposable_cache` | precompute/cache/intermediate 文件 | 结论记录后可删 |
| `deprecated_payload` | 已被新结果替代 | 可归档或删除 |

### 建议 CLI

```sh
research-cockpit artifact-retention-audit \
  --root research_cockpit \
  --repo /path/to/repo \
  --min-size-gb 10 \
  --json
```

输出应包含：

- path
- size
- file_count
- linked_nodes
- linked_findings
- retention class
- safe_to_delete
- blockers
- suggested_summary_to_keep

## 建议四：把 Run Closeout 扩展为空间管理入口

### 当前缺口

`create-run`、`update-run`、`complete-run` 可以记录执行过程，但 run 完成后不会强制回答：

- 需要保留哪些 checkpoint？
- 是否还计划 resume？
- optimizer/scheduler 能否删除？
- eval raw generations 是否可删？
- 是否已经生成 portable listening bundle？

### 建议 run 字段

```yaml
output_retention:
  keep_checkpoints:
    - step10000
    - final
  keep_optimizer_state: false
  resume_planned: false
  raw_outputs_disposable: true
  portable_bundle_path: outputs/example/listening_bundle.tar.gz
  cleanup_after_completion: true
  cleanup_notes: "Metrics and bundle preserved; intermediate generations are reproducible."
```

### 建议流程

在 `complete-run` 或 `complete-experiment` 后，如果 output retention 信息缺失，CLI 可以给出 warning：

```text
run_completed_without_retention_policy
```

这不应阻止记录结论，但应提醒 agent 及时补充清理策略。

## 建议五：默认支持 Sparse Worktree 和 Watcher Hygiene

### 当前缺口

现有 skill 推荐 git worktree，但没有强调 sparse checkout，也没有把 watcher 成本视为一等问题。对于包含 `research_cockpit/`、`outputs/`、`data/`、`datasets/` 的仓库，每个 worktree 都复制完整目录会带来明显成本。

### 建议 skill 文档规则

新增 `Repository Hygiene for Large Experiment Repos`：

- 新临时 worktree 默认使用 sparse checkout。
- worktree 中通常不 checkout `research_cockpit/`、`outputs/`、`logs/`、大规模 `data/`、generated dataset。
- canonical RC root 始终使用主 checkout 的绝对路径。
- 大型 artifact root 应位于 git ignored 目录或 repo 外部稳定路径。
- 不要在每个 worktree 下复制一份 `research_cockpit/artifacts/**`。
- 对 IDE 或 repo watcher，推荐 exclude `.worktrees/`、`outputs/`、`data/`、`datasets/**/artifacts/`、`research_cockpit/artifacts/**`。

### 建议 CLI

可以扩展 `start-agent-session`：

```sh
research-cockpit start-agent-session \
  --root /repo/research_cockpit \
  --option option_x \
  --label run_y \
  --branch agent/option_x-run_y \
  --worktree /repo/.worktrees/run_y \
  --create-worktree \
  --sparse \
  --sparse-profile ml-experiment \
  --dry-run \
  --json \
  --show-diff
```

也可以只在 skill 文档中要求项目提供 helper，例如：

```sh
scripts/dev/create_sparse_worktree.sh <task-slug> main
```

### 建议 profile

`ml-experiment` sparse profile 可以默认包含：

- source code
- configs
- scripts
- tests
- minimal docs
- nested repo path if needed

默认排除：

- `research_cockpit/`
- `outputs/`
- `logs/`
- `data/`
- `datasets/**/artifacts/`
- `.venv/`
- `.venvs/`

## 建议六：增加 Active Resource Registry

### 当前缺口

多 agent 同时推进时，清理任务最危险的地方不是删错 Git 文件，而是删掉仍被训练、推理、caption server 使用的 output/cache/model/data。当前 RC run record 可以记录 progress file，但缺少统一的资源声明。

### 建议字段

run record 可选增加：

```yaml
resources:
  gpus:
    - 0
    - 1
  ports:
    - 8000
  process_ids:
    - 123456
  worktree: /repo/.worktrees/example
  output_roots:
    - /repo/outputs/example_run
  cache_roots:
    - /repo/data/example/.precomputed
  dataset_roots:
    - /repo/data/example
  model_paths:
    - /repo/outputs/example_run/checkpoint-final
```

### 建议 CLI

```sh
research-cockpit active-resources \
  --root research_cockpit \
  --json
```

用于清理前快速判断：

- 哪些目录仍被 active run 声明使用。
- 哪些 GPU/port 仍属于 running run。
- 哪些 worktree/output/cache 有 active dependency。

该命令不能替代系统级 `ps`、`nvidia-smi`、`ss` 检查，但可以作为 RC 层面的第一道保护。

## 建议七：把 Experiment Claim Discipline 结构化

### 当前缺口

实验节点可以记录 success criteria 和 findings，但对于 ML/audio 控制实验，仍容易出现证据等级和 claim scope 不匹配的问题。例如小样本 overfit 被误读为泛化能力，teacher-forced 结果被误读为 pure-noise generation 控制能力。

### 建议字段

实验节点或 finding 支持：

```yaml
evidence_level: overfit_cached_init
scale_class: five_shot
claim_scope: pipeline_fit_only
effective_epochs: 3000
generation_mode: cached_init
requires_pure_noise_for_promotion: true
required_metrics:
  - outside_leakage
  - onset_offset_accuracy
  - wrong_time_counterfactual
  - wrong_text_counterfactual
```

### 建议 lint

`research-cockpit lint --semantic` 可以新增 warnings：

- `tiny_sample_claims_generalization`
- `teacher_forced_claims_generation`
- `missing_effective_epochs`
- `timeline_claim_without_timing_metric`
- `open_vocab_claim_without_unseen_split`
- `cfg_claim_without_condition_dropout_record`

这些 warning 不一定通用于所有项目，所以建议做成可配置 ruleset。

## 建议八：Dashboard 和 Build Performance Guardrails

### 当前缺口

RC dashboard 和 context build 对大型 artifact tree 敏感。当前 skill 提到 build profile 和 `--skip-resource-search`，但没有把大目录扫描风险纳入流程规范。

### 建议规则

- Dashboard build 默认不递归扫描 heavy artifact payload。
- Resource search 只读取 artifact metadata、summary files 和 explicitly linked files。
- 对超过阈值的目录，只记录 size、file count 和 top-level summary。
- profile 中应报告最慢目录、最慢节点、扫描文件数、跳过文件数。
- 当 `research_cockpit/artifacts/**` 超过阈值时，CLI 给出 external artifact root 建议。

### 建议配置

```yaml
resource_scan:
  max_files_per_artifact: 200
  max_bytes_per_artifact: 104857600
  skip_patterns:
    - "**/*.wav"
    - "**/*.flac"
    - "**/checkpoint-*/*"
    - "**/.precomputed/**"
  summary_files:
    - metrics_summary.json
    - report.md
    - README.md
    - bundle_check.json
```

## 建议九：Listening Bundle 作为一等 Artifact 类型

### 当前缺口

音频生成任务中，主观试听和指标对比经常需要被打包下载到本地。普通 artifact path 不足以表达 bundle 的可移植性要求。

### 建议 artifact 类型或 metadata

```yaml
artifact_kind: listening_bundle
portable: true
entrypoint: index.html
archive: bundle.tar.gz
relative_paths_validated: true
bundle_check: bundle_check.json
methods:
  - gt
  - ours
  - open_acn
metrics_summary: metrics_comparison_summary.json
```

### 建议 lint

对 `artifact_kind: listening_bundle`：

- `index.html` 必须存在。
- HTML audio `src` 不得是绝对路径。
- 所有 referenced audio 必须在 bundle 内。
- 如果有 `bundle_check.json`，必须显示通过。

## 建议十：提供 Maintenance Audit 总入口

### 当前缺口

实际清理时需要分别查 Git worktree、Git branch、RC run、RC artifact、disk usage、process、dashboard health。skill 可以提供一个维护入口，让 agent 先审计再行动。

### 建议命令

```sh
research-cockpit maintenance-audit \
  --root research_cockpit \
  --repo /path/to/repo \
  --include-nested TangoFlux \
  --min-size-gb 10 \
  --json
```

输出分区：

- `active_assignments`
- `running_runs`
- `active_resources`
- `worktree_candidates`
- `branch_candidates`
- `large_artifact_candidates`
- `large_output_candidates`
- `dashboard_performance_warnings`
- `unsafe_cleanup_blockers`
- `recommended_next_actions`

这可以成为清理任务的标准第一步。

## 建议的文档改动位置

建议 skill 作者不要把所有细节都堆进 `SKILL.md`，否则 startup instruction 会变得过长。推荐拆分：

| 文件 | 内容 |
| --- | --- |
| `SKILL.md` | 保留高优先级流程和路由说明 |
| `capabilities/maintenance.md` | cleanup、retention、worktree、branch、watcher hygiene |
| `capabilities/experiment-tracking.md` | run retention、claim discipline、resource declaration |
| `capabilities/integrations.md` | sparse worktree、external artifact root、IDE watcher exclude |
| `docs/artifact-retention-policy.md` | retention class 和 schema |
| `docs/worktree-branch-lifecycle.md` | closeout checklist 和 branch policy |
| `docs/large-repo-hygiene.md` | dashboard/profile/watcher 性能建议 |

`SKILL.md` 中只需要新增 capability routing：

```markdown
- Cleanup, artifact retention, worktree closeout, branch lifecycle, and large-repo hygiene: `capabilities/maintenance.md`
```

并在 `Parallel Agents With Git Worktrees` 后补一句：

```markdown
For large experiment repositories, prefer sparse worktrees and keep generated outputs, caches, and bulky artifacts outside temporary worktree checkouts. Before deleting or moving any worktree/output/cache, run the maintenance closeout checklist.
```

## 分阶段实现计划

### P0：文档和 skill 规则

不改 CLI，只更新 skill 文档和 capability docs。

验收标准：

- skill 明确区分 RC metadata、evidence summary、heavy artifact payload。
- skill 提供 worktree closeout checklist。
- skill 提供 branch lifecycle policy。
- skill 提供 artifact retention classes。
- skill 提供 sparse worktree 和 watcher hygiene 推荐。

### P1：schema 扩展和 lint

为 run、artifact、finding 增加可选 metadata，并通过 semantic lint 给出 warning。

验收标准：

- 旧数据不需要迁移也能 validate。
- 新数据可以记录 retention、resources、evidence_level、claim_scope。
- `lint --semantic` 能发现最常见的 claim/evidence 不匹配和缺失 retention policy。

### P2：只读审计 CLI

优先实现不会修改文件的审计命令。

验收标准：

- `worktree-audit` 能列出 worktree、branch、dirty state、RC active node、candidate action。
- `branch-audit` 能分类临时 branch。
- `artifact-retention-audit` 能按 size 和 linked evidence 输出清理建议。
- `active-resources` 能列出 RC 层面的 active paths、GPU、port、PID。
- `maintenance-audit` 能聚合上述结果。

### P3：closeout 辅助 CLI

实现 dry-run 优先的 closeout 命令，输出 RC mutation plan 和 shell command draft。

验收标准：

- 默认只 dry-run。
- 发现 active run、dirty worktree、未记录 finding、缺少 artifact 时阻塞。
- 不直接强删 worktree 或 branch。
- 对 destructive action 只生成明确、可审阅命令。

### P4：Dashboard 性能优化

将 large artifact scan 和 generated payload scan 纳入 profile 和默认限制。

验收标准：

- build profile 报告扫描文件数和最慢路径。
- 默认跳过明显 heavy payload。
- artifact resource view 仍能显示 summary 和关键 evidence links。

## 迁移建议

对已有项目，不建议强制一次性补齐所有 metadata。建议渐进迁移：

1. 新增 retention class 和 resource declaration 为 optional。
2. 新 run 默认提示填写 output retention。
3. 对大于阈值的 artifact/output/cache，审计命令提示补充 retention。
4. 清理 worktree 前必须完成 closeout checklist。
5. 长期 `codex/*` branch 分批转为 `research/*` 或删除。

## 风险和取舍

### 不应把 RC 变成文件清理工具

RC 应提供审计、证据和安全检查，不应直接替代人工确认的 destructive cleanup。尤其是删除 output/cache/checkpoint，需要保留用户审批和系统级 process 检查。

### 不应让 skill 启动成本过高

新增规则应放在 `capabilities/maintenance.md` 等按需文档中。`SKILL.md` 只保留短规则和路由，避免每次使用 RC 都加载大量维护细节。

### 不应强制所有项目使用 ML/audio 规则

`evidence_level`、`claim_scope`、`effective_epochs`、`listening_bundle` 等适合 ML/audio 项目，但应作为可配置 extension，不应污染轻量研究项目。

### 不能只依赖 RC 判断 live usage

RC 的 active resource registry 是第一道保护，不是最终事实。真正清理前仍需要结合 `ps`、`nvidia-smi`、`ss`、文件路径占用和用户审批。

## 推荐的最小可行改进

如果 skill 作者只能先做一轮，我建议优先做：

1. 新增 `capabilities/maintenance.md`。
2. 在 `SKILL.md` 中路由到 maintenance capability。
3. 文档化 worktree closeout checklist。
4. 文档化 artifact retention classes。
5. 文档化 branch lifecycle policy。
6. 文档化 sparse worktree 和 watcher hygiene。

这不需要改 CLI，却能马上减少错误清理、worktree 堆积、artifact 过重和 watcher 变慢的问题。

第二轮再做只读审计 CLI。第三轮再做 schema/lint 和 dashboard profile 强化。

## 一句话总结

Research Cockpit 目前已经能回答“当前研究推进到哪里了”。下一步应该系统化回答“哪些结果必须保留、哪些 payload 可以清理、哪些临时分支该关闭、哪些 worktree 还能删除、哪些 claim 有足够证据”，这样它才能支撑长期、多 agent、大规模实验仓库，而不是只做节点图记录工具。
