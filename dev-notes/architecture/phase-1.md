# 阶段 1：协议、配置、ledger

读者如果只看过实现计划，这一页说明阶段 1 落地了什么。

## 加了什么

一次询问和一次裁决。请求带有决策点 id、问题类型（`choice` 或 `score`）、候选、任务 frame 的摘要，以及该点需要的最少状态。裁决带有答案、每个候选的概率、耗时和适配器名。答案不在候选里，或某个候选没有数值概率，就算这次裁判失败。

十二个决策点声明在 `kernel/points.py`。默认模式都是 `shadow`。只有 `tool.approval` 和 `tool.constraint` 在裁判失败时得到 `block`（挡住）。其余点得到 `off`（当作该点关闭）。模式本身是 `off` 时不询问裁判。

假裁判按测试注入的答案返回，不访问网络。`llm` 和 `jev` 会发非流式 HTTP，并用录制的响应做测试。超时、非 200、以及回复里没有协议 JSON，都是裁判失败，失败信息里不放响应正文和密钥。

用户配置只从家目录的 `~/.sillward/config.json` 读取。没有文件时：裁判 `llm`，全部 `shadow`，阈值 `0.80` / `0.70` / `0.80`，`record_state` 为 false。`modes.default` 覆盖没单列的点。`SILLWARD_MODE_OVERLAY` 只改这一次解析出来的模式，不写回文件，也不能改 `judge`。项目目录里的配置文件不参与解析。

配置写 `jev` 且环境变量 `TYPESAFE_API_KEY` 非空时选用 jev，否则用 `llm`。配置写 `llm` 时，有这个密钥也不改选。

Ledger 写在项目的 `.sillward/ledger.jsonl`。默认一行是决策点、答案、各候选概率、耗时、模式、适配器名、随后动作。成功时动作是 `unchanged`：裁决已记下，本阶段还没有门把它变成执行、送回、recall、捕获或丢弃。`record_state` 为 true 时，同一行追加脱敏后的请求；参数和文件内容变成 `[redacted]`。

`sillward` 命令的 `setup` 仍然是空的。启动 tau 时还不会读这份配置，也不会写 ledger。

## 为什么存在

实现计划把阶段 1 定为判断核的合同，而不是把判断接到 tau 的钩子上。后面的门和角色都要经过同一个入口：查模式、问裁判、执行失败策略、写 ledger。失败策略如果散落到各个钩子里，会挡住工具的点和其余点就会各写一套。

配置和覆盖放在这一阶段，是为了把「谁能改模式」钉死。端点、密钥、模式、阈值来自用户配置和进程环境。项目里的文件不能改 `judge` 或模式。夜间对照用的 `SILLWARD_MODE_OVERLAY` 因此只能影响当次进程，进程结束就消失。

Ledger 的默认行故意很窄。lessons 和对话里会有项目私有规则，但 ledger 只记裁决和随后动作。调试时才用 `record_state` 留下脱敏后的请求。

## 落在哪个模块以及用了 tau 的哪条缝

| 模块 | 职责 |
| --- | --- |
| `protocol.py` | 请求、裁决、协议检查。不知道 tau |
| `config.py` | 读取用户配置，叠上进程覆盖 |
| `judges/fake.py` | 测试注入的答案 |
| `judges/llm.py` | 调用 tau 已经登录的模型 |
| `judges/jev.py` | TypeSafe System One。没有 base URL 时失败，不发请求 |
| `judges/remote.py` | 两边共用的 HTTP 和协议 JSON 解析 |
| `judges/factory.py` | 按配置和 `TYPESAFE_API_KEY` 选择适配器 |
| `kernel/points.py` | 十二个点的默认模式和失败类别 |
| `kernel/runner.py` | 查模式、问裁判、应用失败策略、写 ledger |
| `kernel/ledger.py` | 追加 `.sillward/ledger.jsonl` |

计划里那张钩子表（`input`、`tool_call`、`tool_result`、`send_user_message`、`append_entry`、`CodingSession.load`、`add_prompt_section`）这一阶段都没有使用。`extension.py` 的 `setup` 仍然不注册工具、命令或钩子。门要到阶段 2 才把这里的 `block` / `off` / 裁决变成钩子返回值。

`llm` 用的是另一条缝：tau 的 provider 设置和凭证，不是上面那些钩子。调用方传入 tau 的 home。有 `providers.json` 时用 tau 的加载函数读出默认 provider 和它的 `default_model`；没有这份文件时用 tau 自带目录。密钥先取该 provider 的环境变量，没有再取 `credentials.json` 里 `credential_name` 那一条。`api` 为 `openai-responses` 时向 `{base}/responses` 发非流式请求；为 `openai-completions` 时向 `{base}/chat/completions` 发。tau 自带的 OpenAI 条目是 Responses，所以已登录的默认模型走这条。其它 API 在发请求之前失败。协议、配置、ledger 和假裁判都不导入 tau。

jev 不读 tau。实现计划只规定了模型 id 和 `TYPESAFE_API_KEY`，没有给出主机，所以仓库不写死 URL。调用方传入 base URL 之后，它按 chat completions 发送 `jev_model`。

## 怎么测试

测试在 `tests/test_phase1.py` 和 `tests/test_phase1_judges.py`。不访问网络，不读开发者自己的 `~/.sillward` 或 `~/.tau`。配置和 tau home 都用临时目录。HTTP 用 `httpx` 的 `MockTransport` 回放录制的响应。

- 假裁判注入的答案、概率、耗时和适配器名出现在 ledger 那一行。
- 默认行的键只有计划里的那七个。请求里的用户原文、工具参数、文件内容，以及失败信息里夹带的密钥，都不在这一行。
- `tool.approval` 和 `tool.constraint` 在超时、HTTP 错误、答案不在候选中、概率缺失时，动作是 `block`，答案为空。
- 其余十个点在同样的失败下动作是 `off`。
- 模式为 `off` 时不调用裁判，ledger 里没有注入的答案。
- 临时项目里放了 `.sillward/config.json` 和别的配置文件，并把当前目录切过去。没有用户文件时仍是默认的 `llm` 和全部 `shadow`。家目录里的文件则按其内容生效，项目文件改不了它。
- `SILLWARD_MODE_OVERLAY` 只改变带上它的那一次解析。用户文件内容不变，进程环境不被写入。下一次不带这个变量时，模式回到文件里的值。覆盖里的 `judge` 会让这次解析失败。
- `record_state` 为 true 时，参数和文件内容是 `[redacted]`，非敏感的 `step` 和 frame 摘要还在。
- tau home 里用 tau 自己的接口把默认模型存成 `gpt-4o`，并把密钥放进 `credentials.json`。录制的 Responses 响应被解析成裁决；请求打到 `https://api.openai.com/v1/responses`，模型 id 是存下来的那个。`OPENAI_API_KEY` 优先于凭证文件。chat completions 用另一份录制响应。5xx、超时和协议外正文都变成裁判失败，且异常文本不含密钥和文件内容。没有密钥，或默认 provider 是 Anthropic 时，不会发出请求。
- 配置为 `llm` 时，即使有 `TYPESAFE_API_KEY` 也仍是 llm。配置为 `jev` 但没有密钥时回到 llm。两者都有时是 jev。没有 base URL 的 jev 不发请求。
