# 阶段 4：角色

读者如果只看过实现计划，这一页说明阶段 4 落地了什么。

## 加了什么

父 agent 多了工具 `agent`。参数是角色名、任务文本、路径前缀。一层委托，三个角色：

| 角色 | 工具 |
| --- | --- |
| implementer | `read`、`write`、`edit`、`bash` |
| reviewer | `read` |
| explorer | `read` |

子会话没有 `agent`。它不加载注册这个工具的 `extension.py`。reviewer 的任务文本由调用方放进 `task`，explorer 的目标也在 `task` 里。三个角色使用父会话当时的模型 id，thinking level 也从父会话带过去。

implementer 的 `write` 和 `edit` 在真正改文件之前检查路径。解析 `..` 之后不在前缀里，调用失败，文件的内容和修改时间都不变。这条检查不经过裁判。`bash` 不按前缀监禁。

拉起子 agent 进入 `tool.approval`。`active` 时用一般阈值，默认 `0.80`。没有界面时低于阈值就挡住，不问人。子会话自己的命令不再过一遍父会话的审批。

`swarm.routing` 的候选是三个角色。`shadow` 询问并写 ledger，动作是 `unchanged`，子会话仍是父 agent 指定的角色。`active` 时，只有答案的概率达到 `thresholds.routing_override`（默认 `0.80`）并且和指定角色不同，才改派；ledger 动作是 `execute`。刚好 `0.80` 改派，`0.79` 不改派。

`swarm.patch` 只看 implementer 的 `agent` 结果。候选是 `within` 和 `beyond`。`shadow` 询问并写 ledger，动作是 `unchanged`。不撤回已经写下的文件，也不替换交回父 agent 的文字。计划没有写 active 时怎样拒绝补丁，所以这一版 active 同样不改文件、不改结果。

选中的 lessons 用阶段 3 的同一个 recall 放进子会话的任务前面。子会话不写 lessons。

## 为什么存在

实现计划把阶段 4 定为一层角色，不是测评运行器。阶段 3 的「子轮」只是对同一个 recall 函数多传一个角色名，还没有子会话。

reviewer 和 explorer 不能写、不能跑命令、不能再委托。implementer 的写范围是路径前缀，不是再问一次裁判。`swarm.routing` 和 `swarm.patch` 的默认模式仍是 `shadow`，所以不改派、不拒绝补丁；第一次对照里委托会不会发生，仍看 `tool.approval` 那条 `0.80`。

## 落在哪个模块以及用了 tau 的哪条缝

| 模块 | 职责 |
| --- | --- |
| `roles/definitions.py` | 三个角色的工具名单和固定说明 |
| `roles/sandbox.py` | 按角色取工具，并在 `write` / `edit` 执行前检查前缀 |
| `roles/spawn.py` | 打开子会话，跑完后把最后一条助手文本交回父 agent |
| `roles/prompt.py` | 只给子会话加固定说明，不注册工具 |
| `teamwork/routing.py` | `swarm.routing` |
| `teamwork/patch.py` | `swarm.patch` |
| `extension.py` | 注册 `agent`，并在父会话的 `tool_result` 上记录 patch |
| `gates/approval.py` | 拉起子 agent 和 `bash`、项目外写入走同一个阈值 |

用到的 tau 缝：

| tau 的缝 | 本阶段用它做 |
| --- | --- |
| `CodingSession.load` | 子 agent。同一 provider、同一模型 id、同一 thinking level。子会话的记录在内存里 |
| `allowed_tool_names` | 子会话最终工具表只留该角色的名字，因此没有 `agent` |
| `add_prompt_section` | 角色的固定说明。挂在子会话加载的 `roles/prompt.py` 上，不挂在会注册 `agent` 的 `extension.py` 上。逐轮 lessons 仍走 recall，不进这段说明 |
| `tool_call` | `tool.approval` 可以挡住这次 `agent` 调用，子会话就不会打开 |
| `tool_result` | `swarm.patch` 读取 implementer 的 `agent` 结果。`shadow` 不改钩子返回值 |

前缀检查在包装过的 `write` / `edit` 里，先于 tau 自己的写文件。它不走裁判，也不走钩子。

子会话要复用父会话的 provider。扩展上下文公开了模型名和 thinking level，没有公开 provider 对象；绑定上的会话视图就是这个 `CodingSession`。没有改 tau 的源码。

## 怎么测试

测试在 `tests/test_phase4.py`。用脚本化的假 provider 驱动 tau 的 `CodingSession`，用假裁判注入答案。不访问网络，不读开发者自己的 `~/.sillward` 或 `~/.tau`。家目录、tau home 和项目都是临时目录。模式用 `SILLWARD_MODE_OVERLAY` 强制。子会话的工具和模型 id 取模型调用时实际收到的那一份。

- reviewer 和 explorer 的工具列表只有 `read`。固定说明出现在子会话的系统提示里。
- 三个角色的子会话都没有 `agent`。implementer 的工具是 `read`、`write`、`edit`、`bash`。
- 三个角色的模型 id 都是父会话的 `shared-model`。
- implementer 用 `src/../../elsewhere/...` 去 `write` 或 `edit` 前缀外的文件时，结果不是成功写入，目标文件不出现或字节和修改时间都不变。
- `swarm.routing` 为 `shadow`，裁判以 `0.95` 要求换成 implementer 时，子会话仍只有 `read`，指定的文件没有写出来。ledger 的答案是 implementer，动作是 `unchanged`，不含任务文本。
- 同一点为 `active` 时，概率 `0.80` 改派成 implementer，文件被写上；概率 `0.79` 仍是 reviewer，文件不出现。
- `swarm.patch` 为 `shadow`，裁判回答 `beyond` 时，前缀内的文件仍是 implementer 写下的内容，父 agent 仍看到子会话的结束语。ledger 有这条裁决，不含文件内容。
- `tool.approval` 为 `active` 且概率 `0.79` 时，没有界面也不会等待输入，`agent` 被挡住，子会话不启动。
- `memory.recall` 为 `active` 时，选中的 lesson 出现在子会话的任务前面，没选中的不出现。
