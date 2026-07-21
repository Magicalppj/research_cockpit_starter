# Spec: Command And Interface Consolidation

## Status

Superseded historical plan. The 0.3.0 one-version role-facade design in `docs/plans/2026-07-19-multi-agent-research-management-framework.md` replaces its alias and compatibility strategy; command examples below are not current workflow guidance.

## Date

2026-06-18

## Implementation Progress

- [x] Phase 1 partial: `commands --json --compact` 输出 `group`、`canonical_name`、`status`、`aliases`、`replacement`、`input_modes`。
- [x] Phase 1 partial: `commands` 支持 `--group <group>`、`--status <status>` 和 `--deprecated` 过滤。
- [x] Phase 1 partial: 将 command group/status/lifecycle/alias 发现契约集中到 `command_registry.py`，并针对 manifest contract metadata 和过滤行为增加单元测试。
- [x] Phase 4 partial: 增加首批非破坏性 grouped aliases：`research-cockpit artifact create|ingest|link` 和 `research-cockpit run create|update|complete|list|context`。
- [x] Phase 5 partial: README、`capabilities/focus-context.md`、`capabilities/experiment-tracking.md` 同步 `--group` 与 command discovery 字段说明。
- [ ] Phase 2: JSON envelope policy 仍未落地。
- [ ] Phase 3: 复杂输入 `--file` / `--print-schema` parity 仍需继续盘点和补齐。
- [ ] Phase 4 remaining: `context`、`graph`、`maintenance` 等 grouped aliases 仍需进一步设计，尤其是与现有 top-level `context` 的兼容关系。
- [ ] Phase 6: compatibility/deprecation tagging 仍未开始；当前命令默认 `active`。

## Source Context

Research Cockpit 已经从单一研究状态工具扩展为包含多 agent 协作、assignment cursor、graph/context、run/gate、artifact/evidence、maintenance/audit、build/profile/search、UI dashboard 等多条工作流的插件。当前 CLI 命令数量已经较多，`research-cockpit commands --json --compact` 也已经成为下游 agent 的重要发现接口。

近期文档与 manifest 同步工作暴露了一个典型问题：实现层已经支持 `create-artifact --file`、retention 相关字段和 `--print-schema`，但 agent command manifest 曾经落后于实现。这个问题不是单点 bug，而是“命令契约、文档、实现、测试”分散维护后自然产生的漂移风险。

现有架构文档已经给出清晰边界：

- `src/research_cockpit/commands/*` 和 `research-cockpit` CLI 是公共工作流入口。
- domain modules 负责业务行为，command modules 应保持较薄。
- `src/research_cockpit/model.py` 是兼容 facade，新代码应优先依赖更聚焦的模块。
- Markdown notes 不是结构化状态真相源；YAML 和 JSON state 仍是核心 truth source。
- mutating command 的 dry-run 路径必须保持无写入。

本计划从总体设计角度优化“功能和接口过多”的问题，目标不是立刻减少能力，而是降低发现、使用、维护和演进成本。

## Assumptions

- 第一阶段保持现有 top-level CLI command 向后兼容，不删除下游 agent 可能依赖的命令。
- `research-cockpit commands --json --compact` 是 agent-facing API，需要按公共接口标准维护。
- YAML/JSON data root 布局不在本计划中改变。
- Windows 和 Unix shell 都需要继续可用，复杂输入优先走 `--file` 以降低 quoting 风险。
- 不引入新的运行时依赖，除非后续实现阶段单独论证。
- 不设计一个通用 `update-anything --json-patch` 类型接口；Research Cockpit 的 mutating command 应继续表达明确工作流意图。

## Objective

建立一套可渐进落地的命令与接口整合方案，使 Research Cockpit 在功能继续增长时仍然满足以下目标：

- 下游 agent 能按工作流快速找到正确命令，而不是在长命令列表中猜测。
- 命令实现、manifest、文档、schema、测试之间减少漂移。
- 复杂输入有一致的 `--file` / `--print-schema` 契约。
- JSON 输出有稳定 envelope，便于多 agent 和 UI 可靠消费。
- 旧命令有明确 compatibility/deprecation 状态，未来可以安全迁移。
- 代码边界保持清楚：CLI 负责交互契约，domain modules 负责行为，storage modules 负责持久化。

## Non-Goals

