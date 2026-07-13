# Spec: Graph Interaction Upgrade

> **Historical record:** this file preserves design or implementation context and is not current operational guidance. Current operational guidance: repository-root `README.md`, `SKILL.md`, `AGENTS.md`, `capabilities/`, and `docs/internal-architecture.md`.


## Assumptions

1. 主要用户是研究者本人，agent 是后续读取前端操作结果和研究状态的消费者。
2. 当前第一阶段继续保留 Streamlit 作为应用外壳；是否迁移 PyVis 只针对图谱区域评估，不重写整个应用。
3. 当前仓库的 repo-native YAML / JSON 数据可视为第一阶段的“数据库”；如果需要 SQLite、PostgreSQL 或其他外部数据库，需要单独确认。
4. 只读查看、临时筛选和临时点击选择不默认写入持久数据；保存视图、设置 focus、claim/report option、apply suggestion 等会改变研究状态的操作必须持久化。
5. 节点自由文本编辑不进入第一批 MVP；后续只有在字段级校验、预览和回滚边界明确后再做。

## Objective

把 Research Cockpit 的图谱页升级为面向长期研究记录的交互式图谱工作台。研究者需要通过点击、筛选、聚焦分支和安全操作快速理解当前研究状态；agent 需要能从持久化的 YAML / JSON 中读取这些交互结果，继续执行后续研究任务。

核心用户故事：

- 作为研究者，我可以点击或选择节点，立即查看节点摘要、证据、资源、行动建议、decision checklist 和原始 YAML。
- 作为研究者，我可以在 Current Focus、Current Branch、Option Workstream 和 Global 之间切换视图，避免大图失控。
- 作为研究者，我可以按 type、status、stage、priority、blocking、evidence、focus role 和 workstream 过滤节点。
- 作为研究者，我可以执行受控安全操作，例如 set focus、claim option、查看 option context、report option 和 apply suggestion。
- 作为 agent，我可以读取最新 focus、保存的图谱视图、操作日志和 dashboard context，而不需要猜测研究者刚才在前端做了什么。

## Recommended Direction

采用“两步走”方案：

1. **先强化 Streamlit 图谱工作台的数据接口和筛选体验。** 继续使用现有 `graph_view.json`、`option_workstreams.json` 和节点 YAML，补充交互所需的 view mode、filter facets、saved view 和 interaction log。这样能快速提高可用性，并为迁移图谱组件打基础。
2. **再替换 PyVis 图谱区域为双向交互组件。** 首选 React Flow / xyflow，因为它适合节点工作流 UI、受控状态和节点选择事件；如果后续发现节点规模和图分析能力比工作流交互更重要，再评估 Cytoscape.js。

不建议第一批直接做完整 React 前端应用。当前真正的瓶颈是图谱区域的点击、筛选、分支视图和持久化交互状态，不是 Streamlit 外壳本身。

参考资料：

- Streamlit Custom Components v2: https://docs.streamlit.io/develop/concepts/custom-components/components-v2
- React Flow interactivity: https://reactflow.dev/learn/concepts/adding-interactivity
- Cytoscape.js graph visualization: https://js.cytoscape.org/

## Tech Stack

当前栈：

- Python 3 标准脚本工作流。
- `streamlit>=1.32` 作为 UI 外壳。
- `pyvis>=0.3.2` 作为当前图谱渲染。
- `networkx>=3.2` 用于图结构生成。
- YAML 节点和 `current_state.yaml` 作为真相源。
- dashboard JSON 作为可再生成的 agent/UI 读取产物。

候选新增：

- Streamlit custom component，用于图谱区域的双向通信。
- React Flow / xyflow，用于节点选择、局部展开、视图控制和后续可选拖拽布局。
- Cytoscape.js 仅作为备选，适合大规模网络可视化、图分析和性能优先场景。

## Commands

开发和验证命令：

