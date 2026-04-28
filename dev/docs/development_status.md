# Research Cockpit 开发状态
## Agent Option Workstream v1 更新（2026-04-28）
本阶段支持“每个 agent follow 一个方案节点”的最小闭环，采用 `problem -> option -> problem -> option -> experiment/decision` 的递归分支结构。目标是让下游 agent 可以认领一个 option，在该 option 子树内继续拆分问题、方案和实验，最后把证据报告回 option，供上游 problem 做方案比较和最终决策。

已完成：

- `option` 节点新增可选 `agent_workstream` 与 `workstream_report` 字段；`agent_workstream.status` 支持 `claimed/in_progress/blocked/reported/released`，`workstream_report.recommendation` 支持 `accept/reject/continue`。
- 校验层会拒绝非 option 节点使用 workstream 字段，并校验非法 workstream status、非法 recommendation 和 `report_to_problem` 引用。
- 新增 `build_option_subtree(...)` 和 `build_option_workstream_context(...)`，支持递归解析 `option -> problem -> option -> experiment/decision` 子树。
- Branch Comparison、decision evidence bundle 和 Decision Trace 的实验证据统计已纳入 option 子树下的递归实验；直接挂在 option 下的旧数据仍兼容。
- 新增 `claim_option.py`、`option_workstream_context.py` 和 `report_option_workstream.py` 三个工作流脚本。
- `build_dashboard.py` 新增输出 `option_workstreams.json`；`agent_context_pack.json` 增加 `active_option_workstreams`，`focus_context_pack.json` 在 focus 位于 option 分支时增加 `option_workstream_context`。
- Streamlit UI 新增“方案工作流 / Option Workstreams”页签；option 节点 Actions 区展示 claim/context/report 命令模板。
- `list_agent_commands.py`、`skill_smoke_test.py`、`dev/scripts/run_skill_release_check.py` 已纳入 option workstream 只读检查。

验证结果：

- `python -m unittest discover -s dev/tests`：通过。
- `python scripts/skill_smoke_test.py --json`：纳入 option workstream context 检查。
- `python dev/scripts/run_skill_release_check.py --json --skip-mutating`：用于发布前只读回归。

下一批候选：

- 为 mutating scripts 扩展 dry-run coverage，特别是 `claim_option.py` 和 `report_option_workstream.py`。
- 增强 option workstream UI，支持从前端安全触发 claim/report，而不是只展示命令模板。
- 前端图谱交互升级：点击展开节点、编辑节点文本、以 option workstream 为中心过滤图谱。

## Agent Skill Release Hardening v1 更新（2026-04-28）

本阶段把人工 subagent forward-test 固化为可重复的发布前检查流程，目标是在开源或复制 `skills/research-cockpit/` 前，自动验证 package 结构、公开化扫描、只读 agent 启动、包外可移植性、隔离写入 workflow 和 decision quality gate。

已完成：

- 新增 `dev/scripts/run_skill_release_check.py`，作为开发侧 release harness；默认检查 `skills/research-cockpit/`，支持 `--skill-path`、`--python`、`--json`、`--keep-temp` 和 `--skip-mutating`。
- Release check 输出 `package_shape`、`public_scan`、`read_only_startup`、`portable_copy`、`isolated_mutation` 和 `decision_gate` tracks；失败时返回退出码 `1`，JSON 中保留命令、返回码、stdout/stderr 摘要。
- 只读启动段覆盖 `agent_bootstrap.py`、`skill_smoke_test.py`、`list_agent_commands.py`、`search_knowledge.py --query t5` 和 `suggest_next_actions.py`。
- 包外复制段会把 skill package 复制到 `.test_tmp/`，再从脚本绝对路径运行 `skill_smoke_test.py --json`，验证脚本能从自身位置推导 package root。
- 隔离写入段只在临时副本里运行 `record_finding.py`、`update_decision_evidence.py`、`validate_cockpit.py` 和 `build_dashboard.py`，并确认原始 package 没有被修改。
- Decision gate 段运行 `check_decision_acceptance.py --id decision_flan_t5_clap --json`，用于确认 checklist 能结构化报告 ready/not-ready 状态，不自动 accept，也不使用 `--force-accept`。
- `dev/README.md` 已补充 release check 用法；skill package README 继续只保留 runtime/self-test 说明，避免把开发侧 harness 打进可发布 skill 语义。

验证结果：

- 新增 release checker 测试覆盖 package shape、public scan、依赖缺失结构化输出、skip-mutating 报告和隔离写入只改临时副本。
- `python dev/scripts/run_skill_release_check.py --json` 可作为发布前完整检查入口。
- `python dev/scripts/run_skill_release_check.py --json --skip-mutating` 可作为较快的只读检查入口。

下一批候选：

- 扩展 dry-run coverage，让更多 mutating scripts 可预览写入结果。
- 增强 decision acceptance UI 修复提示，帮助用户补齐 alternatives、consequences 和 next actions。
- 规划前端图谱交互升级，为可点击展开节点、编辑节点文本和未来前端迁移保留接口边界。

## Agent Skill Forward-Test v1 更新（2026-04-28）

本阶段已完成面向 agent skill 使用场景的前向测试与小幅加固。测试目标是验证 `skills/research-cockpit/` 能否作为独立 skill 被后续 agent 发现、启动、读取上下文、执行只读分析，并在隔离副本中完成写入型研究工作流。

已完成：

