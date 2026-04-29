# Research Cockpit

Research Cockpit 是一个 repo-native 的研究记录与 agent 协作插件。它把长期研究拆成可追踪的图谱节点：`stage`、`problem`、`option`、`experiment`、`decision` 和 `artifact`，并用本地 YAML / Markdown / JSON 保存研究状态、证据、决策和 agent 可读上下文。

这个仓库本身就是可安装的 agent skill / plugin。核心代码和工具在插件仓库内，真实研究项目只需要在仓库根目录保存自己的 `research_cockpit/` 状态资产。

## 适合什么场景

- 长期研究项目：问题、候选方案、实验、证据和决策需要持续积累。
- 人类研究者 + coding agent 协作：agent 先读取 context pack，再通过受控脚本写入研究状态。
- 本地优先记录：所有状态文件都可以进入 git diff、code review 和归档流程。
- 轻量数据层：当前接受 YAML / JSON 作为数据库，不依赖外部服务。

## 仓库组织

插件仓库：

```text
research-cockpit/
  src/research_cockpit/       # Python runtime、model、Streamlit UI、React Flow component wrapper
  capabilities/               # 面向 agent 的分能力说明
  templates/                  # 新研究仓库初始化模板
  examples/demo_research_cockpit/
  schemas/
  docs/
  tests/
  pyproject.toml
  SKILL.md                    # 薄入口
```

研究仓库：

```text
audio-edit-research/
  research_cockpit/           # 项目自己的研究状态资产
  .agent/skills/research-cockpit/
```

## 安装

建议使用 Python 3.10+。

```sh
git clone <this-repo-url> research-cockpit
cd research-cockpit
python -m pip install -e .
```

如果需要隔离环境，可以先创建并激活任意 Python virtual environment 或 conda environment，再执行同一条 `python -m pip install -e .`。

主要 Python 依赖：

- `pyyaml`：读写 YAML 状态。
- `pydantic`：结构校验。
- `networkx`：图谱关系处理。
- `pandas`：表格数据整理。
- `streamlit`：研究者前端。
- `pyvis`：legacy graph fallback。

## 前端依赖

Research Graph 默认使用 React Flow + Dagre。普通用户启动 Streamlit 不需要 Node.js，因为仓库提交了 production build。

只有修改前端组件源码时才需要重新构建：

```sh
cd src/research_cockpit/ui/graph_component/frontend
npm install
npm run build
```

主要前端依赖：

- `@xyflow/react`：React Flow 图谱交互。
- `dagre`：自动层级布局。
- `streamlit-component-lib`：Streamlit custom component 通信。
- `vite` / `typescript`：构建工具链。

图谱数据变化不需要重新 build。后台 agent 通过 `research-cockpit` CLI 修改 truth-source YAML 后，在前端点击 `刷新图谱 / Refresh` 即可让 Streamlit 重新读取当前 `research_cockpit/`。

## 初始化研究状态

在研究仓库根目录运行：

```sh
research-cockpit init
```

这会创建：

```text
research_cockpit/
  current_state.yaml
  graph/nodes/
  graph/edges.yaml
  graph/graph_views.yaml
  graph/interaction_log.yaml
  notes/
```

如果只在插件仓库内试用，可以直接使用 demo 数据：

```sh
research-cockpit smoke --root examples/demo_research_cockpit --json
```

## 启动前端

在研究仓库根目录运行：

```sh
research-cockpit build --root research_cockpit
research-cockpit ui --root research_cockpit --server.port 8501
```

前端主要页面：

- Research Graph：React Flow 图谱、节点点击、右侧 inspector、筛选、保存/加载视图、PyVis fallback。
- Dashboard：当前上下文、建议动作和数据摘要。
- Branch Comparison：比较同一 problem 下的 options。
- Decision Trace：查看 decision checklist、证据和修复提示。
- Option Workstreams：查看 agent 认领和回报的 option 分支工作。
- Search / Resources：搜索节点、notes 和本地 linked resources。
- Data Health：查看校验错误和资源缺失。

## 让 agent 使用

把本仓库复制或 vendoring 到研究仓库：

```text
your-research-repo/
  .agent/skills/research-cockpit/
  research_cockpit/
```

然后告诉 agent：

```text
使用 research-cockpit skill。先运行 `research-cockpit bootstrap --json`，
读取 agent_context_pack 和 focus_context_pack，再通过 `research-cockpit` CLI 写入研究状态。
```

推荐启动命令：

```sh
research-cockpit bootstrap --root research_cockpit --json
research-cockpit validate --root research_cockpit
```

所有关键写入都应走 `research-cockpit` CLI，不要让 agent 直接手写 YAML，除非对应 capability 明确允许并说明字段边界。

## 开发验证

```sh
python -m unittest discover -s tests
research-cockpit smoke --root examples/demo_research_cockpit --json
python dev/scripts/run_skill_release_check.py --json --skip-mutating
python dev/scripts/run_agent_usability_check.py --json
git diff --check
```