```powershell
python -m unittest discover -s dev\tests
python skills\research-cockpit\scripts\skill_smoke_test.py --json
python dev\scripts\run_skill_release_check.py --json --skip-mutating
python dev\scripts\run_subagent_forward_check.py --json --skip-mutating
streamlit run skills\research-cockpit\ui\app.py --server.address 0.0.0.0 --server.port 8501
```

如果后续引入前端 component，应在对应 component 目录补充：

```powershell
npm install
npm run build
npm run dev
```

这些命令只有在 component scaffold 落地后才成为必跑验证项。

## Project Structure

现有相关文件：

```text
skills/research-cockpit/ui/app.py
  Streamlit 页面、图谱 tab、节点详情和安全操作入口。

skills/research-cockpit/ui/view_helpers.py
  UI helper、图谱过滤、节点格式化、命令模板。

skills/research-cockpit/cockpit/model.py
  节点模型、graph_view 生成、focus metadata、option workstream、decision checklist。

skills/research-cockpit/scripts/build_dashboard.py
  重建 dashboard JSON 和 context pack。

skills/research-cockpit/research_cockpit/dashboards/graph_view.json
  当前图谱数据源。

skills/research-cockpit/research_cockpit/dashboards/option_workstreams.json
  option workstream 视图数据源。

dev/tests/test_ui.py
dev/tests/test_model.py
dev/tests/test_scripts.py
  当前测试入口。
```

建议新增或扩展：

```text
skills/research-cockpit/research_cockpit/graph/graph_views.yaml
  用户保存的图谱视图预设，例如当前分支、blocking 节点、某个 option workstream。

skills/research-cockpit/research_cockpit/graph/interaction_log.yaml
  持久化的前端安全操作日志，供 agent 判断最近的人类操作。

skills/research-cockpit/cockpit/graph_interactions.py
  图谱视图和交互日志的读写 helper。若实现很小，可先放在 model.py，后续再拆。

skills/research-cockpit/ui/graph_component/
  仅在迁移 PyVis 图谱区域时新增，承载 React Flow 或 Cytoscape component。
```

## Data Contract

### Graph View

`graph_view.json` 继续作为图谱渲染输入，但需要补充或稳定以下字段：

```yaml
nodes:
  - id: problem_demo_quality_gap
    label: Demo answer quality is inconsistent
    type: problem
    status: open
    priority: high
    parent: stage_demo_research
    stage_id: stage_demo_research
    focus_role: current
    focus_visible_depth: 0
    is_focus_visible: true
    is_hidden_by_focus: false
    has_blockers: false
    has_next_actions: true
    has_evidence: false
    option_workstream_id: null
edges:
  - from: problem_demo_quality_gap
    to: option_demo_prompt_refinement
    type: contains
current_focus_node: problem_demo_quality_gap
current_focus_path: [...]
available_filters:
  types: [...]
  statuses: [...]
  stages: [...]
  focus_roles: [...]
  workstreams: [...]
```

第一批实现可以只补最必要字段：`stage_id`、`has_blockers`、`has_next_actions`、`has_evidence`、`option_workstream_id` 和 `available_filters`。

### Saved Views

`graph_views.yaml` 建议结构：

```yaml
views:
  - id: current_branch_blockers
    title: Current branch blockers
    scope: current_branch
    filters:
      node_types: [problem, option, experiment, decision]
      statuses: [open, active, planned, proposed, blocked]
      focus_roles: [current, parent, child, sibling]
      only_blocking: true
      only_missing_evidence: false
      option_workstream_id: null
    created_at: "2026-04-28T00:00:00Z"
    updated_at: "2026-04-28T00:00:00Z"
```

### Interaction Log

`interaction_log.yaml` 建议结构：