- 使用多个 subagent 覆盖只读启动、上下文读取、研究检索、行动建议理解、隔离副本写入、decision acceptance checklist 和包外可移植性测试。
- 只读测试确认 agent 可从 `SKILL.md` 进入，运行 `agent_bootstrap.py --json`、`skill_smoke_test.py --json`、`list_agent_commands.py --json`、`search_knowledge.py --query t5 --json` 和 `suggest_next_actions.py --json`。
- 隔离写入测试确认 mutating workflow 可在复制出的 skill package 中运行，真实 package 数据未被修改；副本中可完成 `record_finding.py`、`update_decision_evidence.py`、`validate_cockpit.py` 和 `build_dashboard.py`。
- 可移植性测试确认脚本可从自身路径推导 package root；当 cwd 不可靠时，agent 可以使用脚本绝对路径完成调用。
- 修复 `skill_smoke_test.py`：默认解释器缺少依赖时，现在输出结构化 JSON 诊断和安装/切换解释器提示，不再暴露 Python traceback。
- 补充 `README.md` 和 `SKILL.md`：明确支持 `--root` 的脚本应传入 `research_cockpit` 数据目录，而不是 skill package 根目录。
- 补充测试：覆盖 smoke test 依赖缺失诊断，并调整公开化扫描测试，避免测试 fixture 自身出现本机私有路径关键词。

验证结果：

- `python -m unittest discover -s dev/tests`：114 个测试通过。
- `python scripts/validate_cockpit.py`：skill package 数据校验通过。
- `python scripts/skill_smoke_test.py --json`：在依赖完整解释器下通过。
- 默认 `python` 缺少依赖时，`skill_smoke_test.py --json` 会稳定返回结构化失败 JSON。
- 私有路径关键词扫描无命中；当前公开文档和 skill package 不再包含真实本机路径或用户名。

当前已知状态：

- `decision_flan_t5_clap` 尚不能通过 acceptance checklist，主要缺少 `alternatives_considered` 和 `next_required_actions`。
- 当前仍有一个缺失资源 warning：`figures/dataset_pipeline_v2_150.png`。
- 这些属于研究数据完整性问题，不阻断 skill packaging，但会影响后续 agent 对当前研究状态的质量判断。

下一批候选：

- 将 forward-test checklist 固化为发布前回归流程，必要时增加自动化脚本封装。
- 继续扩展 dry-run coverage，让更多写入脚本支持安全预览。
- 增强 decision acceptance UI 修复提示，帮助用户补齐 alternatives、consequences 和 next actions。
- 规划前端图谱交互升级，为可点击展开节点、编辑节点文本和未来前端迁移保留清晰接口边界。

## Knowledge Search v1 更新（2026-04-28）
本批补齐 cockpit 内的轻量全文搜索能力，把 Markdown notes 和节点 YAML 文本字段纳入本地索引。范围保持克制：不引入数据库、搜索服务或新依赖，不反向解析 Markdown 状态，也不索引任意 linked file/config 正文。

已完成：

- 新增 `build_search_index(root, nodes, current=None)`，生成 `node` 与 `note` 两类搜索条目；note 会优先通过 `links.notes` 关联到节点，未关联 note 也会进入索引。
- 新增 `search_knowledge(...)` 和 `make_search_snippet(...)`，支持大小写不敏感匹配、稳定评分、片段提取、source/node type/focus-only/limit 过滤。
- 新增 `build_search_index_summary(...)`，用于 agent/context 和 Data Health 展示计数、focus 附近 entry 与未关联 note 数量，不把全文塞进 context pack。
- `scripts/build_dashboard.py` 新增输出 `research_cockpit/dashboards/search_index.json`；`agent_context_pack.json` 与 `focus_context_pack.json` 增加 `search_index_summary`。
- 新增 `scripts/search_knowledge.py`，支持 `--query`、`--json`、`--limit`、`--source note|node`、`--node-type` 和 `--focus-only`。
- UI 新增“搜索 / Search”页签，支持查询、来源过滤、节点类型过滤、focus-only、结果数量限制、结果表和 preview；Data Health 增加搜索索引摘要。
- README 已补充搜索 CLI、UI 页签、dashboard 输出和 agent 读取顺序说明。

下一批候选：

- decision acceptance checklist 已完成；后续可增加 UI 缺失项修复提示或 acceptance 历史记录。
- 增加 linked resource text indexing，但需要先明确哪些文件类型可安全读取、如何控制上下文体积。
- 增加 suggestion lifecycle cleanup，用于清理长期 orphan 的历史记录。

## Suggestion Lifecycle v1 更新（2026-04-28）

本批补齐 Action Guidance 的建议生命周期闭环，让用户可以把建议标记为 `dismissed` 或 `completed`，避免已处理建议反复出现。生命周期只写入 `current_state.yaml`，不执行建议命令，也不改变 experiment/decision/resource 的真实状态。

已完成：

- 每条 suggestion 增加稳定 `key`，由 `kind + source_node_id + action` 生成；展示用 `next_action_001` 仍保留。
- `current_state.suggestion_lifecycle` 支持记录 `state`、`reason`、`updated_at`、`action`、`kind` 和 `source_node_id`。
- `build_action_suggestions(...)` 默认只返回 active 建议；传入 `include_inactive=True` 时返回 dismissed/completed 历史并带 lifecycle metadata。
- 新增 `scripts/update_suggestion_state.py`，支持 `--state dismissed|completed|active`，可用当前展示 id 或稳定 key 定位建议。
- `scripts/suggest_next_actions.py` 增加 `--include-inactive` 和 `--state active|dismissed|completed|all`。
- Action Guidance UI 增加建议状态过滤、忽略、标记完成和恢复活跃按钮；dismissed/completed 建议不能写入行动队列。
- Data Health 增加 suggestion lifecycle 摘要，展示 active/dismissed/completed/orphan 数量；orphan 仅作为 warning，不阻断校验。

下一批候选：

- 增加 notes / Markdown 全文搜索或轻量索引。
- 增加 decision acceptance checklist，在接受 decision 前提示证据、替代方案和后续影响是否完整。
- 增加 suggestion lifecycle 清理脚本，用于删除长期 orphan 历史记录。

## Decision Evidence Injection v1 更新（2026-04-28）

本批把已有 experiment findings、`result_summary` 和 `outcome` 自动汇总到 decision，减少手工整理证据。范围保持只更新证据字段：不会自动接受 decision，也不会自动关闭 parent option/problem。

已完成：

