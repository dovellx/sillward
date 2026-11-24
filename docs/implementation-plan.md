# sillward 实现计划

sillward 是跑在仓库内 [huggingface/tau](https://github.com/huggingface/tau) 之上的编程 agent。tau 的源码在 `tau/`，基线是上游 `v0.4.8`。sillward 以扩展加载它，并提供判断核、跨会话 lessons、一层角色委托，以及与未改过的 tau 的对照测评。

本文是实现合同。阶段顺序、模块边界、配置位置和测试门禁以本文为准。

## 1. 已锁定的决定

| 项 | 决定 |
| --- | --- |
| 产品 | 可运行的编程 agent，不是评测平台，也不是给别人组合 agent 的框架 |
| 与 tau 的关系 | 源码在 `tau/`，基线 `v0.4.8`，以路径依赖安装，并以扩展加载。界面和 harness 的改动落在这份目录里 |
| 运行时 | Python 3.12+，uv，进程内调用 tau |
| 许可证 | MIT |
| 名字 | 导入、命令、发行名都是 `sillward`。源码在 `src/sillward`。用户配置是项目里的 `.sillward/config.json`。项目状态也在 `.sillward/` |
| 角色 | 父 agent 对用户说话。一层委托：implementer、reviewer、explorer。同一模型，同一 thinking level |
| 判断点 | 12 个，默认 `shadow`。第一次夜间对照前只有 `tool.approval` 与 `turn.continue` 为 `active` |
| 裁判 | choice / score，带概率。适配器 `llm` 与 `jev`。测试用假裁判 |
| 失败 | 会挡住工具的点失败即挡住。其余点失败则当作该点关闭 |
| 询问 | 交互式低于阈值时问人。非交互低于阈值时挡住，理由交回模型 |
| 配置权 | 模式、阈值和 `llm` 的 API key 来自项目里的 `.sillward/config.json`。`jev` 的密钥来自环境变量 `TYPESAFE_API_KEY`。评测只在当次进程覆盖模式 |
| 记忆 | 项目级 lessons。父 agent 与三个角色都 recall。只有父 agent 可写。全文去重。矛盾并存 |
| ledger | 只记裁决与动作。默认不记原文、参数和文件内容 |
| 测评 | 提交时跑假模型测试。夜间在 Linux 上做 tau 与 sillward 的对照。v1 的赢是报告稳定，且 sillward 通过率不低于 stock tau |
| 以后再做 | hive、判断式压缩、生成中改写输出、检查点回溯、`memory.merge`、mude、Terminal-Bench 门禁 |

## 2. 架构

```text
sillward CLI
    └── tau CodingSession（--no-extensions，只加载本扩展）
            └── setup(sillward)
                    ├── kernel      问裁判、应用模式、写 ledger
                    ├── gates       父循环上的决策点
                    ├── memory      lessons 的读写
                    └── roles       子会话、工具边界、路由与补丁检查
```

tau 已有的缝，以及每个缝承担的决策点：

| tau 的缝 | sillward 用它做 |
| --- | --- |
| `input` 钩子 | `task.frame`；把 recall 到的 lessons 写进本轮用户消息 |
| `tool_call` 钩子 | `tool.approval`、`tool.constraint`；挡住或放行 |
| `tool_result` 钩子 | `tool.admission`、`tool.injection`；`swarm.patch` 读取 implementer 的结果 |
| `send_user_message(deliver_as="follow_up")` | `turn.continue`、`turn.completion` |
| `append_entry` | 会话内的 task frame |
| `CodingSession.load` | 子 agent |
| `add_prompt_section` | 角色的固定说明。随会话重建，不承担逐轮 lessons |

压缩历史、改写已经写入记录的消息、在生成过程中改输出，都在 harness 内部。v1 不碰。

父 agent 使用 tau 的 `read`、`write`、`edit`、`bash`。子 agent 的工具集由 sillward 在创建子会话时限定。

## 3. 仓库布局

当前工作目录名是 `lambda`。实现开始时把仓库目录改成 `sillward`。包名不跟随旧目录名。

```text
tau/                       # huggingface/tau v0.4.8，路径依赖
src/sillward/
  __init__.py
  cli.py                 # 启动 tau，只加载本扩展
  extension.py           # setup(tau)
  config.py              # 读取项目 .sillward/config.json 与进程覆盖
  protocol.py            # 请求与裁决
  judges/
    base.py
    fake.py
    llm.py
    jev.py
  kernel/
    points.py            # 12 个点的声明
    runner.py            # 询问、模式、失败策略
    ledger.py
  gates/
    frame.py
    approval.py
    constraint.py
    injection.py
    admission.py
    turn.py
  memory/
    store.py
    recall.py
    capture.py
  roles/
    definitions.py
    spawn.py
    sandbox.py
  teamwork/
    routing.py
    patch.py
eval/
  config.toml            # 钉死的 model id、回合数、时限
  tasks/                 # 每题一个目录
  run.py
  report.py
tests/
docs/implementation-plan.md
pyproject.toml
LICENSE
```

`pyproject.toml` 的项目名、import 包和脚本入口都是 `sillward`。`tau-ai` 来自 `tau/`，版本与该目录里的 `pyproject.toml` 一致，当前是 `0.4.8`。开发依赖是 pytest 与 ruff。

`.gitignore` 忽略 `.sillward/` 里的 lessons、ledger 和含有 `llm_api_key` 的 `config.json`。例外是 `.sillward/config-template.json`，这份模板入库，改名为 `config.json` 后才会被读取。

## 4. 模块边界

每个模块只做一件事。决策点的「问什么」和「裁决如何改变行为」分开。

- `protocol` 定义请求和裁决，不知道 tau。
- `judges` 把请求变成裁决，不知道钩子。
- `kernel.runner` 查模式、调用裁判、执行失败策略、写 ledger。门和角色都通过它。
- `gates` 与 `teamwork` 把 tau 的事件翻译成请求，再按裁决调用 tau 的钩子返回值。
- `memory.store` 读写 JSONL。`recall` 与 `capture` 决定哪一条 lesson 进入请求。
- `roles.sandbox` 在子会话创建时去掉不允许的工具，并在 `write` / `edit` 执行前检查路径前缀。这条路径检查不经过裁判。
- `cli` 只负责把扩展挂到 tau 上。评测运行器调用同一入口，并传入进程级模式覆盖。

禁止：

- 在钩子里直接请求 Jev 或主模型。一律走 `kernel.runner`。
- 让 `.sillward/config.json` 以外的项目文件修改端点、密钥、模式或阈值。
- 让子 agent 获得 `agent` 工具，从而再委派一层。

## 5. 裁判协议

一次请求包含：决策点 id、问题类型（`choice` 或 `score`）、候选答案、任务 frame 的摘要、以及该点需要的最少状态。裁决包含：答案、每个候选的概率、耗时、裁判适配器名。概率缺失或答案不在候选里，视为失败。

适配器：

| 适配器 | 行为 |
| --- | --- |
| `fake` | 测试注入答案。不访问网络 |
| `llm` | 使用 tau 已经登录的模型，要求返回协议 JSON |
| `jev` | TypeSafe System One。模型 id 放在用户配置里 |

没有 Jev 密钥时用 `llm`。有密钥时用 `jev`。选择写在项目的 `.sillward/config.json`。

失败策略：

| 点的类别 | 裁判超时、5xx 或协议外内容 |
| --- | --- |
| 会挡住工具的点：`tool.approval`、`tool.constraint` | 挡住该次调用，理由交回模型，并记入 ledger |
| 其余点 | 当作该点为 `off`，行为与未安装判断核相同，失败记入 ledger |

`tool.approval` 在 `active` 时的阈值：

- 任务需要的概率至少 `0.80` 才执行。
- 项目内的测试、构建、lint 至少 `0.70` 才执行。
- 交互式会话里，低于阈值时问人。拒绝则挡住。
- 非交互会话（`sillward -p` 与夜间评测）里，低于阈值时挡住，理由交回模型。没有人可问。

阈值放在用户配置里，默认值如上。

`tool.approval` 的对象是 `bash`、项目外的改动、外部动作，以及拉起子 agent。项目内的 `write` 和 `edit` 不经过它。implementer 的路径前缀由沙箱检查。

## 6. 决策点

模式为 `active`（裁决生效）、`shadow`（询问并记 ledger，不改变行为）、`off`（不询问）。

| 点 | v1 初始模式 | 生效时做什么 |
| --- | --- | --- |
| `task.frame` | shadow | 判断这条用户消息是新任务、硬约束、纠正、子目标，还是无变化。只有变化才重写会话内的 frame |
| `tool.approval` | 第一次对照前改为 active | 按第 5 节的阈值放行、询问或挡住 |
| `tool.constraint` | shadow | 写操作或命令是否违反用户已经陈述的约束。违反则挡住 |
| `tool.injection` | shadow | 分段检查 `bash` 输出和过长的工具结果里是否有指向模型的指令。有则不把该段交给模型 |
| `tool.admission` | shadow | 长输出按块判断是否现在有用。有用的进入上下文，其余换成指针 |
| `turn.continue` | 第一次对照前改为 active | 回合停在「下一步再跑」或停在已经授权的工作上时，用 follow-up 送回去。每个用户消息最多两次 |
| `turn.completion` | shadow | 模型声称完成时，若没有验证动作，送回一次 |
| `memory.recall` | shadow | 哪些 lessons 适用于本任务。选中的进入本轮 |
| `memory.capture` | shadow | 用户消息是否在纠正 agent 或立下规则。是则写成 lesson |
| `memory.worth` | shadow | agent 自己提出的 lesson 是可复用、一次性，还是已知。只保留可复用 |
| `swarm.routing` | shadow | 三个角色里哪一个适合这次委托。active 以后，仅当概率至少 0.80 且与父 agent 指定的角色不同，才改派 |
| `swarm.patch` | shadow | implementer 的改动是否越出任务、路径和行数范围 |

从 shadow 拨到 active 只能由人修改项目里的 `.sillward/config.json`。不根据 ledger 自动拨动。第一次夜间对照使用的进程覆盖只打开 `tool.approval` 和 `turn.continue`，不写回用户配置。

`task.frame` 存在会话的 `append_entry` 里，随会话结束而结束。它不是 lesson。

## 7. 角色与沙箱

父 agent 通过工具 `agent` 委托。参数是角色名、任务文本、路径前缀。reviewer 的任务文本里包含要看的 diff。explorer 的任务文本里包含要找的目标。

| 角色 | 工具 | 其他限制 |
| --- | --- | --- |
| implementer | `read`、`write`、`edit`、`bash` | `write` 和 `edit` 的路径必须落在前缀内。`bash` 不做路径监禁，由 `tool.approval` 在 active 时把关 |
| reviewer | `read` | 无 `bash`，无写工具，无 `agent` |
| explorer | `read` | 同上 |

子会话使用与父 agent 相同的模型和 thinking level。子会话不加载会再次提供 `agent` 工具的路径。

`swarm.routing` 与 `swarm.patch` 在 v1 保持 shadow，因此第一次能力对照里，委托行为的变化只来自 `tool.approval`：拉起子 agent 也要过 0.80 那条线，否则在非交互运行里被挡住。这是预期结果，写进夜间报告的说明。

## 8. 记忆与 ledger

Lessons 文件：`.sillward/lessons.jsonl`。

一条 lesson 至少包含：id、正文、来源（`user` 或 `agent`）、创建时间、捕获时的概率。正文在空白规范化之后完全相同，则不追加第二条。互相矛盾的两条都保留。v1 没有 `memory.merge`。

写入：

- 用户的纠正或规则走 `memory.capture`，只由父 agent 的输入触发。
- agent 提出的 lesson 走 `memory.worth`。
- 子 agent 的捕获请求直接丢弃，不询问裁判。

读取：recall 的结果进入父 agent 和三个角色的当轮上下文。shadow 时只把「本会送入的条目」记入 ledger，不改上下文。

Ledger 文件：`.sillward/ledger.jsonl`。

默认字段：决策点、答案、各候选概率、耗时、模式、适配器名、随后动作（执行、挡住、送回、recall、捕获、丢弃）。不写用户原文、工具参数、文件内容。`record_state` 默认 `false`。设为 `true` 时才追加经脱敏的请求，供本地调试。

## 9. 配置

项目里的 `.sillward/config.json` 由用户拥有。家目录不参与解析。字段：

- `judge`：`llm` 或 `jev`
- `llm_api_key`：`llm` 裁判的 API key。没写时为空
- `jev_model`：System One 的模型 id
- `modes`：每个决策点的 `active`、`shadow` 或 `off`，外加 `default`
- `thresholds`：`approval` 默认 `0.80`，`approval_check` 默认 `0.70`，`routing_override` 默认 `0.80`
- `record_state`：默认 `false`

`llm` 的密钥写在项目 `.sillward/config.json` 的 `llm_api_key`。这份文件被 git 忽略。`jev` 的密钥只从环境变量 `TYPESAFE_API_KEY` 读取。密钥不写入 ledger。

进程覆盖：环境变量 `SILLWARD_MODE_OVERLAY`，值是 JSON 对象，只含点名到模式的映射。评测运行器在子进程里设置它。sillward 启动时把它叠在项目配置之上，进程结束即消失。除了 `.sillward/config.json`，项目里的其它文件不能提供 `judge` 或模式。

缺少项目配置时，使用本文的默认值：全部 shadow，阈值如上，裁判为 `llm`。

## 10. 测评

两层，目的不同。

**提交门禁。** GitHub Actions 或本地 `pytest`。假模型和假裁判。不断言模型是否更会写代码。断言钩子、ledger 字段、拦截、送回次数、沙箱、记忆去重、配置不被项目文件改写。Windows 与 Linux 都要能跑这层，因为这层不依赖 bash 语义。

**夜间对照。** Linux（GitHub-hosted ubuntu，或文档化的本地容器）。两臂：

- stock tau：`--no-extensions`
- sillward：只加载本扩展，`SILLWARD_MODE_OVERLAY` 把 `tool.approval` 和 `turn.continue` 设为 `active`，其余保持 `shadow`

两臂使用 `eval/config.toml` 里钉死的同一个 provider 和 model id。密钥来自环境变量。每题默认 30 个回合、10 分钟，写在题目清单里，两臂相同。

题目大约 8 到 12 道，语言是 Python，评分是 pytest。需要 shell 的步骤按 POSIX 写。每题是一个目录：说明、起始代码、隐藏测试、评分命令。评分只看测试是否通过，不让裁判给分。

报告至少包含：日期、tau 版本钉、sillward 版本、model id、实际模式覆盖、每题两臂的通过与否、回合数、通过率。报告可以入库到 `eval/reports/`，但不得包含密钥或完整对话。

v1 的产品接受线：连续的夜间运行能产出这份报告，并且 sillward 的通过率不低于 stock tau。一次低于，不把构建判失败；把结果记入报告，再决定是否把下一个 shadow 点拨到 active。统计显著性和 Terminal-Bench 2.1 不作为 v1 门禁。

## 11. 阶段

每个阶段结束时，该阶段的测试在无网络的情况下变绿，ruff 干净。后一阶段不开始把前一阶段的失败留在主分支上。

### 阶段 0：骨架

建立 uv 项目、MIT 许可证、`src/sillward`、控制台脚本、空的 `setup`。脚本启动 tau 且只加载本扩展。

退出标准：`sillward --version` 打印自身版本和所钉的 `tau-ai` 版本。`pytest` 能导入 `setup`。

### 阶段 1：协议、配置、ledger

实现请求、裁决、假裁判、用户配置加载、进程覆盖、ledger 写入。

退出标准：

- 假裁判的答案出现在 ledger 里。
- ledger 默认行不含原文、参数、文件内容。
- 会挡住工具的点在裁判失败时得到「挡住」。
- 其余点在裁判失败时得到「当作 off」。
- 家目录，以及项目里 `.sillward/config.json` 以外的文件，不能改变 `judge` 或 `modes`。项目的 `.sillward/config.json` 可以。
- `SILLWARD_MODE_OVERLAY` 只影响当前进程的解析结果。

`llm` 与 `jev` 适配器在本阶段可以只有接口和错误路径。真正的 HTTP 调用放到阶段 1 的末尾，并用录制的响应做测试，不在单测里打外网。

### 阶段 2：父循环上的门

接上 `task.frame`、`tool.approval`、`tool.constraint`、`tool.injection`、`tool.admission`、`turn.continue`、`turn.completion`。

退出标准：用假 provider 和假裁判。

- `tool.approval` 为 active 时，低于阈值的 `bash` 被挡住，高于阈值的执行。
- 同一点为 shadow 时，调用照常执行，ledger 仍有记录。
- 非交互模式下，低于阈值不会等待输入。
- `turn.continue` 对同一条用户消息最多发出两次 follow-up。
- `tool.admission` 与 `tool.injection` 在测试里被强制为 active 时，分别替换长输出和去掉被标记的段落。shadow 时原文不变。

### 阶段 3：记忆

实现 lessons 存储、recall、capture、worth、全文去重。

退出标准：

- 裁判同意的用户纠正追加一条 lesson。
- 相同正文不产生第二条。
- 两条不同正文都保留。
- active 的 recall 把选中条目放进父轮与子轮的上下文夹具。
- 子 agent 的捕获不写文件。

### 阶段 4：角色

实现 `agent` 工具、三种定义、沙箱、`swarm.routing`、`swarm.patch`。

退出标准：

- reviewer 与 explorer 的工具列表只有 `read`。
- implementer 在前缀外的 `write` 或 `edit` 失败，且失败发生在文件变更之前。
- 子会话没有 `agent` 工具。
- routing 与 patch 在 shadow 下不改变父 agent 指定的角色，也不拒绝补丁，ledger 有记录。
- 三个角色的夹具使用同一个模型 id。

### 阶段 5：测评运行器

实现题目目录约定、两臂运行器、报告。先用假模型把运行器和报告跑通，再放入真实题目。

退出标准：

- 假模型下两臂都能产出一份报告，字段齐全。
- 至少一道 Python 题可以由 pytest 判为通过或失败。
- 夜间工作流或容器入口有文档，指定 Linux，并设置 `SILLWARD_MODE_OVERLAY`。
- 报告样例不含密钥。

### 阶段 6：第一次真实对照

在 Linux 上用钉死的模型跑完全部题目。不把「通过率已经不低于 tau」写成合并代码的条件。把它写成产品接受记录：报告归档，数字写在记录里。若低于 tau，下一轮工作是阅读 ledger，决定是否把某一个 shadow 点拨到 active，然后重跑夜间。一次只拨一个点。

## 12. 工程约束

- 公共函数和协议对象带类型注解。
- 决策点的声明集中在 `kernel/points.py`。新增一个点要同时补上模式默认值、失败类别和一条测试。
- 单测不访问网络，不读取开发者自己的 `~/.sillward/config.json` 或本仓库的 `.sillward/config.json`。测试用临时目录。
- 密钥不出现在仓库、ledger 默认行和评测报告里。
- `tau/` 的基线钉死。升级单独做一个变更：用 subtree 合并上游标签，并重跑阶段 2 到阶段 4 的假 provider 测试。夜间对照的 stock 那一臂使用未改过的 `tau-ai==0.4.8`。
- 格式与静态检查用 ruff。提交门禁包含 pytest 与 ruff。
- 扩展的对外行为通过 tau 的钩子返回值表达，不依赖 TUI 控件。

## 13. 风险

| 风险 | 处理 |
| --- | --- |
| tau 扩展 API 变化 | 钉版本。钩子测试失败时先看钉，而不是放宽断言 |
| 夜间里子 agent 经常被 `tool.approval` 挡住 | 这是非交互阈值的结果。报告里单列「委托被挡」的次数，避免把它读成角色实现损坏 |
| 开发机是 Windows，题目假设 POSIX shell | 提交门禁不跑这些题目。夜间只在 Linux 跑 |
| Jev 不可用 | `llm` 适配器是开发与 CI 的默认路径。对照报告写明实际适配器 |
| lessons 含有项目私有规则 | 文件位于 `.sillward/` 且被 gitignore。测试用临时仓库 |
| 发行名以后被 PyPI 占用 | 2026-11-19 检查时 `sillward` 为 404。发布前再查一次。导入名保持 `sillward` |

## 14. 以后再做

这些不进入上述阶段：

- hive 的共享板，以及 `hive.publish`、`hive.deliver`、`hive.relate`
- 用裁判做压缩，或把过期工具结果改成墓碑
- 生成过程中改写输出
- 回到检查点
- `memory.merge`
- 本地裁判 mude
- 允许仓库覆盖裁判配置
- 以 Terminal-Bench 2.1 作为合并门禁
- 子 agent 再委派，或给 reviewer / explorer 开放 `bash`