```yaml
events:
  - id: "20260428T120000_set_focus_problem_demo_quality_gap"
    kind: set_focus
    actor: researcher
    node_id: problem_demo_quality_gap
    command: "python scripts/set_focus.py --focus-node problem_demo_quality_gap"
    before:
      current_focus_node: option_demo_prompt_refinement
    after:
      current_focus_node: problem_demo_quality_gap
    created_at: "2026-04-28T12:00:00Z"
```

必须记录的事件：

- `set_focus`
- `claim_option`
- `report_option`
- `apply_suggestion`
- `accept_decision`
- `save_graph_view`

不默认记录的事件：

- 临时点击节点
- 临时搜索
- 临时筛选
- 展开/折叠临时视图

如果研究者点击“保存当前视图”，再记录为 `save_graph_view`。

## Interaction Model

### View Modes

- `Current Focus`：只看当前 focus node、父节点、子节点、兄弟节点和直接证据。
- `Current Branch`：显示当前 focus path 及其下游 active/open 分支。
- `Option Workstream`：选择一个 option，显示该 option 子树、实验、decision、report 和 upstream problem。
- `Global`：显示全局图，但默认仍应用隐藏 archived/rejected/parked 的过滤。

### Filters

P0 过滤：

- node type
- status
- focus role
- stage
- option workstream
- has blockers
- has next actions
- has evidence / missing evidence

P1 过滤：

- priority
- resource existence / indexed resource
- decision acceptance ready / not ready
- stale experiment
- owner / agent

### Node Inspector

点击或选择节点后，右侧详情至少包含：

- Summary：type、status、priority、tags、summary、parent/children。
- Evidence：findings、evidence summary、supporting experiments、decision checklist。
- Resources：notes、config、artifact path、run id、indexed resource 状态。
- Actions：set focus、record finding、promote/update/check/accept decision、claim/report option、create note。
- Agent Context：key files、key questions、next action hint、raw YAML。

### Safe Operations

安全操作必须遵循：

- 所有写入都走现有脚本或同等 model helper，不在 UI 中手写 YAML 字符串。
- 写入前显示命令或变更摘要。
- 写入后运行校验并重建 dashboard/context。
- 写入后追加 interaction log。
- 失败时不部分写入；若脚本已写入但 rebuild 失败，必须在 UI 中明确显示失败状态。

## Code Style

保持现有 Python helper 风格：小函数、纯过滤逻辑优先、UI 只做组合和展示。

示例：

```python
def filter_graph_for_interaction(graph: dict, filters: dict) -> dict:
    selected = []
    included = set()
    for node in graph.get("nodes", []):
        if not graph_node_matches_filters(node, filters):
            continue
        selected.append(node)
        included.add(node["id"])

    return {
        **graph,
        "nodes": selected,
        "edges": [
            edge for edge in graph.get("edges", [])
            if edge.get("from") in included and edge.get("to") in included
        ],
    }
```

约定：

- UI 文案用中文为主，保留 API、字段名、命令参数英文。
- filter / format / command builder 继续放在 `ui/view_helpers.py`，除非逻辑需要被脚本复用。
- 持久化读写逻辑放在 `cockpit` 层，不直接塞进 Streamlit callback。
- 新增 YAML 字段必须有测试和 README / spec 说明。

## Testing Strategy

单元测试：

- `filter_graph_for_interaction(...)` 能组合 type/status/stage/focus/workstream/blocking/evidence 过滤。
- `graph_views.yaml` 读写能保留未知字段、校验必填字段、拒绝重复 id。
- `interaction_log.yaml` append 行为稳定、按时间排序、不会覆盖已有事件。
- `graph_view.json` 新增 filter facets 后仍兼容旧字段。

脚本测试：

- 保存视图脚本或 helper 写入后能触发校验。
- set focus / claim / report / apply suggestion 后能追加 interaction log。
- `build_dashboard.py` 输出包含图谱交互需要的字段。

UI helper 测试：

- view mode 能生成正确过滤条件。
- 节点 inspector 行格式稳定。
- 空结果时显示清晰提示，不抛异常。

