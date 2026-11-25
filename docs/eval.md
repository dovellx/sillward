# 测评

夜间对照在 Linux 上跑。可以是 GitHub 托管的 Ubuntu，也可以是本地的 Linux 容器。Windows 上的 `pytest` 只检查运行器和报告，不代替这次对照。

## 跑一次

在仓库根目录：

```text
uv run python -m eval.run
```

不在 Linux 上时，这条命令退出码是 2，不会启动模型。查看本次会使用的平台和模式覆盖，不启动模型：

```text
uv run python -m eval.run --plan
```

sillward 这一臂设置 `SILLWARD_MODE_OVERLAY` 为 `{"tool.approval":"active","turn.continue":"active"}`。其余决策点保持 `shadow`。stock tau 那一臂不加载本扩展。两臂使用 `eval/config.toml` 里钉死的同一个 provider 和 model id：`openai` / `gpt-5.4`，也就是所钉 `tau-ai==0.4.8` 的默认模型。这个钉来自 `eval/config.toml`，不读项目 `.sillward/config.json` 的 `models.agent`。评测工作目录没有该文件时，裁判模型也不受项目配置影响。回合数是 30，时限是 600 秒，两臂相同。密钥来自环境变量，例如 `OPENAI_API_KEY`，不写进报告。

GitHub 上的入口是 `.github/workflows/nightly.yml`。它在 `ubuntu-latest` 上、由手动触发运行，并设置同一个 `SILLWARD_MODE_OVERLAY`。

## 题目

每道题是 `eval/tasks/<id>/` 下一个目录：

| 路径 | 内容 |
| --- | --- |
| `instruction.md` | 交给模型的说明 |
| `start/` | 起始代码，复制成工作区 |
| `hidden/` | 隐藏测试。模型运行时工作区里没有这一目录，评分时才放进去 |
| `score.txt` | 一行评分命令。退出码 0 算通过 |

当前的一道题是 `sum_function`。评分命令是 `python -m pytest hidden`。通过与否只看 pytest，不看裁判。

## 报告

运行结束后写下 `eval/reports/<UTC 日期>.json`。字段是：

- `date`：UTC 日期
- `tau_pin`：`pyproject.toml` 里钉住的 `tau-ai` 版本
- `sillward_version`：已安装的 sillward 版本
- `model_id`：配置里钉住的模型
- `mode_overlay`：这一次实际叠上的模式
- `tasks`：每题两臂的 `passed`、`turns`、`blocked_delegations`
- `pass_rate`：两臂各自的通过率
- `blocked_delegations`：两臂各自拦住的委托次数

`turns` 是已经完成的模型调用次数。到达回合上限后运行器自己停住，这一下不计入。时限到了、调用还没完成的，也不计入。

`blocked_delegations` 只数被 `tool.approval` 挡住的 `agent` 调用。非交互运行里，拉起子 agent 低于 `0.80` 会被挡住。这个数字单列出来，避免把它读成角色实现坏了。

报告里没有密钥，也没有完整对话。低于 stock tau 的通过率不会让这次运行失败；数字留在报告里，并写入下面的产品接受记录。

## 产品接受记录

一次对照结束时，报告写在 `eval/reports/<UTC 日期>.json`，同一份内容再归档到 `eval/acceptance/<UTC 日期>/`。

| 路径 | 内容 |
| --- | --- |
| `report.json` | 这次报告的副本 |
| `record.json` | 这次对照的数字 |
| `ledgers/<题目>.jsonl` | 只有 sillward 的通过率低于 stock tau 时才有。内容是 sillward 那一臂的 ledger |

`record.json` 写下这些数字：UTC 日期、`tau_pin`、`sillward_version`、`model_id`、实际的 `mode_overlay`、每题两臂的 `passed` 和 `turns`、两臂 `pass_rate`、两臂 `blocked_delegations`。通过率低于 stock tau 时，`below_tau` 为 true，并写下下一步：阅读归档的 ledger，决定是否把一个仍是 shadow 的点拨到 active，然后重跑夜间。一次只能拨一个点。不低于时，记录里没有这一步，也不复制 ledger。

同一 UTC 日期再跑一次时，新记录写到 `eval/acceptance/<日期>-2/`。前一次的数字留在原来的目录里。

退出码是 0。通过率低于 stock tau 不会让这次运行失败，也不作为合并代码的条件。

在 Linux 以外的机器上可以先打印下一次夜间要用的覆盖。这条命令不启动模型，也不修改项目里的 `.sillward/config.json`：

```text
uv run python -m eval.run --promote tool.constraint
```

打印出来的 JSON 是夜间基线再加这一个点：`tool.approval`、`turn.continue` 和被点名的那个点，值都是 `active`。两个 `--promote`、一个基线里已经是 active 的点、或者十二个决策点以外的名字，退出码是 2，标准输出是空的。

下一次夜间把这份 JSON 放进 `SILLWARD_MODE_OVERLAY`。运行器以基线为底，最多再接受一个拨到 `active` 的点。基线的两个点保持 `active`。环境里多出一个以上的点时，入口退出码是 2，钉住的模型不会启动。

`uv run python -m eval.run --plan` 的 JSON 里有 `acceptance`，值是 `eval/acceptance`。它仍然不启动模型。

GitHub 工作流在运行结束后，把 `eval/reports/` 和 `eval/acceptance/` 存成该次运行的构件。构件里是分数和 ledger 的默认字段，不是密钥，也不是完整对话。
