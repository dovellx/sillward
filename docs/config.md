# 配置

用户配置是当前项目里的 `.sillward/config.json`。模式、阈值和 `llm` 的 API key 放在这里。编码模型和 `llm` 裁判的模型也在这份文件里。家目录的 `~/.sillward/config.json` 不参与解析。项目根上的其它文件，例如 `config.json` 或 `sillward.json`，也不参与解析。这份文件含有密钥，已被 git 忽略。仓库里另有 `.sillward/config-template.json`，把文件名改成 `config.json` 后填入 `llm_api_key` 即可。模板本身不会被读取。在别的目录里运行时，读的是那个目录自己的 `.sillward/config.json`；没有文件就用下面的默认值。

没有用户配置时：

- `judge` 为 `llm`
- 十二个决策点都是 `shadow`
- `thresholds.approval` 为 `0.80`，`approval_check` 为 `0.70`，`routing_override` 为 `0.80`
- `record_state` 为 `false`
- `llm_api_key` 为空
- 不指定 `models.agent` 和 `models.judge`

## 字段

`judge` 是 `llm` 或 `jev`。`llm_api_key` 是 `llm` 裁判调用模型时用的密钥。`jev_model` 是 System One 的模型 id。

`models` 给编码会话和 `llm` 裁判指定 tau 目录里已有的模型，以及各自的密钥。可以两个都写，也可以只写一侧：

```json
"models": {
  "agent": { "provider": "deepseek", "model": "deepseek-v4-pro", "api_key": "sk-..." },
  "judge": { "provider": "deepseek", "model": "deepseek-v4-pro", "api_key": "sk-..." }
}
```

`models.agent` 是这次编码会话的模型，父会话用它。`models.agent.api_key` 是这把钥匙。启动编码会话时，sillward 用文件里的这把钥匙，不用再导出环境变量，也不用手改 tau 的 `credentials.json`。tau 会先读它自己的凭据，所以这次启动会用文件里的钥匙盖过同名凭据和环境变量。钥匙不会出现在转发给 tau 的命令行参数里。

`models.judge` 是 `llm` 裁判的模型。`models.judge.api_key` 有值时，裁判用这把钥匙；没写时仍用文件里的 `llm_api_key`。

`provider` 和 `model` 都可以只写一个。只写 `model` 时，编码会话让 tau 用默认 provider 配这个模型 id，裁判也用默认 provider 配这个 id。只写 `provider` 时，用该 provider 的默认模型。

没写 `models`、写成 `null`、某个目标缺席或为 `null`、目标是 `{}`、或者 `provider`、`model`、`api_key` 都是 `null`，那一侧和没配置时一样。模型 id 必须已经在 tau 的目录里。内置模型来自 tau 自带的 catalog，用户追加的写在 tau 主目录的 `catalog.toml`。这里不写地址、请求头或另一份 catalog。自定义网关先加进 tau 的 catalog，再在这里写它的 provider 名、模型 id 和密钥。`TYPESAFE_API_KEY` 仍只给 jev，不写进这个文件。

命令行上的 `--provider`、`--model` 或 `-m` 盖过整个 `models.agent`。只写了其中一个时，文件里的另一个也不补上，并且不会写回 tau 的 `providers.json`。implementer、reviewer、explorer 跟父会话，不单独配模型。`jev_model` 仍只给 jev。`judge` 是 `jev` 时，`models.judge` 不参与。

`modes` 的值是 `active`、`shadow` 或 `off`。可以写 `default`，也可以按决策点 id 覆盖。没写到的点使用 `default`；没有 `default` 时，该点的声明默认是 `shadow`。

`thresholds` 里没写到的键保持上面的默认值。

`record_state` 为 `true` 时，ledger 的同一行会带上脱敏后的请求。默认不带。

## 进程覆盖

环境变量 `SILLWARD_MODE_OVERLAY` 的值是一个 JSON 对象，键是决策点 id，值是 `active`、`shadow` 或 `off`。它叠在用户配置之上，只改变这一次读取得到的模式，不写回 `config.json`。下一次读取如果进程里没有这个变量，模式回到文件里的值。

对象里出现决策点以外的键（包括 `judge` 和 `models`）时，这一次读取失败，文件保持原样。不要把模型放进这个环境变量。

## 密钥与适配器

`llm` 的密钥写在配置文件的 `llm_api_key`。它不写入 ledger 的默认行。`OPENAI_API_KEY` 和 tau home 里的 `credentials.json` 不参与这把钥匙。没写、或写成 `null` 时，询问在发出请求之前失败。

