# 配置

用户配置是家目录里的 `~/.sillward/config.json`。端点、密钥、模式和阈值只放在这里，或放在进程环境里。仓库不提供这份文件。项目目录中的任何文件，包括项目里的 `.sillward/config.json`，都不能改变 `judge` 或决策点的模式。

没有用户配置时：

- `judge` 为 `llm`
- 十二个决策点都是 `shadow`
- `thresholds.approval` 为 `0.80`，`approval_check` 为 `0.70`，`routing_override` 为 `0.80`
- `record_state` 为 `false`

## 字段

`judge` 是 `llm` 或 `jev`。`jev_model` 是 System One 的模型 id。

`modes` 的值是 `active`、`shadow` 或 `off`。可以写 `default`，也可以按决策点 id 覆盖。没写到的点使用 `default`；没有 `default` 时，该点的声明默认是 `shadow`。

`thresholds` 里没写到的键保持上面的默认值。

`record_state` 为 `true` 时，ledger 的同一行会带上脱敏后的请求。默认不带。

## 进程覆盖

环境变量 `SILLWARD_MODE_OVERLAY` 的值是一个 JSON 对象，键是决策点 id，值是 `active`、`shadow` 或 `off`。它叠在用户配置之上，只改变这一次读取得到的模式，不写回 `config.json`。下一次读取如果进程里没有这个变量，模式回到文件里的值。

对象里出现决策点以外的键（包括 `judge`）时，这一次读取失败，文件保持原样。

## 密钥与适配器

密钥不写入配置文件，也不写入 ledger 的默认行。`TYPESAFE_API_KEY` 来自环境变量。

- 配置里的 `judge` 是 `jev`，并且 `TYPESAFE_API_KEY` 非空：选用 jev。
- 配置写了 `jev` 但没有这个密钥：回到 `llm`。
- 配置写了 `llm`：即使环境里有 `TYPESAFE_API_KEY`，仍然用 `llm`。

`llm` 使用 tau 已经登录的模型。密钥先看该 provider 的环境变量（OpenAI 默认是 `OPENAI_API_KEY`），没有再看 tau home 里 `credentials.json` 的对应条目。当前会发非流式 HTTP 的只有两种 API：`openai-responses`（`{base}/responses`）和 `openai-completions`（`{base}/chat/completions`）。tau 自带目录里的 OpenAI 默认是 Responses。其它 provider API 在发出请求之前失败。

实现计划没有给出 TypeSafe 的主机名，仓库里也不写死这个 URL。没有调用方传入的 base URL 时，jev 这次询问失败，并且不会发请求。有 base URL 时，它按 chat completions 发送 `jev_model`，并要求回复是协议 JSON。

## Ledger

文件是项目下的 `.sillward/ledger.jsonl`，一行一个 JSON 对象。这个目录已被 git 忽略。

默认字段只有：`point`、`answer`、`probabilities`、`elapsed_ms`、`mode`、`adapter`、`action`。其中没有用户原文、工具参数和文件内容。

`record_state` 为 `true` 时，同一行多一个 `request`。其中 `parameters`、`file_content` 以及同类的参数、正文和密钥字段会变成 `[redacted]`。`frame_summary` 保留，调用方应传入摘要，而不是整段用户原文。

`action` 是这次裁决之后实际发生的事：

| action | 含义 |
| --- | --- |
| `execute` | active，工具调用被放行 |
| `block` | 调用被挡住。低于阈值、裁判拒绝、人拒绝，或会挡住工具的点发生裁判失败，都是这个动作 |
| `follow_up` | active，向模型送回了一条 follow-up |
| `rewrite` | active，写入了新的 task frame，或改写了工具输出 |
| `unchanged` | 裁决已记下，行为没有改变。`shadow` 下成功的裁决用这个动作 |
| `off` | 该点模式是 `off`，或一个不会挡住工具的点发生了裁判失败 |

## 父循环上的门

会话跑起来之后，父循环上的七个点才会改行为。模式仍是 `active`、`shadow`、`off`。`shadow` 询问并写 ledger，不改变这次调用、这段输出或这次回合。`off` 不询问。

`tool.approval` 看的是 `bash`，以及路径不在项目目录内的 `write` 和 `edit`。项目内的 `write` 和 `edit` 不询问这一点。放行条件是裁决答案为允许，并且该答案的概率达到阈值：

- 一般命令、项目外写入：`thresholds.approval`，默认 `0.80`
- 项目内的测试或 lint：命令以 `pytest` 或 `ruff` 开头，前面可以是 `uv run` 或 `python -m`。用 `thresholds.approval_check`，默认 `0.70`

交互界面里，允许但低于阈值时会询问。拒绝则挡住。`sillward --print`（`-p`）以及其它没有界面的运行（包括夜间评测）里，低于阈值直接挡住，理由交回模型，不会等待输入。概率刚好达到阈值就执行，不再询问。

`tool.constraint` 看 `bash`、`write`、`edit`。active 时，裁决为违反已陈述的约束就挡住。

`tool.approval` 和 `tool.constraint` 在裁判超时、HTTP 错误或协议外内容时挡住该次调用，不论当前是 `active` 还是 `shadow`。

`tool.injection` 把 `bash` 输出按空行分段。其它工具的输出只有不少于 4000 个字符时才分段。active 时，被判为指向模型的指令段落不会进入上下文。`shadow` 时原文保留。

`tool.admission` 只处理不少于 4000 个字符的工具输出，同样按空行分段。active 时，当前用得上的段落保留，其余换成指针 `[omitted]`。`shadow` 时原文保留。

`turn.continue` 在回合已经停住（没有继续调用工具）且裁决为继续时，用 follow-up 把回合送回去。同一条用户消息最多两次。送回的文字是：`Continue the authorized work. Do the next step now.`

`turn.completion` 在模型停住并声称完成、而这轮用户消息里没有实际跑过 `pytest` 或 `ruff` 时，送回一次：`You claimed the work was complete without a verification action. Run the project's tests or lint, then continue.` 同一条用户消息只送这一次。若两点都要送，先送 completion 这一条。

`task.frame` 判断这条用户消息是新任务、硬约束、纠正、子目标，还是无变化。active 时，只有这次不是无变化、并且和会话里已经记下的 frame 不同，才写一条新的 frame。用户消息原文不改。`shadow` 只记账。这个 frame 随会话结束，不是 lesson。