- 新增 `build_decision_evidence_bundle(nodes, option_id, supporting_experiments=None)`，自动收集 option 下已有结构化证据实验，并保留手动传入的 supporting experiments。
- Evidence bundle 输出 `supporting_experiments`、`evidence_strength`、`evidence_summary`、`findings_count`、`outcome_counts` 和 `latest_finding`。
- `scripts/promote_decision.py` 新增 `--auto-evidence`，创建 decision 时可自动填充 supporting evidence；显式传入的非 `none` `--evidence-strength` 会优先保留。
- 新增 `scripts/update_decision_evidence.py`，用于刷新已有 proposed/accepted decision 的 evidence 字段，并默认重建 dashboard/context。
- Action Guidance 中 `review_decision` 的 `suggested_command` 已改为调用 `update_decision_evidence.py`，避免对已有 decision 再次生成同名 decision。
- Decision Trace 会展示自动生成的 evidence summary 文本，并继续展示 supporting experiments、finding 数量、outcome 分布和最新 finding。

下一批候选已推进到 Suggestion Lifecycle v1；剩余重点是全文搜索、decision acceptance checklist 和 lifecycle 历史清理。

## Action Execution v1 更新（2026-04-27）

本批在 Action Guidance v1 的只读建议基础上，补齐“建议入队”闭环。写回范围刻意保持最小：只把建议文本追加到 `current_state.next_actions` 或来源节点 `next_actions`，不直接执行实验、不自动改状态、不自动生成 decision。

已完成：

- 新增 `scripts/apply_suggestion.py`，支持 `--id <suggestion_id>`、`--target current|node` 和 `--no-build`。
- `build_action_suggestions(...)` 增加 `queued_in_current` 和 `queued_in_node`，用于标记建议是否已经进入行动队列。
- Action Guidance UI 增加写入当前行动队列、写入来源节点行动队列两个本地写回入口。
- 写回逻辑会去重；已存在的 action 不重复追加。
- 写回后更新 `updated_at`、运行统一校验，并默认重建 dashboard/context。

下一批候选已推进到 Decision Evidence Injection v1；剩余重点是 suggestion 生命周期、全文搜索和 decision acceptance checklist。

## Action Guidance v1 更新（2026-04-27）

本批新增只读行动建议和决策证据阅读能力，目标是让人和 agent 更快判断“下一步应该推进什么”，但不自动写回 `current_state.yaml` 或节点 YAML。

已完成：

- 新增 `build_action_suggestions(root, nodes, current, link_rows=None)`，从当前 focus、阻塞项、planned/done experiment、proposed decision 和缺失本地资源生成候选行动。
- 新增 `scripts/suggest_next_actions.py`，支持默认人类可读输出、`--json`、`--limit`、`--kind` 和 `--focus-only`。
- `scripts/build_dashboard.py` 新增输出 `research_cockpit/dashboards/next_action_suggestions.json`。
- `agent_context_pack.json` 和 `focus_context_pack.json` 增加 `suggested_next_actions`，保持只读。
- UI 新增“行动建议 / Action Guidance”页签，支持按建议类型、优先级和当前 focus 相关性过滤。
- Dashboard 首页展示 Top 3 建议；Data Health 展示缺失资源建议数量。
- Decision Trace 增加 evidence summary，汇总支持实验数量、findings 数量、结果分布和最新 finding。

当前 dashboard 输出文件为：

- `graph_view.json`
- `agent_context_pack.json`
- `focus_context_pack.json`
- `current_state.md`
- `current_state.json`
- `experiment_matrix.json`
- `linked_resources.json`
- `next_action_suggestions.json`

下一批候选：

- 允许用户从建议中选择一项写回 `current_state.yaml` 或对应节点。
- 增强 `promote_decision.py`，自动从 findings 汇总 supporting evidence。
- 增加全文搜索或 notes 内容索引。

## Notes/Search v1 更新（2026-04-27）

本批已把结构化 YAML 状态进一步连接到长篇研究记录和产物路径，重点是“可创建 note、可看资源、可搜节点”，仍然保持 YAML 为真相源，Markdown notes 只作为辅助长文本记录。

已完成：

- 新增 `scripts/create_note.py`：支持为 `problem`、`option`、`experiment`、`decision` 创建 Markdown 模板，并写回节点 YAML 的 `links.notes`。
- 新增 `build_link_rows(root, nodes)`：统一汇总 `links`、`linked_artifacts`、`config_path`、`path`、`run_id` 等资源引用。
- `scripts/build_dashboard.py` 新增输出 `research_cockpit/dashboards/linked_resources.json`。
- `node_context(...)` 增加标准化 `links`；`focus_context_pack.json` 的 `knowledge_index` 已纳入当前 focus 附近的 note/config/file 路径。
- UI 新增“资源 / Resources”页签，支持按节点类型、资源类型和存在状态过滤。
- Research Graph 节点详情选择器增加搜索框，支持按 `id/title/summary/tags/status/type` 查找节点。
- 节点详情 Summary 区将 `links` 展示为可读资源表；Actions 区为可支持 note 的节点显示 `create_note.py` 命令模板。
- Data Health 增加 linked resource 摘要；缺失本地资源路径仅作为 warning，不阻断 `validate_cockpit.py`。

当前 dashboard 输出文件为：

- `graph_view.json`
- `agent_context_pack.json`
- `focus_context_pack.json`
- `current_state.md`
- `current_state.json`
- `experiment_matrix.json`
- `linked_resources.json`

下一批候选：

- 新增 `suggest_next_actions.py`，基于 active problem、blocked node、缺失 evidence 和 stale experiment 生成候选下一步动作。
- 增强 `promote_decision.py`，从已有 findings 自动汇总 supporting evidence。
- 在 UI 中进一步突出 focus path 的下一步推进建议。
- 后续再考虑全文搜索和 Markdown 在线编辑；本阶段不引入。

## 当前阶段

