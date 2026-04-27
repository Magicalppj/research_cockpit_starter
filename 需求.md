本项目要解决的其实是四件事，同时服务 **人** 和 **agent**：

1. **如何组织研究信息**
   不让项目知识散落在聊天记录、commit、实验日志、脑子里。

2. **如何更新状态**
   每次做完一个实验、一个分支决策、一个数据集版本更新，都能自然沉淀下来。

3. **如何让 AI 快速了解任务上下文**
   agent 不需要读完整个仓库，也能立刻知道“当前主线是什么、卡点是什么、有哪些候选方案”。

4. **如何让你自己一眼看到现在处于哪一步**
   就像一个可交互的多叉树 / 决策图：
   当前阶段、当前问题、每个问题有哪些解决方案、每个方案验证到哪一步、结论是什么。

---

# 我建议的核心思想

不要把系统设计成“文档仓库”，而要把它设计成：

> **图结构的研究状态机（graph-structured research state machine）**

也就是说，你的研究对象不是“文件”，而是一些**节点（nodes）**和**边（edges）**：

* 一个**阶段**是节点
* 一个**问题**是节点
* 一个**候选方案**是节点
* 一个**实验**是节点
* 一个**结论/决策**是节点
* 它们之间有连接关系

例如：

```text
Stage: Better text control
  -> Problem: Gemma event encoding is weak
      -> Option A: FLAN-T5 event branch
      -> Option B: UMT5 event branch
      -> Option C: CLAP semantic anchor
          -> Experiment E031
          -> Experiment E032
              -> Decision: Adopt FLAN-T5 + CLAP
```

这比单纯的 Markdown 文件树更适合你想要的**可视化多叉树 / 分支流程图**。

---

# 一、信息模型：到底要记录什么

我建议定义 6 类核心对象。

---

## 1. Stage（阶段）

表示你当前研究的大阶段，例如：

* Dataset construction
* Edit model formulation
* Text encoder redesign
* Backbone comparison
* Paper preparation

字段建议：

```yaml
id: stage_text_encoder
type: stage
title: Text Encoder Redesign
status: active   # planned / active / blocked / done
priority: high
summary: Improve event-level text understanding for edit-program audio generation.
children:
  - problem_event_text_weak
```

---

## 2. Problem（问题）

表示当前需要解决的一个核心问题。

例如：

* Event text not followed
* Remove under overlap is weak
* Dataset edit supervision insufficient
* TTS should or should not be part of main network

字段建议：

```yaml
id: problem_event_text_weak
type: problem
title: Event-level text control is weak
status: active
stage: stage_text_encoder
question: Why does the model ignore fine-grained old/new event text?
impact: critical
parent: stage_text_encoder
children:
  - option_flan_t5
  - option_umt5
  - option_clap_anchor
current_best_option: option_flan_t5_clap
```

---

## 3. Option / Branch（候选方案 / 分支）

表示某个问题下的解决方案分支。

例如：

* FLAN-T5 event branch
* UMT5 event branch
* FLAN-T5 + CLAP
* Keep Gemma only

字段建议：

```yaml
id: option_flan_t5_clap
type: option
title: FLAN-T5-XL token branch + CLAP anchor
status: active
problem: problem_event_text_weak
hypothesis: FLAN-T5 improves token-level understanding; CLAP improves audio-semantic alignment.
pros:
  - stronger event text understanding
  - explicit audio-semantic anchor
cons:
  - requires new timeline feature cache
  - extra training complexity
children:
  - exp_041_t5_only
  - exp_042_t5_clap
decision_state: promising   # open / promising / rejected / accepted
```

---

## 4. Experiment（实验）

这是验证某个方案的具体证据节点。

例如：

* E041: LTX + FLAN-T5 only
* E042: LTX + FLAN-T5 + CLAP
* E043: Compare replace-following on v2_150

字段建议：

```yaml
id: exp_042_t5_clap
type: experiment
title: FLAN-T5-XL + CLAP event branch on v2_150
status: running   # planned / running / done / failed
option: option_flan_t5_clap
dataset: dataset_v2_150
backbone: ltx23_audio_branch
metrics:
  - local_edit_following
  - clap_text_alignment
  - preserve_score
result_summary: null
links:
  config: experiments/exp_042.yaml
  run_log: mlflow://run/abc123
  notes: notes/exp_042.md
```

实验节点最重要的作用是：

> 它把“一个想法”变成“有证据支持或反对的想法”。

---

## 5. Decision（决策 / ADR）

表示某个问题最终阶段性收敛的结论。

例如：

* Adopt unified edit-program formulation
* Do not model TTS add in the main network
* Keep Gemma for global prompt, add FLAN-T5 + CLAP for event branch