- 不在第一阶段删除或重命名现有命令。
- 不把 YAML state 迁移到数据库。
- 不重写 UI、graph 计算或核心 domain logic。
- 不降低 dry-run、audit log、assignment scope 的安全边界。
- 不用单一泛化 mutation API 替代语义明确的工作流命令。
- 不一次性生成所有文档并替换人工说明；先建立可检查契约，再决定是否生成。

## Design Principles

1. **Backward-compatible first**: 先增加 canonical metadata、grouped alias 和检查，再逐步标记 legacy entrypoint。
2. **Single source of truth for command contracts**: 命令名、分组、mutating 属性、参数、schema、输出契约、deprecation 状态应从一个契约源派生或被测试强校验。
3. **Workflow-oriented API**: 命令按“读上下文、计划、写入、审计、构建、维护”组织，而不是按内部文件结构暴露。
4. **File-first for nested inputs**: 对复杂 JSON/YAML 输入保持 `--file` 优先，并提供 `--print-schema`。
5. **Stable JSON envelope**: 下游 agent 不应依赖自然语言提示判断执行结果。
6. **Thin CLI, focused domain modules**: command module 只做解析、验证、调用和输出，不承载复杂业务逻辑。
7. **Explicit lifecycle states**: active、compatibility、deprecated、removed 的语义必须明确。

## Target Interface Shape

### 1. Command Contract Source Of Truth

建立或强化一层命令契约定义。可选实现路径：

- 轻量路径：继续以 `src/research_cockpit/commands/list_agent_commands.py` 为 manifest 源，但扩展字段并增加 parity tests。
- 更完整路径：新增 `src/research_cockpit/command_specs.py` 或 `src/research_cockpit/commands/_specs.py`，由 CLI help、agent manifest、文档检查共同读取。

建议契约字段：

- `name`: 现有可执行命令名。
- `canonical_name`: 推荐命令名。
- `group`: 工作流分组，例如 `context`、`graph`、`run`、`artifact`、`maintenance`、`build`。
- `lifecycle`: `read`、`plan`、`mutate`、`audit`、`build`、`profile`。
- `mutating`: 是否会写入数据 root 或生成文件。
- `requires_assignment`: 是否建议或要求 `--assignment`。
- `input_modes`: `flags`、`file`、`schema`。
- `required_flags` / `optional_flags`: agent 可发现参数。
- `output_contract`: JSON envelope 类型。
- `aliases`: 兼容或 grouped alias。
- `status`: `active`、`compatibility`、`deprecated`。
- `replacement`: deprecated/compatibility command 的迁移目标。
- `docs`: 对应 capability/doc anchors。

### 2. Grouped Command Aliases

保留现有 top-level commands，同时增加面向发现的分组别名。第一阶段别名只做路由，不改变行为。

候选分组：

- `research-cockpit context ...`
  - `coordinator`
  - `assignment`
  - `node`
  - `option`
  - `session`
- `research-cockpit graph ...`
  - `add`
  - `update`
  - `plan`
  - `view`
- `research-cockpit run ...`
  - `create`
  - `update`
  - `complete`
  - `context`
  - `list`
- `research-cockpit artifact ...`
  - `create`
  - `link`
  - `ingest`
  - `audit`
- `research-cockpit maintenance ...`
  - `audit`
  - `closeout`
  - `active-resources`
  - `worktree-audit`
  - `branch-audit`
- `research-cockpit build ...`
  - `dashboard`
  - `profile`
  - `smoke`

`research-cockpit commands --json --compact` 应暴露这些关系：

```json
{
  "name": "create-artifact",
  "canonical_name": "artifact create",
  "group": "artifact",
  "aliases": ["create-artifact"],
  "status": "active",
  "replacement": null
}
```

### 3. JSON Envelope Standardization

不同命令可以有不同 payload，但 envelope 应稳定。

Read-only command:

```json
{
  "ok": true,
  "schema_version": "1.0",
  "root": "research_cockpit",
  "warnings": [],
  "data": {}
}
```

Dry-run mutation:

```json
{
  "ok": true,
  "schema_version": "1.0",
  "dry_run": true,
  "would_change": true,
  "changed_files": [],
  "preflight_ok": true,
  "before": {},
  "after": {},
  "warnings": []
}
```

Executed mutation:

```json
{
  "ok": true,
  "schema_version": "1.0",
  "changed": true,
  "changed_files": [],
  "warnings": [],
  "recommended_commands": []
}
```

Audit/planner command:

```json
{
  "ok": true,
  "schema_version": "1.0",
  "blockers": [],
  "findings": [],
  "recommended_next_actions": [],
  "execution_commands": []
}
```

Build/profile command:

```json
{
  "ok": true,
  "schema_version": "1.0",
  "written_files": [],
  "profile": {},
  "warnings": []
}
```

### 4. Complex Input Schema Policy

对包含嵌套结构、批量字段或容易出现 shell quoting 问题的命令统一要求：

- 支持 `--file <path>`。
- 支持 `--print-schema`。
- schema 示例包含 required fields、optional fields、枚举值和 minimal example。
- `--file` 输入优先使用 JSON/YAML parser，避免 ad hoc string parsing。
- `commands --json --compact` 中标记 `input_modes: ["flags", "file", "schema"]`。

优先覆盖命令类型：

- artifact/evidence 创建和链接。
- gate result / run summary / option workstream 输入。
- graph plan / node plan / batch update。
- maintenance audit closeout plan。

### 5. Context Surface Consolidation

当前上下文相关命令覆盖 coordinator、assignment、node、option、agent session 等入口。保留已有命令的同时，建立统一 mental model：

- coordinator/global triage: `research-cockpit context coordinator --root ...`
- assignment cursor: `research-cockpit context assignment --root ... --assignment ...`
- known node handoff: `research-cockpit context node --root ... --id ... --with-bootstrap --with-artifacts --compact`
- option workstream: `research-cockpit context option --root ... --option ...`
- compatibility aliases: `bootstrap`、`node-context`、`agent-session-context` 等继续工作。

此处的重点是降低 agent 读顺序复杂度，而不是改变数据模型。

### 6. Documentation And Release Checks

文档优化不应依赖人工记忆同步。建议分两步：

1. Release check 增加 command contract/doc parity 检查。
2. 评估是否从契约生成 README command table 或 capability snippets。

最小可行检查：

- manifest 中声明 `--file` 的命令，help 或 schema test 必须能证明支持。
- manifest 中声明 `--print-schema` 的命令，测试必须能调用成功。
- README/capability docs 中列出的命令必须存在于 command contract。
- deprecated command 必须有 `replacement`。

### 7. Deprecation Lifecycle

命令状态建议：

- `active`: 推荐使用的命令。
- `compatibility`: 旧入口仍支持，但 manifest 指向 canonical replacement。
- `deprecated`: 不推荐，输出 warning，至少保留一个版本窗口。
- `removed`: 已移除，只应出现在历史迁移文档中。

第一阶段不移除命令。可先标记明显 compatibility 的入口，例如 legacy focus 或旧 context entrypoint，并在文档中说明推荐命令。

## Project Structure

当前相关位置：

- `src/research_cockpit/cli.py`: argparse parser 和顶层命令注册。
- `src/research_cockpit/command_registry.py`: command registry 入口。
- `src/research_cockpit/commands/list_agent_commands.py`: agent-facing command manifest。
- `src/research_cockpit/commands/*`: 具体命令 wrapper。
- `src/research_cockpit/model.py`: 兼容 facade。
- `src/research_cockpit/storage.py`: YAML/JSON 读写边界。
- `tests/test_scripts.py`: CLI/script behavior 测试集中位置。
- `capabilities/*`: agent capability 文档。
- `README.md`: 用户和 agent 的第一层说明。
- `docs/internal-architecture.md`: 内部模块边界。

可能新增位置：

- `src/research_cockpit/command_specs.py` 或 `src/research_cockpit/commands/_specs.py`: command contract definitions。
- `tests/test_command_contracts.py`: manifest/help/schema parity tests。
- `docs/command-interface.md`: CLI contract 与 envelope 说明。

## Commands

当前基线验证命令：

```sh
python -B -m unittest discover -s tests
python dev/scripts/run_skill_release_check.py --json --skip-mutating
git diff --check
research-cockpit commands --json --compact
```

实现后建议新增或扩展的验证命令：

```sh
research-cockpit commands --json --compact --group artifact
research-cockpit commands --json --compact --deprecated
research-cockpit command-docs --check
```

