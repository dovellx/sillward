# 阶段 2：父循环上的门

读者如果只看过实现计划，这一页说明阶段 2 落地了什么。

## 加了什么

父循环上的七个决策点接到了 tau 的钩子上：`task.frame`、`tool.approval`、`tool.constraint`、`tool.injection`、`tool.admission`、`turn.continue`、`turn.completion`。记忆和角色还没接。

会话第一次处理输入、工具或回合结束时才读用户配置和进程覆盖，并创建判断核。`setup` 本身只注册钩子。默认仍是全部 `shadow`：询问并写 ledger，不改变工具、输出或回合。`SILLWARD_MODE_OVERLAY` 和 `~/.sillward/config.json` 里的 `modes` 可以单独把某个点设成 `active`。

`tool.approval` 为 active 时，`bash` 以及项目外的 `write` / `edit` 要看概率。一般调用至少 `0.80` 才执行；以 `pytest` 或 `ruff` 开头的项目内测试和 lint（前面可以是 `uv run` 或 `python -m`）至少 `0.70`。低于阈值时，有交互界面就询问，没有界面（`sillward -p` / `--print`，以及夜间评测）就挡住，并把理由交回模型，不等待输入。刚好达到阈值则执行。项目内的 `write` 和 `edit` 不经过这一点。`tool.constraint` 为 active 时，违反已陈述约束的 `bash`、`write`、`edit` 被挡住。这两个点裁判失败时，即使模式是 `shadow` 也挡住该次调用。

`tool.injection` 把 `bash` 输出按空行分段，其它工具只在输出不少于 4000 字符时才分段。active 时丢掉被标成指令的段落。`tool.admission` 只处理不少于 4000 字符的输出；active 时用不上的段落换成 `[omitted]`。两点在 `shadow` 时都保留原文。

`turn.continue` 为 active，且回合停住、裁决是继续时，用 follow-up 送回，同一条用户消息最多两次。`turn.completion` 为 active，模型声称完成且这轮没有跑过 `pytest` 或 `ruff` 时，送回一次。`task.frame` 为 active 且这条用户消息改变了任务时，把 frame 写进当前会话；用户消息原文不改。`shadow` 只记账。

Ledger 的 `action` 现在包括门真正做出的动作：`execute`、`block`、`follow_up`、`rewrite`，以及原来的 `unchanged` 和 `off`。`shadow` 下成功的裁决仍是 `unchanged`。

## 为什么存在

实现计划把阶段 2 定为判断核第一次改变父循环。阶段 1 只把裁决写成 `unchanged`、`block` 或 `off`，钩子还是空的。不把「问什么」和「裁决如何变成钩子返回值」分开，失败策略就会写进每个钩子里。

`shadow` 必须先能跑通：夜间对照以外的默认运行只记账，不改变 tau 的工具和回合。`active` 只在用户配置或当次进程覆盖里打开。非交互时不能询问，因为 `sillward -p` 和夜间评测没有人可问；低于阈值就挡住，是计划里的预期结果。

`task.frame` 放在会话里而不是 lesson 里，是因为它随会话结束。本阶段不实现 lessons 的读写。

## 落在哪个模块以及用了 tau 的哪条缝

| 模块 | 职责 |
| --- | --- |
| `extension.py` | `setup` 注册钩子。第一次事件时加载配置、选择裁判、创建 kernel |
| `gates/frame.py` | `task.frame` |
| `gates/approval.py` | `tool.approval` |
| `gates/constraint.py` | `tool.constraint` |
| `gates/injection.py` | `tool.injection` |
| `gates/admission.py` | `tool.admission` |
| `gates/turn.py` | `turn.continue` 与 `turn.completion` |
| `gates/support.py` | 组请求、提交 ledger 动作、分段、项目内路径、测试/lint 命令 |
| `kernel/runner.py` | `open` 先问裁判，`commit` 再写一行。`consult` 仍是阶段 1 的一次写完 |

用到的 tau 缝：

| tau 的缝 | 本阶段用它做 |
| --- | --- |
| `input` | `task.frame`。不改用户消息原文。lessons 的 recall 还没有 |
| `tool_call` | `tool.approval`、`tool.constraint`。返回挡住和理由，或放行 |
| `tool_result` | `tool.injection`、`tool.admission`。用钩子返回值替换交给模型的文本 |
| `send_user_message(deliver_as="follow_up")` | `turn.continue`、`turn.completion`。在回合结束、模型已经停住时送回 |
| `append_entry` | 会话内的 task frame，命名空间 `sillward.frame` |

`CodingSession.load` 和 `add_prompt_section` 本阶段没有用。子会话要到阶段 4。钩子不直接请求 Jev 或主模型，只走 kernel。

计划里的 `agent` 工具还不存在，所以「拉起子 agent 也要过 approval」要等角色那一阶段。本阶段的 approval 对象是 `bash` 和项目外的 `write` / `edit`。

## 怎么测试

测试在 `tests/test_phase2.py`。用脚本化的假 provider 驱动 tau 的 `CodingSession`，用假裁判注入答案。不访问网络，不读开发者自己的 `~/.sillward` 或 `~/.tau`。家目录、tau home 和项目都是临时目录。模式用 `SILLWARD_MODE_OVERLAY` 强制。

- `tool.approval` 为 active 时，概率 `0.79` 的 `bash` 不执行，工具结果是挡住；概率 `0.80` 会执行，并且不会先询问。
- 同一点为 `shadow` 时，概率 `0.10` 的命令仍会执行，ledger 里有这条裁决，默认行不含命令文本。
- 没有界面时，低于阈值的确认调用不会发生，命令被挡住。有界面时，拒绝则挡住，同意则执行。
- `pytest -q` 在 `0.70` 放行、在 `0.69` 挡住。同样是 `0.70` 的普通命令被挡住。
- 项目内的 `write` 和 `edit` 在裁判拒绝时仍会改文件；项目外的同一次调用被挡住，ledger 只有外面那一行。
- `tool.constraint` 为 active 时，违规的命令不执行；`shadow` 时照常执行并记账。裁判失败时，`shadow` 也会挡住。
- `tool.injection` 为 active 时，标成指令的段落消失，另一段还在；`shadow` 时两段都在。
- `tool.admission` 为 active 时，长输出里用不上的那段被换成 `[omitted]`，用得上的还在；`shadow` 时长输出原文都在。短输出即使含有会被丢掉的字样也不改。
- `turn.continue` 为 active 且裁判一直要求继续时，同一条用户消息正好多出两条 follow-up，provider 被调用三次。
- `turn.completion` 在没有跑过测试时送回一次；这轮已经跑过 `pytest` 时不再送回。
- `task.frame` 为 active 时，新任务写入会话里的一条 frame，下一条「无变化」不再写第二条。用户消息原文不变，ledger 默认行不含那句原文。`shadow` 不写入 frame，ledger 仍有记录。