当前项目处于 **Linked Resource Text Indexing v1 完成后的稳定化阶段**。第一批已完成 v2 schema 兼容层和 `focus_context_pack.json` 生成；第二批已完成 Focus Graph 数据增强和前端 Focus Mode 默认入口；第三批已完成 `set_focus.py --focus-node` 闭环；第四批已补齐前端一键设焦点、方案比较、决策追踪和可选显式边；第五批已补齐校验命令、实验 finding 记录和 decision 生成工作流；第六批已完成 note 模板创建、linked resources 索引、资源页和节点搜索；第七批已完成只读行动建议和决策证据摘要；第八批已完成建议写回行动队列的最小闭环；第九批已完成 decision evidence 自动注入和已有 decision 证据刷新；第十批已完成建议忽略/完成/恢复的生命周期闭环；第十一批已完成 notes + YAML 轻量全文搜索；第十二批已完成 decision 接受质量门；第十三批已完成本地 linked resource 正文索引。

已经从最初的 starter 原型推进到一个可运行、可验证的 repo-native 研究驾驶舱：

- YAML 节点仍然是研究状态的真相源。
- Python 脚本负责校验、维护和生成 dashboard/context 文件。
- Streamlit + PyVis 提供可交互图谱和表格视图。
- Agent 可以优先读取 `research_cockpit/dashboards/agent_context_pack.json` 获取当前上下文。
- 前端已支持中文/英文界面切换，默认中文。

## 已完成能力

### 数据与校验

- 支持 `stage`、`problem`、`option`、`experiment`、`decision`、`artifact` 节点。
- 对节点类型、状态枚举、`parent` / `children` 引用、`current_focus_path`、`current_focus_node` 和 `focus_mode` 做一致性校验。
- 状态枚举已兼容 v2 的 `experiment.cancelled`、`artifact.draft` 和 `artifact.archived`，同时保留旧数据可用状态。
- v2 研究字段会随 YAML 原样保留，并在 context 中提取常用字段，例如 evidence、blockers、next_actions 和 agent_context。
- experiment 节点支持结构化 `findings` 列表，用于记录实验观察、置信度、指标、关联 artifact 和结论方向。
- 图边生成会去重，避免同时写 `parent` 和 `children` 时出现重复边。
- 支持可选 `research_cockpit/graph/edges.yaml`，用于声明 `source`、`target`、`type`、`label` 和 `strength` 显式边；显式边会与 parent/children 派生边合并去重。

### 维护脚本

- `scripts/add_node.py`：新增节点，按节点类型设置默认状态，并校验父节点。
- `scripts/update_status.py`：更新节点状态、摘要和实验结果摘要。
- `scripts/set_focus.py`：更新当前主线，包括 stage/problem/option/focus node/focus path；传入 `--focus-node` 时可自动从 parent 链推导 focus path；默认会重建 dashboard/context 文件。
- `scripts/validate_cockpit.py`：独立运行数据健康检查，支持人类可读输出和 `--json` 输出。
- `scripts/record_finding.py`：向 experiment 节点追加结构化 finding，并可同步更新 `result_summary`。
- `scripts/promote_decision.py`：从 option 和 supporting experiments 生成 decision；支持 `--auto-evidence` 从已有 findings/result/outcome 自动填充证据；accepted decision 会同步更新 option/problem 状态。
- `scripts/update_decision_evidence.py`：刷新已有 decision 的 evidence 字段，不改变 decision status，也不关闭 option/problem。
- `scripts/suggest_next_actions.py`：只读生成下一步行动建议，支持 JSON、kind 过滤、limit 和 focus-only。
- `scripts/apply_suggestion.py`：将建议写入 `current_state.next_actions` 或来源节点 `next_actions`，不直接执行建议命令。
- `scripts/update_suggestion_state.py`：将建议标记为 dismissed/completed，或恢复为 active；只影响建议展示生命周期。
- `scripts/search_knowledge.py`：轻量搜索 Markdown notes 和节点 YAML 文本字段，支持 JSON、source/type、limit 和 focus-only 过滤。
- `scripts/build_dashboard.py`：生成前先校验，再输出 dashboard 和 agent context 文件。

### Dashboard 输出

当前会生成：

- `graph_view.json`
- `agent_context_pack.json`
- `focus_context_pack.json`
- `current_state.md`
- `current_state.json`
- `experiment_matrix.json`
- `linked_resources.json`
- `next_action_suggestions.json`
- `search_index.json`

这些文件是可再生成产物，但当前 MVP 建议保留在仓库中，方便人和 agent 快速读取上下文。

### 前端 UI

Streamlit 页面现在包含：

- 总览
- 研究图谱
- 方案比较
- 决策追踪
- 行动建议
- 搜索
- 资源
- 实验矩阵
- 决策
- Agent 上下文
- 数据健康

图谱页保留 PyVis 交互图，支持节点类型和状态过滤，并高亮当前 focus path。节点详情通过右侧选择器查看。前端已修复 Windows GBK 写 HTML 导致中文界面报错的问题。

第二批后，图谱页已成为默认首屏，并默认使用 Focus Depth 2：

- 读取 `current_focus_node`，当前焦点节点会使用红色粗边框并自动调用 PyVis `network.focus()` 聚焦。
- `graph_view.json` 中每个节点包含 `is_current_focus`、`in_focus_path`、`focus_role`、`focus_visible_depth`、`is_focus_visible` 和 `is_hidden_by_focus`。
- 默认隐藏 `focus_mode.hide_statuses` 中的状态，例如 rejected、parked、archived；用户仍可在状态过滤器中手动显示。
- 右侧节点详情默认选中当前 focus node，并按 Summary / Evidence / Actions / Agent Context 分区展示。
- 节点详情 Actions 区支持一键“设为当前焦点”，会写回 `current_state.yaml` 并重建 dashboard/context。
- 节点详情 Evidence 区展示 experiment findings。
- experiment 节点 Actions 区展示可复制的 `record_finding.py` 命令模板。
- option 节点 Actions 区展示可复制的 `promote_decision.py` 命令模板。
- problem/option/experiment/decision 节点 Actions 区展示可复制的 `create_note.py` 命令模板。
- 图谱详情选择器上方支持按 `id/title/summary/tags/status/type` 搜索节点。
- 方案比较页按当前 problem 汇总候选 option 的状态、证据强度、实验数量、最新结果、优缺点和拒绝原因。
- 决策追踪页展示 Decision -> Option -> Problem -> Stage 链路、支持实验、备选方案和 consequences。
- 决策追踪页展示 evidence summary，包括实验数量、findings 数量、结果分布和最新 finding。
- 行动建议页展示建议，可按 kind、priority 和当前 focus 相关性过滤；支持把建议写入当前行动队列或来源节点行动队列。
- 行动建议页支持按生命周期状态过滤，并可忽略、标记完成或恢复建议；非 active 建议不能写入行动队列。
- 搜索页支持对 Markdown notes 和节点 YAML 文本字段进行轻量全文检索，可按 source、node type、focus-only 和结果数量过滤，并展示结果 preview。
- 资源页汇总 notes、config、artifact path、run id 和 linked artifacts，并支持存在状态过滤。