字段建议：

```yaml
id: decision_text_encoder_flan_clap
type: decision
title: Adopt FLAN-T5-XL + CLAP for event branch
status: accepted
derived_from:
  - option_flan_t5_clap
supporting_experiments:
  - exp_041_t5_only
  - exp_042_t5_clap
summary: FLAN-T5 improves token-level event control; CLAP provides a useful audio-semantic anchor.
consequences:
  - regenerate timeline features
  - modify Semantic Ribbon initializer
  - keep Gemma prompt path unchanged
```

---

## 6. Artifact（产物）

表示一个实际对象：

* 数据集版本
* 模型配置
* 图
* 论文草稿
* checkpoint
* manifest

例如：

```yaml
id: dataset_v2_150
type: artifact
artifact_type: dataset
title: Audio Edit Dataset v2_150
status: active
summary: 150 editable classes, 7.2k atoms, 20k+ edit pairs
links:
  spec: datasets/v2_150.yaml
  figure: figures/dataset_pipeline_v2_150.png
```

---

# 二、文件组织：如何落地到 repo

我建议你用 **“图节点 + 详细文档 + 自动生成视图”** 的组织方式。

---

## 推荐目录结构

```text
research_cockpit/
  graph/
    nodes/
      stage_text_encoder.yaml
      problem_event_text_weak.yaml
      option_flan_t5.yaml
      option_flan_t5_clap.yaml
      exp_041_t5_only.yaml
      exp_042_t5_clap.yaml
      decision_text_encoder_flan_clap.yaml
      dataset_v2_150.yaml
    edges.yaml
    graph_index.yaml

  notes/
    problems/
      problem_event_text_weak.md
    options/
      option_flan_t5_clap.md
    experiments/
      exp_042_t5_clap.md
    decisions/
      decision_text_encoder_flan_clap.md

  dashboards/
    current_state.json
    current_state.md
    graph_view.json
    timeline_view.json
    agent_context_pack.json

  ui/
    app/...
    public/...

  scripts/
    build_graph.py
    build_context_pack.py
    sync_experiments.py
    update_status.py
```

---

## 为什么这样组织好

### `graph/nodes/*.yaml`

这是**结构化真相源**。
agent 和 UI 都读这里。

### `notes/*.md`

这是人类可读的详细说明。
节点里只放摘要和索引，详细推理放 Markdown。

### `dashboards/*.json`

这是自动生成的聚合视图。
给 UI 和 agent 快速读取。

---

# 三、状态更新：如何持续维护而不崩溃

如果更新机制太麻烦，这个系统一定会废掉。
所以更新必须非常轻。

我建议设计成**四类更新动作**。

---

## 动作 1：新增问题 / 分支

当你发现一个新问题，比如：

> “TTS 不应该进入主网络”

就创建一个 problem 节点：

```yaml
type: problem
title: Should TTS be part of the main edit network?
status: active
parent: stage_task_formulation
```

然后它下面加几个 options：

* TTS as ordinary event in network
* TTS add handled by DSP
* TTS via asset conditioning

---

## 动作 2：记录实验结果

做完一个实验后，不是只看 log，而是更新 experiment 节点：

```yaml
status: done
result_summary: FLAN-T5 improves replace-following, but remove still weak.
outcome: positive
suggests:
  - continue with CLAP anchor
```

然后 option 节点自动聚合：

```yaml
decision_state: promising
evidence_strength: medium
```

---

## 动作 3：形成决策

当一个方案基本确定，创建 decision 节点，并且把问题标记为：

```yaml
status: resolved
resolved_by: decision_text_encoder_flan_clap
```

这样 UI 可以显示这个问题已经有结论，不再悬空。

---

## 动作 4：设置当前焦点

你现在最需要这个。

在 `current_state.yaml` 里维护：

```yaml
current_stage: stage_text_encoder
current_problem: problem_event_text_weak
current_focus_path:
  - stage_text_encoder
  - problem_event_text_weak
  - option_flan_t5_clap
next_actions:
  - regenerate timeline cache with FLAN-T5-XL
  - implement CLAP anchor in EventStateInitializer
  - run T5-only ablation
```

这个文件是：

* UI 首页高亮当前主线
* agent 快速理解上下文
* 你自己打开仓库就知道“我现在在干嘛”

---

# 四、给 AI 的上下文组织：怎样让 agent 快速理解项目

这是关键。
不要让 agent 每次读一堆零散文件。

你需要一个**自动生成的 context pack**。

---

## `agent_context_pack.json`

它应该是 skill 每次自动生成的精简上下文包，内容包括：