`TYPESAFE_API_KEY` 仍来自环境变量，不写入配置文件。

- 配置里的 `judge` 是 `jev`，并且 `TYPESAFE_API_KEY` 非空：选用 jev。
- 配置写了 `jev` 但没有这个密钥：回到 `llm`。
- 配置写了 `llm`：即使环境里有 `TYPESAFE_API_KEY`，仍然用 `llm`。

没写 `models.judge` 时，`llm` 使用 tau 默认 provider 的默认模型和接口；写了则使用该 provider 和模型。`models.judge.api_key` 有值时用这把钥匙，否则用上面的 `llm_api_key`。当前会发非流式 HTTP 的只有两种 API：`openai-responses`（`{base}/responses`）和 `openai-completions`（`{base}/chat/completions`）。tau 自带目录里的 OpenAI 默认是 Responses。其它 provider API 在发出请求之前失败。

`jev` 按 TypeSafe System One 询问，和 mu 的 `jev-direct` 同一条接口：`POST https://api.typesafe.ai/v1/systemone`，请求体是 `model`、`state`、`questions`。`Authorization` 是 `Bearer` 加上 `TYPESAFE_API_KEY`。`jev_model` 不写时用 `jev-latest`。`choice` 题的候选是选项名，`score` 题的候选是从 0 开始的有序档位；返回的 `answers.verdict` 再收成裁决里的答案和每个候选的概率。

## Ledger

文件是项目下的 `.sillward/ledger.jsonl`，一行一个 JSON 对象。这个目录已被 git 忽略。

默认字段只有：`point`、`answer`、`probabilities`、`elapsed_ms`、`mode`、`adapter`、`action`。其中没有用户原文、工具参数和文件内容。

`record_state` 为 `true` 时，同一行多一个 `request`。其中 `parameters`、`file_content` 以及同类的参数、正文和密钥字段会变成 `[redacted]`。`frame_summary` 保留，调用方应传入摘要，而不是整段用户原文。

`action` 是这次裁决之后实际发生的事：

| action | 含义 |
| --- | --- |
| `execute` | active，工具调用被放行，或 `swarm.routing` 改派了角色 |
| `block` | 调用被挡住。低于阈值、裁判拒绝、人拒绝，或会挡住工具的点发生裁判失败，都是这个动作 |
| `follow_up` | active，向模型送回了一条 follow-up |
| `rewrite` | active，写入了新的 task frame，或改写了工具输出 |
| `capture` | active，一条 lesson 写入了 `.sillward/lessons.jsonl` |
| `recall` | active，一条选中的 lesson 放进了本轮用户消息 |
| `discard` | 子会话的写入请求被丢掉。这次没有询问裁判 |
| `unchanged` | 裁决已记下，行为没有改变。`shadow` 下成功的裁决用这个动作 |
| `off` | 该点模式是 `off`，或一个不会挡住工具的点发生了裁判失败 |

## 父循环上的门

会话跑起来之后，父循环上的七个点才会改行为。模式仍是 `active`、`shadow`、`off`。`shadow` 询问并写 ledger，不改变这次调用、这段输出或这次回合。`off` 不询问。

`tool.approval` 看的是 `bash`，路径不在项目目录内的 `write` 和 `edit`，以及拉起子 agent。项目内的 `write` 和 `edit` 不询问这一点。放行条件是裁决答案为允许，并且该答案的概率达到阈值：

- 一般命令、项目外写入、拉起子 agent：`thresholds.approval`，默认 `0.80`
- 项目内的测试或 lint：命令以 `pytest` 或 `ruff` 开头，前面可以是 `uv run` 或 `python -m`。用 `thresholds.approval_check`，默认 `0.70`

交互界面里，允许但低于阈值时会询问。拒绝则挡住。`sillward --print`（`-p`）以及其它没有界面的运行（包括夜间评测）里，低于阈值直接挡住，理由交回模型，不会等待输入。概率刚好达到阈值就执行，不再询问。

`tool.constraint` 看 `bash`、`write`、`edit`。active 时，裁决为违反已陈述的约束就挡住。

`tool.approval` 和 `tool.constraint` 在裁判超时、HTTP 错误或协议外内容时挡住该次调用，不论当前是 `active` 还是 `shadow`。