### 测试

已有 `unittest` 测试覆盖：

- 有效 cockpit 数据校验。
- 非法状态、未知父节点、未知 focus path 的错误报告。
- 图边去重。
- Agent context 解析。
- Focus context 解析和 dashboard 输出。
- Focus Graph 元数据和前端 Focus Mode 过滤。
- CLI 写入行为，包括 `set_focus.py --focus-node` 后自动重建 dashboard/context。
- `set_focus.py --focus-node` 省略 `--path` 时自动推导路径。
- 可选 `edges.yaml` 的加载、校验、合并和图谱输出。
- 方案比较和决策追踪 helper 输出。
- 独立 `validate_cockpit.py` CLI 成功/失败退出码。
- `record_finding.py` 写入 experiment findings，并拒绝非法节点和未知 artifact。
- `promote_decision.py` 创建 proposed decision；accepted decision 同步更新 option/problem。
- `promote_decision.py --auto-evidence` 和 `update_decision_evidence.py` 的证据汇总、去重、显式强度保留和错误处理。
- Dashboard 生成文件。
- 中文节点图谱 HTML 生成。
- 行动建议生成、CLI 过滤、dashboard 输出和 UI helper。
- 建议 lifecycle 的稳定 key、dismissed/completed 过滤、CLI 写入、恢复和 Data Health 摘要。
- 搜索索引生成、notes 关联、YAML 字段检索、focus-only 过滤、CLI 查询、dashboard 输出和 UI helper。

当前验证命令：

```powershell
python -m unittest discover -s dev\tests
python scripts\build_dashboard.py
```

## 当前技术决策

- **暂不迁移 React Flow**：当前阶段优先稳定数据层、脚本和测试闭环；React Flow 留到图交互复杂度上升后再评估。
- **保留 YAML 作为真相源**：方便人工编辑、git diff、agent 读取和脚本生成。
- **dashboard 文件可提交**：虽然可再生成，但对 agent 上下文启动很有价值。
- **不强制 notes 联动**：节点可以声明 `links`，但当前 UI 只展示链接字段，不解析 Markdown 正文。
- **不引入额外测试框架**：当前使用标准库 `unittest`，避免为 MVP 增加依赖复杂度。

## 已知限制

- PyVis 图节点点击不能直接驱动右侧详情面板；当前通过选择器查看节点详情。
- 图布局仍依赖 force layout，大图规模上来后可读性会下降。
- 状态更新脚本只做结构化 YAML 修改，不会同步更新长篇 notes。
- 当前全文搜索只覆盖 Markdown notes 和节点 YAML 文本字段，尚未索引任意 linked file/config；也还没有标签过滤、时间线视图或历史变更视图。
- 暂未接入 MLflow、DVC、Git branch/commit 等外部研究产物。
- Streamlit 适合 MVP 和内网使用，若要长期作为主 UI，后续需要更完整的前端工程方案。

## 已完成规划：v2 P0 / P1 第一批

规划来源：

- `dev/specs/research_cockpit_v2_specs/README.md`
- `dev/specs/research_cockpit_v2_specs/docs/A_node_schema_v2.md`
- `dev/specs/research_cockpit_v2_specs/docs/B_ui_interaction_spec.md`
- `dev/specs/research_cockpit_v2_specs/schemas/node_v2.schema.yaml`

本阶段目标不是一次性完成完整 v2，而是在现有 Streamlit + PyVis + YAML + Python scripts 架构上补齐最关键的研究导航闭环：

> 打开 cockpit 后默认看到当前研究焦点、当前问题、活跃方案、相关证据、阻塞项和下一步动作；agent 能读取聚焦后的 context pack，而不必加载全局背景。

### 范围边界

本阶段纳入：

- v2 节点字段的兼容支持，包括 focus、evidence、agent_context 和研究工作流字段。
- `current_focus_node` 和 `focus_mode` 的读取、校验和默认值。
- 新增 `focus_context_pack.json` 生成。
- 研究图谱默认进入 Focus Mode，并提供 Depth 1 / Depth 2 / Global 视图。
- 右侧节点详情升级为 Summary / Evidence / Actions / Agent Context 四个信息区。
- 扩展 `set_focus.py`，让 CLI 能维护 `current_focus_node` 并重建 dashboard。
- 补充模型、生成器、脚本和 UI 相关测试。

本阶段暂不纳入：

- React Flow 迁移。
- MLflow、DVC、Git branch/commit 自动同步。
- 全量 YAML 数据迁移。
- 节点全文搜索、快捷键、minimap、saved filters。
- UI 内直接写回复杂节点内容。

### 实施任务拆分

1. **v2 schema 兼容层**（已完成）
   - 扩展状态白名单，支持 v2 状态，例如 experiment 的 `cancelled`、artifact 的 `draft` 和 `archived`。
   - 保留当前 YAML 兼容性，避免一次性迁移已有数据。
   - 校验 `current_focus_node`、`focus_mode.hide_statuses`、focus path 和引用字段。
   - 验收：旧数据仍通过校验；v2 示例字段能被加载并保留；非法 focus node 会给出清晰错误。