手工验证：

- 启动 Streamlit 后，能在 30 秒内从当前 focus 找到相关 option、experiment、decision 和资源。
- 在 Option Workstream 视图中，只显示选中 option 的子树和 upstream problem。
- 执行 set focus 后，`current_state.yaml`、dashboard JSON 和 `interaction_log.yaml` 同步更新。

## Boundaries

Always:

- 保留 YAML 节点和 `current_state.yaml` 作为第一阶段真相源。
- 写入操作必须经过校验，并重建 dashboard/context。
- 新增持久数据结构必须有测试。
- 大图默认进入 Current Focus 或 Current Branch，不默认渲染全局。

Ask first:

- 引入 React Flow、Cytoscape.js 或任何 npm 依赖。
- 新增 SQLite、PostgreSQL 或其他外部数据库。
- 改变节点 YAML schema 的必填字段。
- 允许节点正文或 YAML 字段在线编辑。
- 引入前端构建链到 release package。

Never:

- 不在 UI 中直接拼接并写入未校验 YAML。
- 不把每一次临时点击和临时筛选都写入日志。
- 不在第一批删除 PyVis 旧入口，除非新组件已通过验收。
- 不绕过 decision acceptance、option workstream 和 suggestion lifecycle 现有脚本边界。

## Success Criteria

P0 完成后必须满足：

- 图谱页支持 Current Focus、Current Branch、Option Workstream、Global 四种视图。
- 节点详情能覆盖 Summary、Evidence、Resources、Actions、Agent Context。
- 至少支持 type、status、focus role、stage、option workstream、blocking、missing evidence 过滤。
- 至少 `set_focus` 作为安全写入操作可从前端执行，并写入 interaction log。
- agent 能从 dashboard/context 或 interaction log 读到最近一次持久化图谱操作。
- `python -m unittest discover -s dev\tests` 通过。
- `python skills\research-cockpit\scripts\skill_smoke_test.py --json` 通过。

P1 完成后应满足：

- React Flow 或 Cytoscape component spike 给出明确结论：保留 PyVis 增强、迁移 React Flow，或迁移 Cytoscape。
- 如果迁移，节点点击能直接驱动右侧 inspector，不再依赖 selectbox 作为主交互。
- 保存视图和 interaction log 能被 agent bootstrap 或 context pack 摘要读取。

## Implementation Plan

### Phase 1: 数据接口和只读探索增强

目标：不迁移 PyVis，先补齐 filter facets、view modes 和 inspector 信息结构。

实现内容：

- 扩展 `graph_view.json` 的节点 facet 字段。
- 扩展 `filter_graph_for_view(...)` 或新增 `filter_graph_for_interaction(...)`。
- 在图谱页加入 Current Branch 和 Option Workstream view mode。
- 增强右侧 node inspector 的资源、证据和关联节点信息。

验证：

- unit tests 覆盖过滤组合。
- 手工打开 Streamlit 检查四种 view mode。

### Phase 2: 持久化视图和安全操作日志

目标：让前端产生的研究状态改变能被 agent 后续读取。

实现内容：

- 新增 `graph_views.yaml` 和 `interaction_log.yaml` 读写 helper。
- 保存当前筛选为 view preset。
- `set_focus` UI 操作成功后追加 interaction log。
- 后续再接入 claim/report/apply suggestion。

验证：

- 写入 helper 测试。
- set focus 后检查 current state、dashboard 和 interaction log。

### Phase 3: 图谱组件迁移 spike

目标：用最小 POC 验证 PyVis 是否应被替换。

实现内容：

- 创建一个只读 custom component spike，接收 `graph_view.json` 子集并返回 selected node id。
- 首选 React Flow；若大图性能明显不足，再用 Cytoscape.js 对比。
- 不在 spike 中做写入操作，只验证点击、筛选、局部视图和事件回传。