`tool.injection` 把 `bash` 输出按空行分段。其它工具的输出只有不少于 4000 个字符时才分段。active 时，被判为指向模型的指令段落不会进入上下文。`shadow` 时原文保留。

`tool.admission` 只处理不少于 4000 个字符的工具输出，同样按空行分段。active 时，当前用得上的段落保留，其余换成指针 `[omitted]`。`shadow` 时原文保留。

`turn.continue` 在回合已经停住（没有继续调用工具）且裁决为继续时，用 follow-up 把回合送回去。同一条用户消息最多两次。送回的文字是：`Continue the authorized work. Do the next step now.`

`turn.completion` 在模型停住并声称完成、而这轮用户消息里没有实际跑过 `pytest` 或 `ruff` 时，送回一次：`You claimed the work was complete without a verification action. Run the project's tests or lint, then continue.` 同一条用户消息只送这一次。若两点都要送，先送 completion 这一条。

`task.frame` 判断这条用户消息是新任务、硬约束、纠正、子目标，还是无变化。active 时，只有这次不是无变化、并且和会话里已经记下的 frame 不同，才写一条新的 frame。用户消息原文不改。`shadow` 只记账。这个 frame 随会话结束，不是 lesson。

## 记忆

Lessons 文件是项目下的 `.sillward/lessons.jsonl`，一行一个 JSON 对象。这个目录已被 git 忽略。

一条 lesson 的字段是 `id`、`body`、`source`、`created_at`、`probability`。`source` 是 `user` 或 `agent`。`created_at` 是 UTC 时间。`probability` 是写下这一条时，同意捕获那个答案的概率。

`memory.capture` 只看父会话里用户自己输入的消息，不看扩展送回的 follow-up。active 且裁决为 `yes` 时追加一条，来源是 `user`。`shadow` 询问并记账，不写文件。

去重时先去掉两端空白，再把连续空白收成一个空格，然后比较正文。这样相同的正文不产生第二条，文件里留下先写下的那条原文。规范化之后仍不同的两条都保留，包括互相矛盾的两条。没有合并。

`memory.recall` 对文件里的每一条各问一次。候选是这条 lesson 的 id 和 `skip`。active 且答案是该 id 时，选中的正文按文件顺序放进本轮用户消息前面：

```text
Applicable lessons:
- <正文>

<用户原文>
```

模型看到的是这段文字，会话里记下的用户消息也是这段。没选中的正文不出现。`shadow` 不改用户消息；ledger 的 `answer` 是本会送入的 lesson id，不是正文。刚捕获的那条不进入同一条用户消息，从下一条用户消息起才参与 recall。父会话和三个角色调用同一个选择。子会话看到的是它自己的任务文本，前面可以有选中的 lessons。

`memory.worth` 的候选是 `reusable`、`once`、`known`。active 时只有 `reusable` 会追加，来源是 `agent`。`once` 和 `known` 不写。没有单独的命令；父会话在记下 agent 自己提出的 lesson 时才问这个点。

子会话的捕获请求和 worth 请求直接丢弃，不询问裁判，不写文件。ledger 的动作是 `discard`。

## 角色

父 agent 用工具 `agent` 做一层委托。参数是 `role`、`task`、`prefix`。`role` 是 `implementer`、`reviewer` 或 `explorer`。reviewer 的 `task` 里放要看的 diff，explorer 的 `task` 里放要找的目标。`prefix` 是 implementer 可以 `write` 和 `edit` 的目录。

子会话使用父 agent 当时的模型 id 和 thinking level。子会话没有 `agent` 工具，不能再委托。reviewer 和 explorer 只有 `read`。implementer 还有 `write`、`edit`、`bash`。

前缀外的 `write` 或 `edit` 在文件被改之前失败。这个检查不询问裁判。`bash` 不按路径前缀拦住。拉起子 agent 在 `tool.approval` 为 `active` 时要过一般阈值；子会话里随后的命令不再单独询问。

`swarm.routing` 的候选是这三个角色。默认 `shadow`：询问并写 ledger，子会话仍用父 agent 指定的角色。`active` 时，裁决概率达到 `thresholds.routing_override`（默认 `0.80`）并且和指定角色不同，才改派。低于这个数不改派。ledger 在改派时的动作是 `execute`，不改派时是 `unchanged`。

`swarm.patch` 只看 implementer 这次 `agent` 调用的结果。候选是 `within` 和 `beyond`。默认 `shadow`：询问并写 ledger，不撤回已经写下的文件，也不替换交回父 agent 的文字。动作是 `unchanged`。
