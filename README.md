# Research Cockpit

Research Cockpit 是一个 repo-native 的研究记录与 agent 协作插件。它把长期研究拆成可追踪的主图节点：`stage`、`problem`、`option`、`experiment` 和 `decision`，并用本地 YAML / Markdown / JSON 保存研究状态、证据、决策和 agent 可读上下文。

`artifact` 仍是合法节点类型，但默认作为 evidence / resource / supporting material 使用。普通文件、配置、JSON、数据集和实验结果优先通过 `linked_artifacts`、`links`、notes 或 Resources 页面挂到研究节点上；只有 artifact 本身是长期研究对象或关键产出时，才建议提升为主图可见节点。

这个仓库本身就是可安装的 agent skill / plugin。核心代码和工具在插件仓库内，真实研究项目只需要在仓库根目录保存自己的 `research_cockpit/` 状态资产。

## 适合什么场景

- 长期研究项目：问题、候选方案、实验、证据和决策需要持续积累。
- 人类研究者 + coding agent 协作：agent 先读取 context pack，再通过受控脚本写入研究状态。
- 本地优先记录：所有状态文件都可以进入 git diff、code review 和归档流程。
- 轻量数据层：当前接受 YAML / JSON 作为数据库，不依赖外部服务。

## 核心节点与状态

Research Cockpit 的主图默认围绕一条研究链组织：

```text
stage -> problem -> option -> experiment -> decision
```

- `stage`：研究阶段或里程碑。
- `problem`：当前要解释、修复或回答的问题。
- `option`：候选方案、假设、路线或分支。
- `experiment`：用于验证 option 的实验、评测或观察。
- `decision`：基于证据形成的 ADR-style 决策。
- `artifact`：文件、配置、数据集、结果产物等支持材料；默认不作为主研究链节点展示。

常见状态含义：

| 节点类型 | 状态 | 含义 |
| --- | --- | --- |
| `stage` | `planned` / `active` / `blocked` / `done` | 已规划 / 正在推进 / 被阻塞 / 已完成 |
| `problem` | `open` / `active` / `blocked` / `resolved` / `parked` | 已记录 / 正在处理 / 被阻塞 / 已解决 / 暂时搁置 |
| `option` | `open` / `active` / `promising` / `rejected` / `accepted` / `paused` / `parked` | 候选 / 正在探索 / 有希望、值得优先验证或推进决策 / 已否定 / 已采纳 / 暂停 / 搁置 |
| `experiment` | `planned` / `queued` / `running` / `done` / `failed` / `cancelled` | 已规划 / 排队 / 运行中 / 已完成 / 失败 / 取消 |
| `decision` | `proposed` / `accepted` / `superseded` / `rejected` | 决策草案 / 已接受 / 被后续决策取代 / 未采纳 |
| `artifact` | `draft` / `planned` / `active` / `done` / `superseded` / `deprecated` / `archived` | 草稿 / 计划产出 / 正在使用 / 已完成 / 被替代 / 不推荐继续使用 / 已归档 |

`promising` 特指 `option` 已经出现正向信号，但还没有被正式采纳。通常下一步是补实验结果、比较 alternatives，或者用 `research-cockpit promote-decision` 生成 `decision` 草案。

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

Maintainer docs:

- `docs/repo-layout.md`: repository layout and source module map.
- `docs/internal-architecture.md`: internal module boundaries and dependency rules.
- `docs/decisions/0001-layered-plugin-architecture.md`: rationale for the layered plugin architecture.

研究仓库：

```text
my-research-repo/
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

安装后推荐使用 `research-cockpit <command>`。如果当前 shell 找不到 console script，但同一个 Python 环境可以 `import research_cockpit`，可以使用等价的 module 入口：

```sh
python -m research_cockpit.cli bootstrap --root /absolute/path/to/research_cockpit --json
```

在受限 agent shell 中，优先传入绝对 `--root`，避免当前工作目录不可控导致读写到错误位置。

如果需要隔离环境，可以先创建并激活任意 Python virtual environment 或 conda environment，再执行同一条 `python -m pip install -e .`。

命令示例使用跨平台的 `sh` 代码块和 `/` 路径分隔符；这些路径同样可被 Python 和 Git for Windows / PowerShell / cmd 识别。不要把本机绝对路径、用户名、私有数据目录或虚拟环境路径写入研究状态和提交历史。

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
research-cockpit init --root research_cockpit
research-cockpit init --root research_cockpit --build --json
research-cockpit bootstrap --root research_cockpit --build --json
research-cockpit validate --root research_cockpit --json
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

- Research Graph：React Flow 图谱、节点点击、右侧 inspector、筛选、保存/加载视图、PyVis fallback；默认聚焦研究主链，artifact 可在 Graph Controls 中手动显示。
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
使用 research-cockpit skill。先运行 `research-cockpit bootstrap --root research_cockpit --json`。
如果已知接手节点，再运行 `research-cockpit node-context --root research_cockpit --id <node_id> --compact --json`。
只有排查全局状态或需要 generated dashboard context 时，再读取 agent_context_pack / focus_context_pack。
```