2. **Focus Context Pack 生成**（已完成）
   - 在模型层新增聚焦上下文构建逻辑，解析 focus node、focus path、父节点、子节点、兄弟方案、实验、决策、artifact、blockers 和 next actions。
   - `scripts/build_dashboard.py` 额外输出 `research_cockpit/dashboards/focus_context_pack.json`。
   - 验收：生成器输出 6 个 dashboard 文件；focus context 包含 `focus_node`、`focus_path`、`local_neighbors`、`current_best_option`、`blockers`、`next_actions` 和 `knowledge_index`。

3. **Focus Graph 数据**
   - 在 `graph_view.json` 中增加 focus 相关字段，例如 `is_current_focus`、`in_focus_path`、`focus_role`、`focus_priority`。
   - 支持按 Depth 1 / Depth 2 / Global 生成或过滤可见节点。
   - 默认隐藏与当前焦点无关的 rejected、parked、archived、resolved 节点。
   - 验收：Focus Mode 不再展示大量无关历史分支；当前 focus path 始终可见。

4. **UI P0 优化**
   - Research Graph 默认进入 Focus Mode。
   - 增加 Depth 1 / Depth 2 / Global 切换和图例。
   - 节点详情改为 Summary / Evidence / Actions / Agent Context 结构。
   - 保留 Streamlit 选择器作为详情查看入口；PyVis 点击驱动右侧面板可后置。
   - 验收：用户打开页面即可在 10 秒内识别当前问题、活跃方案、证据强度、阻塞项和下一步动作。

5. **Set Focus 闭环**（已完成）
   - 扩展 `scripts/set_focus.py`，支持 `--focus-node <id>`。
   - 设置焦点后重建 dashboard，确保 UI 和 agent context 同步。
   - UI 内按钮先作为后续增强；本阶段可以先展示明确的 CLI 命令提示。
   - 验收：通过 CLI 切换 focus 后，`current_state.json`、`graph_view.json`、`agent_context_pack.json` 和 `focus_context_pack.json` 同步变化。

6. **测试与文档**
   - 增加 v2 fixture，覆盖 v2 字段、状态和 current focus。
   - 增加 focus context、focus graph 过滤和 dashboard 输出测试。
   - 更新 README 的维护流程和 agent 读取顺序。
   - 验收：`unittest discover -s tests` 通过；`build_dashboard.py` 可稳定生成所有 dashboard 文件。

### 推荐执行顺序

第一批已完成：

1. v2 schema 兼容层。
2. `focus_context_pack.json` 生成。

这两项已经作为后续 Focus Mode UI 和 agent 聚焦工作的数据基础落地。

第二批已完成：

1. Focus Graph 数据增强。
2. Research Graph 默认 Focus Mode。
3. 右侧详情信息区重组。

第三批已完成：

1. `set_focus.py --focus-node` 闭环。
2. README 和状态文档补齐。

第四批已完成：

1. 前端一键设为当前焦点，并由脚本自动推导 focus path。
2. Branch Comparison / 方案比较只读视图。
3. Decision Trace / 决策追踪只读视图。
4. 可选 `graph/edges.yaml` 显式边加载、校验、合并和基础样式。

第五批已完成：

1. `validate_cockpit.py` 独立校验命令。
2. `record_finding.py` 实验观察记录工作流。
3. `promote_decision.py` 从 option/experiment 推进到 decision 的工作流。
4. Experiment Matrix、node context 和 UI 节点详情对 findings 的轻量展示。

第六批已完成：

1. `create_note.py` note 模板创建与 `links.notes` 写回。
2. `linked_resources.json` 资源索引输出。
3. Resources / 资源页、节点搜索和节点详情资源表。
4. `focus_context_pack.json` 的 `knowledge_index` 纳入当前 focus 附近的 note/config/file 路径。

第七批已完成：

1. `suggest_next_actions.py` 只读行动建议命令。
2. `next_action_suggestions.json` dashboard 输出。
3. Agent/focus context 中的 `suggested_next_actions`。
4. Action Guidance / 行动建议页和 Decision Trace evidence summary。

第八批已完成：

1. `apply_suggestion.py` 建议入队命令。
2. `queued_in_current` / `queued_in_node` 建议状态。
3. Action Guidance UI 中的写入当前行动队列和来源节点行动队列入口。

第九批已完成：

1. `build_decision_evidence_bundle(...)` 决策证据汇总。
2. `promote_decision.py --auto-evidence` 新 decision 证据自动填充。
3. `update_decision_evidence.py` 已有 decision 证据刷新。
4. Action Guidance 的 `review_decision` 建议命令切换为证据刷新命令。

第十批已完成：

1. suggestion 稳定 `key` 和 `current_state.suggestion_lifecycle`。
2. `update_suggestion_state.py` 建议忽略、完成和恢复命令。
3. `suggest_next_actions.py --include-inactive --state ...` 查询历史建议。
4. Action Guidance UI 生命周期过滤和写回入口。
5. Data Health suggestion lifecycle 摘要。

下一批建议进入“历史维护与决策体验增强”层：suggestion lifecycle 历史清理、decision acceptance checklist 后续增强、资源索引策略微调。

## 后续可优化

### 第一优先级：稳定性和可维护性

- 为节点 schema 写更明确的字段说明，降低手写 YAML 出错概率。
- 增加 dashboard 生成的快照测试或结构测试，防止 context pack 字段回退。
- 为 linked resources 增加更清晰的路径约定说明，例如仓库内相对路径、URL、外部路径和 run id 的区别。

### 第二优先级：研究工作流

- 增强 decision acceptance checklist，例如 UI 缺失项修复提示、acceptance 历史记录或更细的检查等级。
- 增加 suggestion lifecycle 清理脚本，删除长期 orphan 历史记录。
- 为 notes 增加推荐写作模板和命名约定，但仍不反向解析 Markdown 正文。

### 第三优先级：UI 交互