```json
{
  "project_name": "Audio Edit Research",
  "current_stage": "Text Encoder Redesign",
  "current_problem": "Event-level text control is weak",
  "active_option": "FLAN-T5-XL + CLAP",
  "current_hypothesis": "FLAN-T5 improves token understanding; CLAP improves audio-semantic alignment.",
  "open_risks": [
    "Need new timeline feature cache",
    "Need train/infer parity for new text features"
  ],
  "latest_decisions": [
    "TTS add should not be part of the main network",
    "Use unified edit-program as the main task formulation"
  ],
  "next_actions": [
    "Implement FLAN-T5 event sequence encoder",
    "Add CLAP anchor to EventStateInitializer"
  ],
  "linked_nodes": [...]
}
```

这样 agent 只要读这一个文件，就能掌握 80% 上下文。

---

## AI 读取上下文的推荐层次

agent 不应该每次全量读图，而是按层级加载：

### 第 1 层：当前状态

* current_state.yaml
* agent_context_pack.json

### 第 2 层：当前主线节点

* current stage
* current problem
* active option

### 第 3 层：相邻信息

* supporting experiments
* relevant decisions
* current dataset / backbone artifact

### 第 4 层：必要详细文档

* 某个 node 对应的 notes/*.md

这叫 **context-on-demand**。
比“把所有文档都塞给 agent”更高效。

---

# 五、可视化交互界面：你真正想要的界面应该长什么样

你说得很清楚：

> 希望像多叉树或流程图一样，一眼看到自己当前处于什么阶段，面前需要处理的问题有哪些，每个问题的解决方案有哪些，方案验证后的结论可以点开查看。

那我建议 UI 设计成 **三层视图 + 右侧详情面板**。

---

## View A：Roadmap / Stage View（全局阶段视图）

这是顶层导航。

显示：

```text
Stage 1: Dataset
Stage 2: Task Formulation
Stage 3: Model Design
Stage 4: Text Encoder
Stage 5: Backbone Comparison
Stage 6: Experiments
Stage 7: Paper
```

每个 stage 是一张卡片，带状态：

* planned
* active
* blocked
* done

点击某个 stage，进入它的问题树。

这个视图回答：

> 我现在整体研究走到哪一阶段了？

---

## View B：Problem Tree（问题-方案多叉树）

这是你最需要的主界面。

结构大概是：

```text
[Problem: Event-level text control is weak]
    ├── [Option A: Keep Gemma]
    │       └── [Exp]
    ├── [Option B: FLAN-T5]
    │       ├── [Exp]
    │       └── [Exp]
    ├── [Option C: CLAP only]
    │       └── [Exp]
    └── [Option D: FLAN-T5 + CLAP]   <-- active
            ├── [Exp running]
            ├── [Exp planned]
            └── [Decision pending]
```

这里你一眼就能看到：

* 当前问题是什么
* 有哪些分支
* 哪个是当前主分支
* 哪些已经被拒绝
* 哪些实验支持哪个方案

这就是你说的“多叉树”。

---

## View C：Decision Flow / History View（决策演化图）

这个视图更适合审视过去。

例如：

```text
Problem -> Option -> Experiment -> Decision -> Consequence
```

你可以点击一个决策，看到：

* 为什么做这个决策
* 参考了哪些实验
* 拒绝了哪些替代方案
* 这个决策影响了哪些下游工作

这个视图特别适合：

* 周总结
* 论文写作
* 防止遗忘为什么当初这么选

---

## 右侧详情面板（非常重要）

无论点击哪个节点，右侧都显示统一详情：

### 顶部

* title
* type
* status
* priority

### 中部

* summary
* current conclusion
* supporting evidence
* linked files / notes / figures / configs

### 底部

* next actions
* blockers
* related nodes

例如点开 `option_flan_t5_clap`，右侧显示：

* Hypothesis
* Expected benefit
* Needed implementation
* Current evidence
* Experiments run / planned
* Open risks
* Next steps

---

# 六、节点状态体系：必须统一，否则图会混乱

我建议每类节点有固定状态。

---

## Stage 状态

* planned
* active
* blocked
* done

## Problem 状态

* open
* active
* resolved
* parked

## Option 状态

* open
* promising
* rejected
* accepted
* paused

## Experiment 状态

* planned
* queued
* running
* done
* failed

## Decision 状态

* proposed
* accepted
* superseded
* rejected

这样 UI 上可以用统一颜色和图标。

---

# 七、建议的技术栈

如果你想要真正的可交互图，不要只用 Mermaid。
Mermaid 适合静态图，不够强交互。

我推荐：

---

## 前端：React Flow

React Flow 非常适合这种节点图/流程图/多叉树界面。

它能做：

* 拖拽图
* 点击节点显示详情
* 高亮当前路径
* 展开/折叠分支
* 缩放/过滤

这正适合你要的研究图谱 UI。

---

## 数据源：YAML / JSON graph

前端读取 `dashboards/graph_view.json`：

```json
{
  "nodes": [...],
  "edges": [...],
  "current_focus_path": [...]
}
```

---

## 后端：可以非常轻

甚至不用复杂后端。
可以先用 Python 脚本构建静态 JSON，然后前端直接读。

也就是说 MVP 可以是：

```text
YAML nodes + Python build_graph.py + React Flow frontend
```

足够了。

---

# 八、我建议你做的 4 个核心页面

---

## 1. Dashboard（首页）

显示：

* 当前主线
* 当前阶段
* 当前问题
* 当前活跃分支
* 最近 5 个决策
* 最近 5 个实验
* 当前 blockers
* next actions

这是总览页。

---

## 2. Research Graph（问题树 / 分支图）

主交互图。
支持：

* 按 stage 过滤
* 按 status 过滤
* 高亮 active path
* 点击节点查看详情
* 展开 / 折叠某个问题树

---

## 3. Experiment Matrix（实验表）

表格视图，方便对比：

* exp id
* stage
* problem
* option
* dataset
* backbone
* status
* main result

这个视图对 agent 和你自己都很实用。

---

## 4. Decisions / ADR View

列出所有重要决策：

* accepted
* proposed
* superseded

每条可点开，查看来龙去脉。

---

# 九、这个 skill 到底要帮你做什么

我建议 skill 不只是“生成文档”，而是做 8 件事：

---

## Skill 功能 1：Add Node

新增 stage / problem / option / experiment / decision / artifact。

---

## Skill 功能 2：Update Status

更新某个节点的状态和摘要。

例如：

* `exp_042` from running -> done
* result summary = ...

---

## Skill 功能 3：Set Current Focus

设置你当前正在推进的主线。

这会更新：

* current_state.yaml
* graph highlighter
* agent_context_pack.json

---

## Skill 功能 4：Record Finding

把一次实验结论沉淀成结构化 finding。

例如：

```yaml
finding:
  statement: FLAN-T5 improves replace-following over Gemma baseline.
  confidence: medium
  evidence:
    - exp_041
```

---

## Skill 功能 5：Promote Decision

当多个 finding 收敛后，生成 decision 节点。

---

## Skill 功能 6：Build Dashboard

自动生成：

* current_state.md
* graph_view.json
* experiment_matrix.md
* agent_context_pack.json

---

## Skill 功能 7：Suggest Next Actions

基于当前 active problem / active option / missing experiments，给出建议下一步。

---

## Skill 功能 8：Context Pack for Agent

当你让 agent 干活时，自动提取最相关的上下文包。

---

# 十、如何让人“更好地定位目前应该解决的问题”

这是一个非常重要的功能点。

我建议每个 problem 节点都有这几个字段：

```yaml
urgency: high
impact: critical
blocking: true
owner: self
```

然后 Dashboard 自动生成：

### Current Blocking Problems

* Event-level text control is weak
* Need new timeline feature cache
* Need reliable edit benchmark

### Nice-to-have Problems

* Better UI for graph filtering
* More caption rewrite templates

这样你打开系统时，不是只看到一堆树，而是先看到：

> **当前最影响主线推进的 3 个问题**

---

# 十一、MVP 版本我建议你先做什么

不要一上来做很复杂的系统。
先做一个 **最小可用研究驾驶舱**。

---

## MVP 必做

### 数据层

* YAML node schema
* current_state.yaml
* build_graph.py
* build_context_pack.py

### UI

* Dashboard
* Research Graph
* Right-side details panel

### 节点类型

* stage
* problem
* option
* experiment
* decision

### 核心功能

* 点击节点
* 高亮 current path
* 展开 / 折叠分支
* 状态颜色
* 右侧显示 summary / next actions / linked experiments

---

## 第二阶段再做

* 搜索
* 按状态过滤
* 时间线视图
* 自动从 MLflow / DVC 同步实验
* 自动从 Git branch 生成关联

---

# 十二、我给你的最终建议

你的系统最适合这样定义：

> **一个 repo-native、graph-based 的 Research Cockpit**
>
> 以 YAML 节点作为真相源，
> 用 Markdown 存详细说明，
> 用 Python 构建图和上下文包，
> 用 React Flow 做交互式多叉树 / 决策流程图，
> 让 human 和 agent 都能围绕同一套研究状态工作。

---