验证：

- 点击节点能同步 Streamlit 侧 selected node id。
- 100+ 节点样例仍可接受。
- 构建命令和运行命令稳定。

### Phase 4: 迁移正式化

目标：如果 spike 成立，把图谱区域切到新 component，同时保留 PyVis fallback。

实现内容：

- React Flow / Cytoscape component 接入正式数据 contract。
- 支持节点点击、局部展开、fit view、search highlight。
- 保留 PyVis legacy toggle，直到新组件稳定。

验证：

- UI helper tests + component build。
- 手工检查 desktop viewport。
- release check 不把 dev-only 构建缓存打入 package。

## Tasks

- [ ] Task: 扩展 graph view facet 数据
  - Acceptance: `graph_view.json` 节点包含 stage、blocking、next action、evidence、workstream 等过滤字段。
  - Verify: `python -m unittest discover -s dev\tests`
  - Files: `cockpit/model.py`, `dev/tests/test_model.py`

- [ ] Task: 增强 Streamlit 图谱 view modes 和过滤
  - Acceptance: UI 支持 Current Focus、Current Branch、Option Workstream、Global 和 P0 过滤项。
  - Verify: `python -m unittest discover -s dev\tests`; 手工启动 Streamlit。
  - Files: `ui/app.py`, `ui/view_helpers.py`, `dev/tests/test_ui.py`

- [ ] Task: 增强 node inspector
  - Acceptance: 节点详情展示 Summary、Evidence、Resources、Actions、Agent Context，并对空字段友好。
  - Verify: `python -m unittest discover -s dev\tests`
  - Files: `ui/app.py`, `ui/view_helpers.py`, `dev/tests/test_ui.py`

- [ ] Task: 增加 saved views 和 interaction log 持久化
  - Acceptance: 可保存视图预设；set focus 成功后追加 interaction log。
  - Verify: `python -m unittest discover -s dev\tests`
  - Files: `cockpit/graph_interactions.py`, `ui/app.py`, `dev/tests/test_model.py`, `dev/tests/test_ui.py`

- [ ] Task: 将 interaction summary 暴露给 agent
  - Acceptance: agent context 或 bootstrap 输出能看到最近持久化图谱操作摘要。
  - Verify: `python skills\research-cockpit\scripts\skill_smoke_test.py --json`
  - Files: `cockpit/model.py`, `scripts/agent_bootstrap.py`, `dev/tests/test_scripts.py`

- [ ] Task: 图谱 component migration spike
  - Acceptance: React Flow 或 Cytoscape spike 能返回 selected node id，并给出迁移/保留结论。
  - Verify: component build 命令；手工 Streamlit 点击验证。
  - Files: `ui/graph_component/`, `ui/app.py`, `dev/docs/development_status.md`

## Open Questions

1. 第一阶段是否确认 YAML / JSON 持久化就是当前“数据库”，还是必须引入 SQLite / PostgreSQL？
2. P0 允许的安全操作是否只包含 `set_focus`，还是同时包含 `claim_option`、`report_option_workstream` 和 `apply_suggestion`？
3. 是否需要持久化节点位置和手动布局？如果需要，应放在 saved view 中，而不是写入节点 YAML。
4. 大图验收样例按多少节点评估：100、300，还是 1000？
5. 图谱 component spike 是否优先 React Flow，Cytoscape.js 仅作为性能备选？

## Review Gate

已确认（2026-04-28）：

- 第一阶段接受 YAML / JSON 作为持久化数据库。
- 后续允许新增 npm component spike 依赖。
- 本规格作为后续任务拆分和验收依据。

仍需在对应阶段确认：

- P0 safe operations 已先落地 `set_focus`；claim/report/apply suggestion/accept decision 接入 interaction log 前需逐项确认写入边界。
- React Flow / Cytoscape spike 开始前，需要确认 component 目录、构建产物是否进入 skill package。