推荐启动命令：

```sh
research-cockpit bootstrap --root research_cockpit --json
research-cockpit node-context --root research_cockpit --id <node_id> --compact --json
research-cockpit validate --root research_cockpit
```

```sh
python -m research_cockpit.cli node-context --root research_cockpit --id <node_id> --compact --json --command-style python
```

`node-context` 是只读命令，会直接从 truth-source YAML 实时整理单个节点的 parent chain、blockers、next actions、证据状态、资源、recent interactions 和安全命令草案。新 agent 如果已经拿到目标 node id，通常先跑 `--compact --json` 的短输出，再按返回的 `recommended_next_steps` 选择下一步写入命令；需要完整 relations、resources 或 recent interactions 时再去掉 `--compact`。如果 agent shell 不能直接调用 console script，可加 `--command-style python`，让命令草案使用 `python -m research_cockpit.cli ...`。

所有关键 YAML 写入都应走 `research-cockpit` CLI，不要让 agent 直接手写 YAML，除非对应 capability 明确允许并说明字段边界。Markdown note 可直接编辑，用来补人类可读细节；结构化 finding、status、focus、decision state、`current_best_option` 和 `next_actions` 仍以 CLI/YAML truth 为准。

常见 agent 流程：

```sh
research-cockpit bootstrap --root research_cockpit --json
research-cockpit node-context --root research_cockpit --id <node_id> --compact --json
research-cockpit suggest-next-actions --root research_cockpit --json
research-cockpit commands --json
research-cockpit claim-option --root research_cockpit --option <option_id> --agent <agent_id> --dry-run --json
research-cockpit validate --root research_cockpit --json
```

`suggest-next-actions` 默认跑一次即可；只有修改了 `next_actions` 或 suggestion lifecycle 后才需要再跑。

记录实验结论时优先使用保守原子命令：

```sh
research-cockpit complete-experiment --root research_cockpit --id <experiment_id> --finding "..." --confidence medium --result-summary "..." --next-action "..." --no-build
```

需要策略切换时显式调用：

```sh
research-cockpit update-node-fields --root research_cockpit --id <problem_id> --current-best-option <option_id> --no-build
```

批量写入时，对支持的命令使用 `--no-build`，但同一个 data root 上的 mutating commands 必须串行执行，不要并行跑多个写入命令；它们共享 `graph/interaction_log.yaml` 并由 mutation lock 保护。最后统一：

```sh
research-cockpit validate --root research_cockpit --json
research-cockpit build --root research_cockpit
```

如果需要刷新 generated dashboard/context，再显式运行 `research-cockpit build --root research_cockpit`。只读接手场景不要默认加 `--build`。

## 隐私边界

Research Cockpit 不会把数据发送到外部服务，但 `research_cockpit/` 是研究状态本身。公开仓库中只应提交可公开的节点、notes、context packs 和 linked resource 摘要；敏感实验记录、私有路径、凭据和未公开数据集位置应留在私有仓库或本地忽略文件中。

## 开发验证

```sh
python -m unittest discover -s tests
research-cockpit smoke --root examples/demo_research_cockpit --json
python dev/scripts/run_skill_release_check.py --json --skip-mutating
python dev/scripts/run_agent_usability_check.py --json
python dev/scripts/run_subagent_forward_check.py --json
git diff --check
```

## Agent Graph Update Workflow

For batch graph changes, preview first, then write sequentially with `--no-build`, then validate and rebuild once. `can_batch` means serial batching, not parallel execution:

```sh
research-cockpit apply-graph-plan --print-schema
research-cockpit apply-graph-plan --root research_cockpit --file graph_update.yaml --dry-run --json --show-diff
research-cockpit apply-graph-plan --root research_cockpit --file graph_update.yaml --no-build
research-cockpit validate --root research_cockpit --json
research-cockpit build --root research_cockpit
```

Use `create-workstream` for the common `problem -> active option -> experiments + follow-up options` shape:

```sh
research-cockpit create-workstream --print-schema
research-cockpit create-workstream --root research_cockpit --file workstream.yaml --dry-run --json --show-diff
research-cockpit create-workstream --root research_cockpit --file workstream.yaml --no-build
```

`create-workstream` sets the new problem `current_best_option` and active option `supporting_experiments`, but it does not change focus, pause old options, or delete old branches.
Use `open` for follow-up option status. File-based graph commands accept option status `planned` only as an input alias and write `open` to YAML.
`create-workstream --print-schema` includes common passthrough fields such as `question`, `hypothesis`, `summary`, `tags`, `success_criteria`, `metrics`, and `next_actions`.
After creating a workstream, use `option-workstream-context --root research_cockpit --id <option_id> --compact --json` to verify experiment ids, statuses, success criteria count, metric count, finding count, and linked artifact count. Use per-experiment `node-context` only when you need full field text.

When a focus node already has canonical actions, use:

```sh
research-cockpit sync-focus-actions --root research_cockpit --from-node problem_x --dry-run --json --show-diff
research-cockpit sync-focus-actions --root research_cockpit --from-node problem_x --no-build
```

## Agent Evidence Close-Out Workflow

