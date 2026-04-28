# Research Cockpit

Research Cockpit 是一个 repo-native 的研究记录与 agent 协作工具。它把长期研究过程拆成可追踪的图谱节点：stage、problem、option、experiment、decision 和 artifact，并用本地 YAML / Markdown / JSON 保存研究状态、证据、决策和 agent 可读上下文。

这个目录本身就是一个可安装的 agent skill 包。你可以把它放进自己的项目仓库，让人类研究者通过 Streamlit 前端查看和整理研究图谱，让 coding agent 通过脚本安全地读取、更新和记录研究过程。

## 适合什么场景

- 长期研究项目：问题、候选方案、实验、证据和决策需要持续积累。
- 多 agent 协作：不同 agent 可以认领 option workstream，记录发现，再回报建议。
- 研究可追踪：关键写入通过脚本完成，生成 agent context pack，并写入 interaction log。
- 本地优先：研究状态保存在仓库内，便于 git diff、代码审查、归档和迁移。

Research Cockpit 不是实时多人数据库，也不是不可篡改审计系统。当前设计接受 YAML / JSON 作为轻量数据库，适合个人或小团队在 repo 中管理研究状态。

## 核心概念

- `research_cockpit/graph/nodes/*.yaml`：研究图谱节点，是结构化研究状态的 truth source。
- `research_cockpit/current_state.yaml`：当前 stage、problem、option 和 focus path。
- `research_cockpit/notes/`：长文研究笔记，通过节点 `links` 关联。
- `research_cockpit/graph/interaction_log.yaml`：关键人类和 agent 操作摘要。
- `research_cockpit/graph/graph_views.yaml`：研究者保存的图谱视图预设，不是冻结快照。
- `research_cockpit/dashboards/*.json`：由脚本生成的前端和 agent context 输出。

推荐工作方式是：YAML 记录事实，Markdown 记录长推理，Python scripts 负责校验和写入，Streamlit 前端负责浏览和安全入口，agent 先读 context pack 再调用脚本。

## 安装

### 1. 安装为项目本地 skill

把整个 `research-cockpit` 目录复制到你的项目仓库中，例如：

```text
your-project/
  .codex/
    skills/
      research-cockpit/
```

也可以使用 `.agent/skills/research-cockpit/`，取决于你的 agent 运行环境如何发现本地 skills。

包内自带的 `research_cockpit/` 是公开 demo 数据。用于真实研究前，建议复制这套 demo 作为模板，或替换为你自己的 `research_cockpit/` 数据目录。不要把旧私有研究历史直接开源发布。

### 2. 安装 Python 依赖

建议使用 Python 3.10+。

