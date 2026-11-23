# 阶段 5：测评运行器

读者如果只看过实现计划，这一页说明阶段 5 落地了什么。

## 加了什么

题目目录、两臂运行器和一份报告。假模型可以把两臂都跑完。另外放进一道可以由 pytest 判通过或失败的 Python 题。

`eval/config.toml` 钉住 provider、model id、回合数和时限：`openai`、`gpt-5.4`、30 回合、600 秒。`gpt-5.4` 是所钉 `tau-ai==0.4.8` 的默认模型，写死在文件里，不在运行时再去读 tau 的默认值。

一道题是一个目录：

| 路径 | 内容 |
| --- | --- |
| `instruction.md` | 交给模型的说明 |
| `start/` | 起始代码 |
| `hidden/` | 隐藏测试 |
| `score.txt` | 一行评分命令 |

现在的题是 `eval/tasks/sum_function`。评分命令是 `python -m pytest hidden`。隐藏测试只在评分时复制到一份临时工作区，模型跑的时候那份目录不在。通过与否只看命令的退出码。

两臂用同一份题目、同一个 model id、同一份回合数和时限：

- tau：不加载本扩展，相当于 `--no-extensions`
- sillward：只加载 `extension.py`，并把 `SILLWARD_MODE_OVERLAY` 设成 `{"tool.approval":"active","turn.continue":"active"}`。其余点仍是 `shadow`

报告是一个 JSON。字段有日期（UTC）、tau 版本钉、sillward 版本、model id、实际模式覆盖、每题两臂的通过与否和回合数、两臂通过率，以及两臂被挡住的委托次数。委托被挡只数结果里带有 `Tool call blocked` 的 `agent` 调用。报告不写密钥，也不写完整对话。通过率低于另一臂不会让这次运行失败。

回合数只计已经完成的模型调用。到达上限之后，运行器自己回一条没有工具调用的停止，这一下不计入，也不会再把调用转给模型。时限包住的是这一次 `prompt`。调用被取消、没有完成时，回合数不加。

夜间入口是 `uv run python -m eval.run`。`--plan` 只打印平台和模式覆盖，不启动模型。当前进程不是 Linux 时退出码为 2，两臂都不会跑。GitHub 工作流 `.github/workflows/nightly.yml` 在 `ubuntu-latest` 上，由手动触发，并设置同一个 `SILLWARD_MODE_OVERLAY`。这一阶段不调用钉住的模型。

## 为什么存在

实现计划把阶段 5 定成测评运行器，不是第一次真实对照。提交门禁仍然用假模型和假裁判，不断言模型会不会写代码。要断言的是：两臂都能留下字段齐全的报告，至少一道 Python 题可以由 pytest 判成通过或失败，夜间入口指定 Linux 并设置进程覆盖，报告样例里没有密钥。

真实模型、全部题目、以及「通过率是否不低于 stock tau」，是阶段 6 的产品接受记录。这里把运行器和报告先跑通，并放进一道真题，让那次对照有可以调用的入口。

`tool.approval` 在非交互运行里会挡住低于 `0.80` 的委托。报告把这个次数单列出来，避免把它读成角色实现坏了。

## 落在哪个模块以及用了 tau 的哪条缝

| 模块 | 职责 |
| --- | --- |
| `eval/config.toml` | 钉死的 provider、model id、回合数、时限 |
| `eval/tasks/` | 每题一个目录 |
| `eval/run.py` | 发现题目、跑两臂、评分、夜间入口 |
| `eval/report.py` | 组装并写下报告。只接收分数，不接收对话 |
| `.github/workflows/nightly.yml` | Linux 上的手动入口，设置 `SILLWARD_MODE_OVERLAY` |

用到的 tau 缝：

| tau 的缝 | 本阶段用它做 |
| --- | --- |
| `CodingSession.load` | 两臂各开一个会话。tau 臂的 `extension_paths` 是空的，并且 `extensions_enabled` 为 false，这就是 `--no-extensions`。sillward 臂只把本包的 `extension.py` 放进 `extension_paths`，目录发现仍然关着 |
| 已有的扩展钩子 | 模式覆盖生效之后，`tool.approval` 和 `turn.continue` 仍走阶段 2 接上的钩子。运行器不新加钩子 |

`CodingSession` 没有把 harness 的 `max_turns` 露出来。回合上限因此包在传给会话的 provider 上：预算用完就停止，不再调用里面的模型。没有改 tau 的源码。时限是会话 `prompt` 外面的 `asyncio.timeout`，也不是 tau 的参数。

两臂的系统提示、skills 和自动压缩是同一套。skills 关掉，自动压缩关掉，这样回合数就是代理循环里的模型调用，也不去读这台机器上的 skill 文件。非交互运行使用 `trust_override="approve"`，避免停下来等人。

Linux 入口用 tau 的 `resolve_provider_selection` 和 `create_model_provider`，按配置里的 provider 和 model id 做真正的模型。测试不走这条路径，它们传入假 provider。假模型因此可以留在进程里，不必改 tau，也不必访问网络。

评分不经过裁判，也不经过钩子。它在临时目录里运行题目自己的命令。

## 怎么测试

测试在 `tests/test_phase5.py`。不访问网络，不读开发者自己的 `~/.sillward` 或 `~/.tau`。家目录、tau home 和工作区都是临时目录。假裁判打在 `sillward.judges.factory.select_judge` 上，因为 tau 是按文件加载 `extension.py` 的，加载时才从工厂取出裁判。

- `eval/config.toml` 的 provider、model、回合数、时限分别是 `openai`、`gpt-5.4`、30、600。
- `sum_function` 的起始代码评分为失败，写成 `return left + right` 之后评分为通过。评分命令就是 `python -m pytest hidden`。
- 假模型下两臂都写出同一份报告。日期是传入的 UTC 时间 `2026-11-23`（当天 23:30 UTC，本地若用东八区会算成第二天）。tau 钉是 `0.4.8`，sillward 版本是 `0.1.0`，model id 是 `gpt-5.4`，模式覆盖只有 `tool.approval` 和 `turn.continue` 为 `active`。两臂都通过，回合数都是 2，通过率都是 1。报告文本里没有环境里的 `OPENAI_API_KEY`、`TYPESAFE_API_KEY`，没有模型停下时吐出的标记，也没有题目说明。
- 两臂的工作区各自写上解答。隐藏测试没有留在工作区里。tau 臂没有 ledger。sillward 臂的 `turn.continue` 行模式是 `active`。
- 配置把回合数改成 1 时，两臂都只把一次调用转给假模型。假模型若被叫到第二次会直接报错。
- 时限是 1 秒、假模型一直等待时，两臂都判失败，回合数是 0，报告文件仍然写下。
- 假模型去拉 reviewer、裁判以 `0.50` 允许时，sillward 臂的委托被挡次数是 1，tau 臂是 0。这道题评分为失败。
- `--plan` 打印 `linux` 和那份覆盖。平台被看成 `win32` 时，入口退出码是 2，并且不会调用两臂运行器。工作流文件含有 `ubuntu-latest` 和同一份覆盖 JSON。`docs/eval.md` 写明 Linux、`SILLWARD_MODE_OVERLAY` 和这份 JSON。
