# Testing Strategy

## Objective

开发反馈必须与风险层级匹配。完整覆盖继续保留，但不再作为每次小改动后的默认命令。统一入口是：

```sh
python dev/scripts/run_test_profile.py <fast|precommit|full> --json --compact --progress
```

不要串行运行 fast、precommit 和 full。选择当前阶段对应的最高一层；高层已经包含所需的低层覆盖。

## Profiles

| Profile | Intended use | Coverage | Target |
| --- | --- | --- | ---: |
| `fast` | 每个实现切片后的反馈 | profile、receipt/runtime id、packet 与 dependency sentinel；受影响范围由 `--extra-test` 补入 | 15s |
| `precommit` | 提交前 | fast + bounded role facade/transaction sentinel + 精确 CLI sentinel + read-only release check | 60s |
| `full` | merge、release 或大范围重构前 | 全部 unittest discovery + 完整 release check | 360s |

Target 只写入结果的 `within_target`，不决定退出码。支持的平台、较慢磁盘或受控沙箱不会仅因 wall-clock 超标而失败。

## Changed Scope

TDD 阶段先直接运行最窄的失败测试。随后运行 fast；若受影响测试不在 bounded profile 中，用可重复参数补入：

```sh
python dev/scripts/run_test_profile.py fast --extra-test tests.test_work_close --extra-test tests.test_scripts.ScriptBehaviorTests.test_context_execution_view_is_bounded_and_keeps_execution_invariants --json --compact --progress
```

`--extra-test` 接受 unittest module、class 或 method id。它只适用于 fast/precommit；full 已发现全部测试。

日常开发不要求在每次文件保存后运行 profile。先用最窄的 RED/GREEN test 驱动当前行为；一个可独立验证的实现切片完成后只运行一次 `fast`。`precommit` 只在准备提交时运行，`full` 只用于 merge/release 或大范围重构。

默认 precommit 不运行整个 `tests.test_scripts` 或 `tests.test_ui`。修改这些区域时必须通过 `--extra-test` 加入对应 module 或 method，不能把默认 profile 当成 changed-scope 自动推断。

## Output Contract

Runner 返回 `test_profile_v1`：

- `ok` 与每个 stage 的 return code。
- 实测总耗时、目标耗时和 `within_target`。
- unittest 的 tests/skips/reported duration。
- stdout/stderr 原始字节数。
- release track 的 passed/skipped 摘要。

成功输出不回传 unittest 点阵或完整 release JSON。失败时仅添加 stdout 与 stderr 各最多 8 KiB 的 tail。`--progress` 将 stage start/end 写入 stderr，适合长时间 full profile。

## Release Deduplication

Full profile 的 unittest stage 设置 `RESEARCH_COCKPIT_EXTERNAL_RELEASE_CHECK=1`，只跳过 `tests.test_release_check` 中与下一 stage 完全重复的完整 mutating release check。随后 runner 显式运行一次完整 release check。

这个环境变量不影响直接执行 `python -m unittest`，也不跳过 read-only、forward 或 usability coverage。不要在 profile 外手工设置它。

## Maintenance Rules

- `fast` 只保留少量稳定且实测快速的 sentinel；大型整模块不得因“核心”标签直接加入。
- 新增 model/state/packet contract 默认进入 precommit，并由当前改动通过 `--extra-test` 精确加入 fast；只有小型模块实测仍满足预算时才可整体加入 fast。
- `precommit` 同样保持 bounded；大型 work-close、handoff、model 和 legacy 模块只选择关键 public-contract method，完整模块由 changed scope 或 full 承担。
- 新增 facade transaction、schema/help parity 或 bounded startup contract 时加入 precommit。
- UI、stress、legacy breadth、大 fixture 和完整 usability 流程保留在 full，除非实测证明适合关键路径。
- Profile 中优先列 module；只从大型混合模块选择少量稳定的 public-contract method。
- 不以删除断言、共享可变 fixture 或放宽隔离换取速度。
- 每次调整 profile 后，运行 `--list`、fast、precommit，并记录实测 tests、skips 与 duration。

## Current Baseline

2026-07-21 的参考机器实测：

- 原始完整 unittest：839 tests、5 skips，约 286s。
- Fast 初始基线：169 tests，约 15s。
- Precommit 初始基线：270 tests、3 skips，加 read-only release check 共约 55s。
- Full profile 初始基线：846 tests、6 skips，加完整 release check 共约 264s。

2026-07-24 在 WSL 挂载盘工作树上，收缩前 fast 已增长到 204 tests、约 156s；这是本次将日常层改为固定 sentinel 集的直接原因。

同一环境收缩后实测：

- Fast：16 tests，约 1.2s。
- Precommit：32 tests 约 27.2s，加 read-only release check 共约 48.3s。

这些数字用于观察趋势，不是跨平台性能承诺。
