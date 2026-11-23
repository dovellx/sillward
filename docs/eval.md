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

sillward 这一臂设置 `SILLWARD_MODE_OVERLAY` 为 `{"tool.approval":"active","turn.continue":"active"}`。其余决策点保持 `shadow`。stock tau 那一臂不加载本扩展。两臂使用 `eval/config.toml` 里钉死的同一个 provider 和 model id：`openai` / `gpt-5.4`，也就是所钉 `tau-ai==0.4.8` 的默认模型。回合数是 30，时限是 600 秒，两臂相同。密钥来自环境变量，例如 `OPENAI_API_KEY`，不写进报告。

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

报告里没有密钥，也没有完整对话。低于 stock tau 的通过率不会让这次运行失败；数字留在报告里。