- 增加按 stage、priority、blocking、decision_state 的过滤。
- 资源页增加更明确的缺失资源修复入口，例如复制相对路径或跳转到节点详情。
- 增加当前 focus path 的专门视图，突出“现在应该推进什么”。
- 增强决策追踪视图：增加按 problem/option 过滤、证据强度排序和更清晰的链路布局。
- 如果图交互成为核心需求，再迁移到 React Flow。

### 第四优先级：外部系统同步

- 从 MLflow 同步实验状态、run id、metrics 和 artifact links。
- 从 DVC 或数据 manifest 同步 dataset artifact 节点。
- 从 Git branch/commit 同步代码变更证据。
- 支持导出 Mermaid 或静态 SVG，方便写周报和论文材料。

## 阶段验收标准

v2 P0 阶段完成后，应满足：

- 旧版 cockpit YAML 不需要全量迁移也能继续工作。
- v2 示例字段可以进入节点 YAML，并在 dashboard/context 中保留或展示。
- `focus_context_pack.json` 可生成，且 agent 能优先从其中读取当前局部上下文。
- Research Graph 默认展示当前焦点附近的图谱，而不是全局图。
- 当前 focus node、focus path、阻塞项、下一步动作在 UI 中清晰可见。
- `set_focus.py` 能维护当前焦点，并触发 dashboard/context 同步更新。
- 所有新增模型、脚本和生成器行为都有测试覆盖。

更长期的目标仍然是 **Research Workflow v1**：

> 让研究者完成一次完整闭环：发现问题 -> 建立方案 -> 运行实验 -> 记录结果 -> 形成决策 -> 更新当前主线。

## Decision Acceptance Checklist v1 更新（2026-04-28）

本批补齐 decision 接受前的硬质量门：默认只有证据、替代方案、后续影响和下一步行动都完整时，脚本才允许接受 decision。质量门只约束脚本工作流，不能阻止手工直接编辑 YAML。

已完成：

- 新增 `build_decision_acceptance_checklist(nodes, decision_id)` 和 `build_decision_acceptance_checklists(nodes)`，输出 `ready`、`checks`、`blocking_failures` 和 `warnings`。
- 新增 `scripts/check_decision_acceptance.py`，支持人类可读输出和 `--json`；存在 blocking failure 时退出码为 `1`。
- 新增 `scripts/accept_decision.py`，用于接受已有 proposed decision，并同步更新 parent option/problem；默认必须通过 checklist，`--force-accept` 仅作为人工确认的例外入口。
- `scripts/promote_decision.py --status accepted` 已接入同一套硬质量门，并新增 `--force-accept`。
- `scripts/update_status.py` 会拒绝直接把 decision 改为 `accepted`，避免绕过 option/problem 同步逻辑。
- `scripts/build_dashboard.py` 新增输出 `research_cockpit/dashboards/decision_acceptance_checklists.json`。
- Decision Trace 和 decision 节点详情会展示 acceptance checklist；decision 节点 Actions 区展示 `check_decision_acceptance.py` 和 `accept_decision.py` 命令模板。
- README 已补充检查和接受 decision 的维护流程说明。

下一批候选：

- linked resource text indexing：把受控类型的本地 linked files/config 纳入搜索索引。
- suggestion lifecycle cleanup：清理长期 orphan 的 suggestion lifecycle 历史记录。
- decision acceptance checklist 后续增强：增加 UI 中的缺失项修复提示或 acceptance 历史记录。

## Linked Resource Text Indexing v1 更新（2026-04-28）

本批把安全的本地 linked resources 正文纳入轻量搜索索引，让 Search 页和 agent 能检索 notes/YAML 之外的配置、数据说明和轻量文本产物。范围保持保守：不引入新依赖，不索引外部 URL、run id、绝对路径或二进制文件，也不把资源正文反向解析为结构化状态。

已完成：

- `build_search_index(...)` 新增 `source="resource"` 条目，复用 `build_link_rows(...)` 中的 `links.*`、`config_path` 和 `path` 等本地相对路径。
- 资源正文索引仅允许 `.md`、`.txt`、`.yaml`、`.yml`、`.json`、`.toml`、`.csv`、`.tsv`；`notes/**/*.md` 仍作为 `note` source，避免重复索引。
- 单个资源最多读取 128KB，超过时设置 `truncated=True`，并记录 `bytes_read`。
- 跳过 URL、run id、绝对路径、缺失文件、unsupported suffix、linked artifact id 等资源，并在 entry metadata 中记录 `skip_reason`，但不作为校验失败。
- `build_search_index_summary(...)` 增加 resource count、truncated count、skipped count 和 focus resource count。
- `scripts/search_knowledge.py --source resource` 已可搜索 indexed resource。
- Search 页会自动出现 resource source；Resources 页显示资源是否已索引、是否截断和跳过原因；Data Health 增加 resource text index 摘要。
- README 已补充资源正文索引范围、允许类型、128KB 截断策略和 `--source resource` 示例。

下一批候选：

- suggestion lifecycle cleanup：清理长期 orphan 的 suggestion lifecycle 历史记录。
- decision acceptance checklist 后续增强：增加 UI 缺失项修复提示或 acceptance 历史记录。
- resource indexing 策略微调：按项目需要评估是否纳入代码文件或更细的文件大小策略。

## Suggestion Lifecycle Cleanup v1 更新（2026-04-28）

本批补齐 suggestion lifecycle 的历史维护能力，重点是安全清理 `current_state.yaml` 中已经无法匹配当前建议 key 的 orphan 记录。它只影响建议历史展示，不执行建议命令，也不改变 experiment、decision 或 resource 的真实状态。

已完成：

- 新增 `build_suggestion_lifecycle_rows(...)`，汇总 lifecycle 明细并标记 `active_match`、`orphan`、`state`、`reason`、`updated_at`、`action`、`kind`、`source_node_id` 和 `age_days`。
- 新增 `scripts/cleanup_suggestion_lifecycle.py`，支持 `--dry-run`、`--state dismissed|completed|all`、`--older-than-days`、`--json` 和 `--no-build`。
- 默认只清理 orphan 记录；`--older-than-days` 遇到缺失或非法日期时不会误删。
- Data Health 会展示 orphan lifecycle 明细，并提供 dry-run 与真实清理命令模板；UI 不自动执行清理。
- README 已补充 lifecycle cleanup 的命令、dry-run 示例和边界说明。

