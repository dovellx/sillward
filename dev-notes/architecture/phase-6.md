# 阶段 6：第一次真实对照

读者如果只看过实现计划，这一页说明阶段 6 落地了什么。

## 加了什么

一次夜间对照结束时，报告会归档，数字写进一份产品接受记录。通过率低于 stock tau 不会让这次运行失败，也不作为合并代码的条件。

归档目录是 `eval/acceptance/<UTC 日期>/`：

| 路径 | 内容 |
| --- | --- |
| `report.json` | `eval/reports/<UTC 日期>.json` 的副本 |
| `record.json` | 这次对照的数字 |
| `ledgers/<题目>.jsonl` | 只有 sillward 低于 tau 时才复制。来源是 sillward 那一臂工作区里的 `.sillward/ledger.jsonl` |

`record.json` 里的数字是：UTC 日期、tau 版本钉、sillward 版本、model id、这一次实际的模式覆盖、每题两臂的通过与否和回合数、两臂通过率、两臂被挡住的委托次数。低于 tau 时 `below_tau` 为 true，并写下下一步：阅读这些 ledger，决定是否把一个仍是 shadow 的点拨到 active，然后重跑夜间。`promote_at_most` 是 1。不低于时没有这一步，ledger 也不复制。

同一 UTC 日期再跑，新目录是 `<日期>-2`。前一份记录保持原样。

夜间基线仍是 `tool.approval` 和 `turn.continue` 为 `active`。运行器读进程里的 `SILLWARD_MODE_OVERLAY`：空的、或者只重复基线，就用基线；在基线之外再把恰好一个 shadow 点设为 `active`，这次对照就带上它。两个额外的点、改掉基线、或者未知的点，入口退出码是 2，钉住的模型不会创建。

打印下一次覆盖、且不改用户配置的命令是 `uv run python -m eval.run --promote <点名>`。它在 Linux 以外也能跑。两个 `--promote`、基线里已经 active 的点、十二个点以外的名字，退出码是 2，标准输出是空的。

`--plan` 多一个字段 `acceptance`，值是 `eval/acceptance`。GitHub 工作流在跑完后把 `eval/reports/` 和 `eval/acceptance/` 存成该次运行的构件。

## 为什么存在

实现计划把阶段 6 定成第一次真实对照，不是再加一个决策点。要做的是：在 Linux 上用 `eval/config.toml` 钉住的模型跑完全部题目，把结果写成产品接受记录。通过率已经不低于 stock tau 不是合并条件。低于的时候，下一轮工作是人去读 ledger，决定要不要把某一个 shadow 点拨到 active，然后重跑。一次只拨一个点。从 shadow 拨到 active 仍然不写回 `~/.sillward/config.json`，也不按 ledger 自动拨。

阶段 5 已经能用假模型写出报告。这一阶段把那份报告收成可以留下来的记录，并给「只拨一个点再重跑」一个不会改用户配置的入口。提交门禁仍然不访问网络，不断言模型会不会写代码。

## 落在哪个模块以及用了 tau 的哪条缝

| 模块 | 职责 |
| --- | --- |
| `eval/acceptance.py` | 归档报告、写下数字、计算是否低于 tau、生成只多一个点的覆盖 |
| `eval/run.py` | 跑完全部题目；Linux 入口使用钉住的模型；`--promote` 与 `--plan` |
| `eval/reports/` | 阶段 5 已经在写的报告。归档是它的一份副本 |
| `.github/workflows/nightly.yml` | Linux 上的手动入口。跑完后把报告和接受记录存成构件 |

没有新的 tau 钩子。两臂仍是阶段 5 的 `CodingSession.load`：tau 臂不加载本扩展，sillward 臂只加载 `extension.py`。ledger 是判断核本来就追加的 `.sillward/ledger.jsonl`。接受记录只在对照结束之后读取 sillward 那一臂的文件，不在钩子里再问一次裁判。

钉住的模型仍走 tau 的 `resolve_provider_selection` 和 `create_model_provider`，provider 和 model id 来自 `eval/config.toml`。测试把这个构造换掉，所以单测不访问网络，也不改 tau 的源码。回合上限和时限仍包在 sillward 这边，不使用 harness 内部没有露出的参数。

## 怎么测试

测试在 `tests/test_phase6.py`。不访问网络，不读开发者自己的 `~/.sillward` 或 `~/.tau`。家目录、tau home 和工作区都是临时目录。假裁判打在 `sillward.judges.factory.select_judge` 上。

- 一份手写的报告在 sillward 通过率 0、tau 通过率 0.5 时，归档副本与报告文件一致。记录里有日期 `2026-11-23`、tau 钉 `0.4.8`、sillward 版本 `0.1.0`、model id `gpt-5.4`、基线覆盖、两题的通过与否和回合数、通过率和被挡次数。`below_tau` 为 true，下一步的 `promote_at_most` 是 1，两份 ledger 的字节与来源文件相同。记录文本里没有报告夹带的对话标记。`acceptance_exit_code` 是 0。
- 两臂通过率都是 0.5 时，记录里没有下一步，也没有 `ledgers/`。退出码仍是 0。
- 同一日期写第二次时，第一份记录的通过率仍是 1，第二份在 `<日期>-2`，并且 `below_tau` 为 true。
- `--promote tool.constraint` 在被看成 `win32` 的进程里打印基线加上 `tool.constraint`，退出码 0。临时家目录里的 `config.json` 字节不变。
- 两个 `--promote`、`--promote tool.approval`、`--promote memory.merge` 都是退出码 2，标准输出为空，标准错误里不是「请到 Linux 上跑」那一句。
- `--plan` 的 `acceptance` 是 `eval/acceptance`，覆盖仍是基线。`acceptance_root()` 指向仓库里的 `eval/acceptance`。
- 假模型下，临时题目目录里的 `alpha` 和 `beta` 都进入同一份记录。model id 是 `gpt-5.4`，两臂都通过，`below_tau` 为 false。归档和记录里没有密钥、没有停下来时吐出的标记、也没有题目说明。传给假模型的 model 参数都是 `gpt-5.4`。用户配置文件保持原样。
- 只有 tau 臂写下解答时，记录的通过率是 sillward 0、tau 1，退出码按这份报告算是 0。归档的 `ledgers/sum_function.jsonl` 与工作区 ledger 字节相同，模式覆盖仍是基线。
- 环境覆盖在基线之外把 `tool.constraint` 设为 `active` 时，记录里的覆盖有这三个点，sillward 臂 ledger 里该点的 `mode` 是 `active`。
- 基线之外同时把 `tool.constraint` 和 `tool.injection` 设为 `active` 时，`run_comparison` 抛出 `AcceptanceError`，假模型没有被调用，报告文件没有写出来。
- Linux 入口在假 provider 下写出接受记录。题目 id 与 `eval/tasks/` 里发现的全部题目一致，model id 和假模型收到的 model 都是 `gpt-5.4`，tau 钉是 `0.4.8`，退出码是 0。
- 平台被看成 `win32` 时，入口退出码是 2，不创建钉住的模型，接受目录也不出现。
- Linux 入口在基线之外看到两个额外的 active 点时，退出码是 2，钉住的模型不会创建，接受目录也不出现。