```powershell
cd .codex\skills\research-cockpit
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

主要 Python 依赖：

- `pyyaml`：读写 YAML 数据。
- `pydantic`：数据结构校验。
- `networkx`：图谱关系处理。
- `pandas`：表格数据整理。
- `streamlit`：前端应用。
- `pyvis`：legacy graph fallback。

如果你的 agent 环境使用固定解释器，可以设置：

```powershell
$env:RESEARCH_COCKPIT_PYTHON="D:\path\to\python.exe"
```

生成的命令模板会优先使用这个解释器。

### 3. React Flow 前端依赖

默认包内已经提交了 `ui/graph_component/frontend/build/`，普通用户启动 Streamlit 不需要 Node.js。

只有当你修改 React Flow 组件源码时，才需要安装和重新 build 前端：

```powershell
cd ui\graph_component\frontend
npm.cmd install
npm.cmd run build
```

主要前端依赖：

- `@xyflow/react`：React Flow 图谱组件。
- `dagre`：自动层级布局。
- `react` / `react-dom`：组件运行时。
- `streamlit-component-lib`：Streamlit custom component 通信。
- `vite` / `typescript`：前端构建工具链。

图谱数据变化不需要重新 build。后台 agent 修改 YAML 后，在前端点击 `刷新图谱 / Refresh` 即可让 Streamlit 重新读取当前数据。

## 启动前端

从 skill 根目录运行：

```powershell
python scripts\build_dashboard.py
streamlit run ui\app.py --server.address 0.0.0.0 --server.port 8501
```

远程服务器可以用 SSH 转发：

```bash
ssh -L 8501:localhost:8501 user@remote-gpu-server
```

前端主要页面：

- Research Graph：默认入口。使用 React Flow + Dagre 显示研究图谱，支持节点点击、拖拽、搜索、右侧 inspector、筛选、保存/加载视图和 PyVis fallback。
- Dashboard：当前上下文、建议动作和数据摘要。
- Branch Comparison：比较同一 problem 下的候选 options。
- Decision Trace：查看 decision checklist、证据和修复提示。
- Action Guidance：查看由当前研究状态生成的 next action suggestions。
- Option Workstreams：查看 agent 认领和回报的 option 分支工作。
- Search / Resources：搜索 YAML 节点、notes 和本地 linked resources。
- Data Health：查看校验问题和资源缺失状态。

前端只直接执行受控的安全操作，例如设置当前 focus、保存图谱视图。实验发现、decision 接受、option 认领等写入仍通过 scripts 完成。

## 让 agent 使用 Research Cockpit

把任务交给 agent 时，可以直接要求它使用 `research-cockpit` skill。例如：

```text
使用 research-cockpit skill。先运行 agent_bootstrap.py --json，
读取 agent_context_pack 和 focus_context_pack，然后围绕当前 focus
提出下一步研究计划。需要写入状态时先 dry-run，再真实执行脚本。
```

推荐 agent 启动顺序：

```powershell
python scripts\agent_bootstrap.py --json
python scripts\list_agent_commands.py --json
```

如果是新复制的包，先跑一次只读 smoke test：

```powershell
python scripts\skill_smoke_test.py --json
```

agent 应优先读取：

```text
research_cockpit/dashboards/agent_context_pack.json
research_cockpit/dashboards/focus_context_pack.json
```

需要更多上下文时，再调用搜索：

```powershell
python scripts\search_knowledge.py --query "answer quality" --json --focus-only
```

## 常用研究工作流

### 添加研究节点

```powershell
python scripts\add_node.py --id experiment_new --type experiment --title "New Experiment" --parent option_demo_prompt_refinement
python scripts\update_status.py --id experiment_new --status running
```

### 记录实验发现

```powershell
python scripts\record_finding.py --experiment experiment_demo_prompt_refinement --statement "The refined prompt improves consistency on the demo cases." --confidence medium --outcome positive --metric consistency_score --summary "Improved answer consistency."
```

### 让 agent 认领一个 option 分支

先预览，再写入：

```powershell
python scripts\claim_option.py --option option_demo_prompt_refinement --agent agent_demo --objective "Evaluate the prompt refinement branch." --dry-run --json
python scripts\claim_option.py --option option_demo_prompt_refinement --agent agent_demo --objective "Evaluate the prompt refinement branch."
python scripts\option_workstream_context.py --option option_demo_prompt_refinement --json
```

回报分支工作：

```powershell
python scripts\report_option_workstream.py --option option_demo_prompt_refinement --agent agent_demo --recommend continue --summary "Promising evidence, but more evaluation is needed." --dry-run --json
python scripts\report_option_workstream.py --option option_demo_prompt_refinement --agent agent_demo --recommend continue --summary "Promising evidence, but more evaluation is needed."
```

### 从 option 生成和接受 decision

```powershell
python scripts\promote_decision.py --id decision_demo_prompt_refinement_v2 --option option_demo_prompt_refinement --title "Adopt prompt refinement branch" --summary "Use the refined prompt as the default demo workflow." --status proposed --auto-evidence --dry-run --json
python scripts\promote_decision.py --id decision_demo_prompt_refinement_v2 --option option_demo_prompt_refinement --title "Adopt prompt refinement branch" --summary "Use the refined prompt as the default demo workflow." --status proposed --auto-evidence
python scripts\check_decision_acceptance.py --id decision_demo_prompt_refinement_v2 --json
python scripts\accept_decision.py --id decision_demo_prompt_refinement_v2 --dry-run --json
python scripts\accept_decision.py --id decision_demo_prompt_refinement_v2
```

decision 接受前需要满足 checklist：支持实验、证据内容、`evidence_strength`、`evidence_summary`、备选方案、后果和后续行动。不要直接用 `update_status.py` 把 decision 改成 `accepted`。

### 使用 action suggestions

```powershell
python scripts\suggest_next_actions.py --json
python scripts\apply_suggestion.py --id next_action_001 --dry-run --json
python scripts\apply_suggestion.py --id next_action_001
python scripts\update_suggestion_state.py --id next_action_001 --state completed --reason "Handled in current pass."
```

suggestions 是建议，不是事实。只有用户或任务明确要求时，agent 才应执行或入队建议。

## 维护和验证

```powershell
python scripts\validate_cockpit.py
python scripts\build_dashboard.py
python scripts\skill_smoke_test.py --json
```

在开发仓库中还可以运行外部测试：

```powershell
python -m unittest discover -s dev\tests
```

如果修改 React Flow 源码，还要运行：

```powershell
cd ui\graph_component\frontend
npm.cmd run build
```

## 目录结构

```text
research_cockpit/
  current_state.yaml
  graph/
    nodes/*.yaml
    edges.yaml
    graph_views.yaml
    interaction_log.yaml
  notes/
  dashboards/
cockpit/
  model.py
scripts/
  agent_bootstrap.py
  list_agent_commands.py
  validate_cockpit.py
  build_dashboard.py
  add_node.py
  update_status.py
  set_focus.py
  claim_option.py
  report_option_workstream.py
  record_finding.py
  promote_decision.py
  update_decision_evidence.py
  update_decision_checklist.py
  check_decision_acceptance.py
  accept_decision.py
  suggest_next_actions.py
  apply_suggestion.py
  search_knowledge.py
ui/
  app.py
  graph_component/
SKILL.md
AGENTS.md
requirements.txt
agents/
references/
```

## 开源发布说明

当前包内 demo 数据是公开示例，适合展示工作流。真实研究项目发布前，请确认：

- `research_cockpit/` 中没有私有研究内容、路径、账号、token 或内部资源。
- git 历史中没有曾经提交过的私有数据；如需要公开整个仓库，请使用导出包或历史清理。
- `node_modules/`、`.test_tmp/`、`__pycache__/` 等生成目录没有被提交。
