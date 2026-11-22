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

`action` 在本阶段有三种：`block`（会挡住工具的点在裁判失败时挡住）、`off`（其余点的裁判失败，或该点模式本来就是 `off`）、`unchanged`（裁决已经记下，还没有门去执行、送回或写入记忆）。

`record_state` 为 `true` 时，同一行多一个 `request`。其中 `parameters`、`file_content` 以及同类的参数、正文和密钥字段会变成 `[redacted]`。`frame_summary` 保留，调用方应传入摘要，而不是整段用户原文。