这些新增命令是计划项，不代表当前仓库已经支持。

## Implementation Plan

### Phase 0: Inventory And Classification

目标：不改行为，先把现有命令按契约维度盘点清楚。

任务：

- 导出现有 `commands --json --compact`。
- 按 `group`、`lifecycle`、`mutating`、`input_modes` 分类。
- 找出重复入口、legacy alias、命名不一致、缺少 `--file` 或 `--print-schema` 的命令。
- 输出一份内部 inventory 表，作为后续实现输入。

验证：

- inventory 中的命令数量与 `commands --json --compact` 一致。
- 每个命令至少有 `group`、`lifecycle`、`mutating` 三项分类。
- 不产生运行时文件写入。

### Phase 1: Command Contract Hardening

目标：让 agent manifest 成为可靠契约，而不是手写提示集合。

任务：

- 扩展 command manifest 字段，至少加入 `group`、`canonical_name`、`status`、`aliases`、`replacement`、`input_modes`。
- 对 manifest 与 argparse/help 建立 parity tests。
- 对 manifest 中声明的 `--file` 和 `--print-schema` 建立专项测试。
- 保持 compact 输出体积可控，详细字段可只在非 compact 模式输出。

验证：

```sh
python -B -m unittest tests.test_scripts.ScriptBehaviorTests
python dev/scripts/run_skill_release_check.py --json --skip-mutating
git diff --check
```

### Phase 2: JSON Envelope Policy

目标：明确并测试代表性命令的 JSON 输出形态。

任务：

- 为 read、dry-run mutation、executed mutation、audit/planner、build/profile 各选 1-2 个代表命令。
- 补充契约测试，断言 `ok`、`schema_version`、`warnings` 等基础字段。
- 对历史 payload 差异做兼容层，不要求一次性重写所有输出。
- 在 `docs/command-interface.md` 记录 envelope policy。

验证：

- 代表性命令的 JSON contract test 通过。
- 不破坏已有 JSON 字段。
- dry-run 测试证明不会写 YAML、JSON state 或 generated dashboards。

### Phase 3: Complex Input File/Schema Parity

目标：让复杂输入命令都具备可发现、可测试、跨平台稳定的输入方式。

任务：

- 补齐缺失的 `--file` 和 `--print-schema`。
- 统一 schema example 风格。
- 对 parser 增加 malformed file、missing required field、valid minimal file 的测试。
- 在 command manifest 中标记 input mode。

验证：

```sh
python -B -m unittest tests.test_scripts.ScriptBehaviorTests
research-cockpit <command> --print-schema
research-cockpit <command> --file <fixture> --dry-run --json
```

### Phase 4: Grouped Aliases

目标：在不破坏旧命令的前提下提供更容易发现的 grouped command surface。

任务：

- 在 argparse 层增加 grouped subcommands。
- grouped alias 调用现有 command handler，避免复制业务逻辑。
- manifest 输出 canonical/alias 关系。
- 文档优先展示 canonical grouped commands，兼容命令放到迁移说明。

验证：

- 旧 top-level 命令和新 grouped alias 对同一 fixture 输出等价核心字段。
- `commands --json --compact --group <group>` 能列出分组命令。
- release check 通过。

### Phase 5: Documentation Sync

目标：减少 README、capability docs、command manifest 的人工同步成本。

任务：

- 更新 README，只展示 canonical workflow 和少量常用命令。
- 在 capability docs 中保留任务导向说明，避免复制完整命令清单。
- 增加 command-doc parity check，至少检查文档列出的命令真实存在。
- 决定是否从 command contract 生成某些命令表。

验证：

```sh
python dev/scripts/run_skill_release_check.py --json --skip-mutating
research-cockpit command-docs --check
```

### Phase 6: Deprecation Tagging

目标：为未来安全收敛接口铺路，但不立即删除能力。

任务：

- 给 compatibility/legacy commands 标记 `status` 和 `replacement`。
- deprecated command 在 JSON 输出中增加 warning。
- 文档说明迁移窗口和推荐 replacement。
- 测试 deprecated command 仍可执行。

验证：

- `research-cockpit commands --json --compact --deprecated` 可列出迁移信息。
- legacy command 行为未破坏。
- deprecated command warning 不影响机器解析。

## Testing Strategy