下一批候选：

- decision acceptance checklist 后续增强：在 UI 中给出缺失项修复提示，或记录 acceptance 历史。
- resource indexing 策略微调：按项目需要评估是否纳入代码文件或调整大小策略。
- 前端图谱交互升级：为后续可点击展开、编辑节点文本和可能的前端迁移继续拆分接口边界。

## Agent Skill Packaging v1 更新（2026-04-28）

本批把项目整理为更适合 agent 调用和开源复用的 skill-ready 形态，重点去除本机私有解释器路径，统一使用通用 `python` 命令，并允许通过 `RESEARCH_COCKPIT_PYTHON` 覆盖。

已完成：

- 新增 `cockpit.model.python_command(...)` / `script_command(...)`，统一生成脚本命令模板；UI、action suggestions 和 context pack 不再写入本机私有 Python 路径。
- `agent_context_pack.json` 和 `focus_context_pack.json` 增加 `metadata`，包含 schema version、生成时间、git commit、worktree dirty 状态和 `current_state.updated_at`。
- 新增 `scripts/agent_bootstrap.py --json [--build]`，作为 agent 启动入口，输出 validation、focus、context 路径、Top suggestions、search summary 和 git 状态。
- 新增 `scripts/list_agent_commands.py --json`，列出主要脚本的用途、是否写入、是否支持 `--json` / `--dry-run` / `--no-build`。
- 新增根目录 `AGENTS.md` 和 `SKILL.md`，记录 agent 工作边界、读取顺序、写入规则和验证命令。
- 新增 `agents/openai.yaml`，为后续安装为 agent skill 提供展示名和默认说明。
- README 已补充 “Using as Agent Skill” 流程，所有公开命令示例改为通用 `python`。
- 新增公开化扫描测试，禁止 README、docs、`cockpit/`、`ui/`、`scripts/` 和 dashboard JSON 中出现真实本机路径或用户名。

下一批候选：

- dry-run coverage 扩展：让更多写入脚本支持预览变更。
- decision acceptance UI 修复提示：在 checklist 缺项时给出对应脚本或 YAML 字段建议。
- 前端图谱交互升级：为可点击展开节点、编辑节点文本和更换前端框架继续收拢数据接口。

## Open Skill Demo Data Split v1 更新（2026-04-28）

本批将可发布的 skill 运行时资产与私有研究资产拆开。`skills/research-cockpit/research_cockpit/` 包内现在只保留一组小型通用 demo graph，用来覆盖核心工作流，同时避免暴露个人研究内容。

已完成：

- 将包内私有研究节点替换为公开 demo 节点：`stage_demo_research`、`problem_demo_quality_gap`、`option_demo_prompt_refinement`、`option_demo_retrieval_branch`、嵌套 demo problem/option 节点、demo experiments 和一个 demo decision。
- 基于 demo graph 重新生成 dashboard/context 输出，确保导出的 context pack 不再包含私有项目术语。
- 更新 skill README 示例，改用通用 demo ID 和通用搜索查询。
- 扩展 release public scan，拒绝可发布 skill package 中出现已知私有研究术语。

备注：

- 这会清理当前工作树内容，便于后续打包。如果还要发布仓库历史，应使用导出归档或重写历史，避免旧提交中的私有资产可被恢复。
- 开发文档和测试仍保留在 skill package 外的 `dev/` 目录下。

## Skill Entry Documentation v1 更新（2026-04-28）

本批将 package `SKILL.md` 重写为面向 agent 的操作契约，适配项目内安装到 `.agent/skills/research-cockpit/` 或 `.codex/skills/research-cockpit/` 的场景。

已完成：

- 扩展 skill entry，补充 data-root 语义、demo data 注意事项、读取顺序、写入边界、工作流路由、关键 decision/option/suggestion 规则、subagent 验证边界、参考资料和验证命令。
- 同步 package README 和 `agents/openai.yaml`，匹配项目本地 skill 安装场景。

## Subagent Forward-Test Hardening v1 更新（2026-04-28）

本阶段补齐手动 subagent 测试暴露的缺口：agent 不再需要手动编辑 decision YAML，就能补齐非 evidence 类 acceptance checklist 字段。

本批已完成：

- 在可发布 skill package 中新增 `scripts/update_decision_checklist.py`。它会追加 `alternatives_considered`、`consequences` 和 `next_required_actions`，仅在显式传参时覆盖 `evidence_summary`，同时更新 `updated_at`、校验数据，并在未使用 `--no-build` 时重建 dashboard/context。
- 更新 agent command manifest、UI command template、`SKILL.md` 和 package README，使 decision gate 流程变为 `record_finding.py -> update_decision_evidence.py -> update_decision_checklist.py -> check_decision_acceptance.py -> accept_decision.py`。
- 新增开发侧 harness `dev/scripts/run_subagent_forward_check.py`，模拟只读 agent 启动、prompt refinement workstream 执行、retrieval branch 扩展、decision gate 补齐和复制包中的 portable skill 启动。
- 为新的 checklist writer 和 forward-check harness 增加测试。写入型 track 只在 `.test_tmp/subagent_runs/` 下操作，并断言原始 package 未被修改。

当前验证目标：

- `python -m unittest discover -s dev\tests`
- `python skills\research-cockpit\scripts\skill_smoke_test.py --json`
- `python dev\scripts\run_skill_release_check.py --json --skip-mutating`
- `python dev\scripts\run_subagent_forward_check.py --json`
- `python dev\scripts\run_subagent_forward_check.py --json --skip-mutating`

下一批候选：

- 为 decision acceptance 失败增加轻量 UI 修复提示。
- 为更多 mutating scripts 扩展 dry-run 覆盖。
- 考虑升级图谱交互，支持点击展开和更安全的节点文本编辑。
