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
| `fast` | 每个实现切片后的反馈 | 核心 model、assignment、packet、coordination 与 profile contract | 30s |
| `precommit` | 提交前 | fast + role facade integration + 精确 CLI sentinel + read-only release check | 60s |
| `full` | merge、release 或大范围重构前 | 全部 unittest discovery + 完整 release check | 360s |

Target 只写入结果的 `within_target`，不决定退出码。支持的平台、较慢磁盘或受控沙箱不会仅因 wall-clock 超标而失败。

## Changed Scope

TDD 阶段先直接运行最窄的失败测试。随后运行 fast；若受影响测试不在 bounded profile 中，用可重复参数补入：

```sh
python dev/scripts/run_test_profile.py fast --extra-test tests.test_work_close --extra-test tests.test_scripts.ScriptBehaviorTests.test_context_execution_view_is_bounded_and_keeps_execution_invariants --json --compact --progress
```

`--extra-test` 接受 unittest module、class 或 method id。它只适用于 fast/precommit；full 已发现全部测试。

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

- 新增纯 model/state/packet contract 时加入 fast。
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

这些数字用于观察趋势，不是跨平台性能承诺。