- **Unit tests**: 验证 command spec 数据结构、manifest 字段、schema parsing。
- **CLI behavior tests**: 通过 subprocess 调用代表命令，验证 JSON envelope、exit code、dry-run no-write。
- **Compatibility tests**: 对旧命令和 grouped alias 做核心输出等价测试。
- **Release checks**: 将 command/doc/schema parity 放入 `run_skill_release_check.py --skip-mutating`。
- **Fixture strategy**: 使用 `.test_tmp` 或已有测试 fixture；避免写真实 project state。
- **Snapshot policy**: 不建议对完整 JSON 做大 snapshot；只断言稳定 contract 字段，降低无意义 churn。

## Boundaries

Allowed without further design approval:

- 增加 manifest metadata。
- 增加只读检查和测试。
- 给复杂输入命令补充 `--file` / `--print-schema`。
- 增加非破坏性 grouped alias。
- 更新 README/capability docs 以指向 canonical workflows。

Requires explicit review:

- 删除、重命名或改变现有命令语义。
- 修改现有 JSON 字段含义。
- 引入新运行时依赖。
- 改变 YAML truth source 或 data root layout。
- 让文档生成完全替代手写说明。

Never:

- 在 dry-run 路径写入 YAML、JSON state、dashboards 或 audit log。
- 让通用 mutation command 绕过 assignment scope 或 coordinator 权限边界。
- 将 project-specific state 写入 plugin repository。
- 隐式执行 Action Guidance 中的 suggested command。

## Risks And Mitigations

- **Downstream agent 依赖旧命令**: 第一阶段只新增 alias 和 metadata，不删除旧入口。
- **接口整合变成大重构**: 按 phase 交付，每阶段有独立验证。
- **manifest 再次漂移**: 用 parity tests 和 release check 固化。
- **JSON envelope 标准化破坏兼容**: 先对代表命令加外层字段，不删除既有 payload。
- **grouped alias 增加维护负担**: grouped alias 调同一 handler，不复制业务逻辑。
- **Windows quoting 问题**: 对复杂输入推广 `--file` 并添加 Windows 友好的测试 fixture。
- **文档自动生成过度**: 先做检查，再决定生成范围。

## Parallelization Plan

可并行推进的工作：

- Agent A: 命令 inventory、manifest metadata 扩展、parity tests。
- Agent B: JSON envelope audit、代表命令 contract tests、dry-run no-write 检查。
- Agent C: README/capability docs 梳理、command-doc parity check 设计。

建议串行推进的工作：

- argparse grouped subcommand routing。
- deprecation warning 的公共输出路径。
- release check 集成。

## Success Criteria

- `commands --json --compact` 能通过 `group`、`canonical_name`、`status`、`aliases` 表达推荐接口和兼容接口。
- 下游 agent 能按 `context`、`graph`、`run`、`artifact`、`maintenance` 等工作流发现命令。
- manifest 中声明的参数、`--file`、`--print-schema` 与实际 CLI 行为有测试保证。
- 代表性命令符合稳定 JSON envelope，且不破坏原有字段。
- 复杂输入命令具备 file/schema 路径，减少 shell quoting 失败。
- README 和 capability docs 以 canonical workflow 为主，不再复制大量易漂移命令细节。
- 所有实现阶段结束时以下命令通过：

```sh
python -B -m unittest discover -s tests
python dev/scripts/run_skill_release_check.py --json --skip-mutating
git diff --check
```

## Open Questions

- grouped command 使用 `research-cockpit artifact create` 还是继续坚持 hyphenated command，并只在 manifest 中分组？
- compatibility command 的 deprecation window 应以版本号、日期还是 release count 表达？
- `docs/command-interface.md` 是完整人工文档，还是由 command contract 生成主体内容？
- `bootstrap`、`context`、`node-context` 等 context entrypoint 是否在近期就统一到 grouped surface，还是先只增加 manifest 分组？
- compact manifest 的 schema 是否需要独立 `schema_version`？

## Proposed Review Gate

进入代码实现前，建议先确认三项设计选择：

1. 是否接受 grouped aliases 作为推荐的新发现界面。
2. 是否新增独立 command spec module，还是先强化现有 `list_agent_commands.py`。
3. 是否把 command/doc parity check 纳入 release check。

确认后可按 Phase 0 到 Phase 6 逐项实现，每个 phase 独立提交、独立 review。