For a known option or experiment, prefer one compact context read:

```sh
research-cockpit context --root research_cockpit --node <node_id> --with-bootstrap --with-artifacts --compact --json
research-cockpit option-workstream-context --root research_cockpit --id option_x --compact --json
```

Use `context` instead of chaining `bootstrap`, generated context packs, and `node-context` unless you are auditing global dashboard state. Use compact `option-workstream-context` when you specifically need the recursive option subtree, short experiment summaries, and evidence counts.

Record evidence artifacts through CLI commands instead of patching artifact YAML:

```sh
research-cockpit create-artifact --print-schema
research-cockpit create-artifact --root research_cockpit --file artifact.yaml --dry-run --json --show-diff
research-cockpit create-artifact --root research_cockpit --file artifact.yaml --no-build
research-cockpit create-artifact --root research_cockpit --id artifact_x --title "Result bundle" --status done --path outputs/run_x --link metrics=outputs/run_x/metrics.json --link-to experiment_x --dry-run --json --show-diff
research-cockpit create-artifact --root research_cockpit --id artifact_x --title "Result bundle" --status done --path outputs/run_x --link metrics=outputs/run_x/metrics.json --link-to experiment_x --no-build
research-cockpit link-artifact --root research_cockpit --artifact artifact_x --to option_x --no-build
```

Use `--file` for artifacts with several `links` or `link_to` targets. Artifact `path` and `links` are stored exactly as provided; JSON resource rows include `resolved_target`, `resolution_base`, `resolution_attempts`, and `exists`. Relative paths are checked against the root parent, then the data root, then cwd. Use `commands --json --compact --workflow evidence` or `commands --json --compact --name <command>` when choosing commands, and run a specific command's `--print-schema` when you need the full file example. Add `--compact` to `--json` on high-level mutation commands when an agent only needs target, changed status, created/updated ids, changed file count, and verify commands.

For experiment sweeps, batch findings from a file:

```sh
research-cockpit complete-experiments --print-schema
research-cockpit complete-experiments --root research_cockpit --file findings.yaml --dry-run --json --show-diff
research-cockpit complete-experiments --root research_cockpit --file findings.yaml --no-build
research-cockpit complete-experiment --root research_cockpit --id experiment_x --finding "..." --confidence medium --json --compact
```

Use `update-finding` for later evidence or wording revisions:

```sh
research-cockpit update-finding --root research_cockpit --experiment experiment_x --finding-id experiment_x_finding_001 --statement "..." --artifact-id artifact_x --dry-run --json --show-diff
```

Finalize a workstream only when the status changes are explicit:

```sh
research-cockpit finalize-workstream --print-schema
research-cockpit finalize-workstream --root research_cockpit --file finalize.yaml --dry-run --json --show-diff
research-cockpit finalize-workstream --root research_cockpit --option option_x --status accepted --problem-status resolved --summary-file summary.md --summary-target report --artifact artifact_x --sync-focus --report --dry-run --json --show-diff
```

Use `--file` for longer close-outs; explicit CLI flags override file values. `finalize-workstream` does not create artifacts, accept decisions, pause old branches, delete nodes, or invent next actions.

A relative `summary_file` inside `finalize.yaml` resolves against the finalize file directory, then the data root, then the current working directory. JSON output includes `resolved_inputs.summary_file`. Compact JSON stays short unless `--show-diff` is requested; with `--show-diff`, the payload includes the full diff plus `diff_line_count`. Finish batched writes with:

```sh
research-cockpit validate --root research_cockpit --json
research-cockpit build --root research_cockpit
```

For older single-purpose mutation commands, preview with JSON and diff rather than guessing the write shape:

```sh
research-cockpit update-suggestion-state --root research_cockpit --id sg_x --state dismissed --reason "..." --dry-run --json --show-diff
research-cockpit update-decision-evidence --root research_cockpit --id decision_x --dry-run --json --show-diff
research-cockpit update-decision-checklist --root research_cockpit --id decision_x --alternative option_x --consequence "..." --next-required-action "..." --dry-run --json --show-diff
```

These commands do not use `--compact`; their JSON output stays short by default and only includes a full diff when `--show-diff` is present. `research-cockpit build --root research_cockpit --json` reports generated files without writing an audit event.

If `validate` reports schema-damaged interaction log events, preview and repair the log explicitly:

```sh
research-cockpit repair-interaction-log --root research_cockpit --dry-run --json --show-diff
research-cockpit repair-interaction-log --root research_cockpit --json --show-diff --backup
research-cockpit validate --root research_cockpit --json
```

`repair-interaction-log` only drops invalid non-mapping event items or replaces invalid `events` containers with an empty list. It refuses YAML scanner errors instead of guessing a repair, and execution writes a backup before changing `graph/interaction_log.yaml`.

Dev forward checks now report a `metrics` block per track/case: `command_count`, `failed_command_count`, `context_read_count`, `mutating_count`, `dry_run_count`, `build_count`, `validate_count`, `manual_yaml_patch_detected`, and `high_level_commands_used`. Use these as trend signals, not hard pass/fail gates.
