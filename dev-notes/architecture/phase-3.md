# 阶段 3：记忆

读者如果只看过实现计划，这一页说明阶段 3 落地了什么。

## 加了什么

项目级 lessons，文件是 `.sillward/lessons.jsonl`。一条至少包含 id、正文、来源（`user` 或 `agent`）、创建时间、捕获时的概率。两端空白去掉、连续空白收成一个空格之后正文相同，就不追加第二条；先写下的那条保持原文。两条正文不同，包括互相矛盾，都保留。没有 `memory.merge`。

`memory.capture`、`memory.recall`、`memory.worth` 接到了判断核上。默认仍是 `shadow`。

`memory.capture` 只看父会话里用户自己输入的消息。active 且裁判认为这是在纠正或立下规则时，追加一条来源为 `user` 的 lesson。`shadow` 询问并写 ledger，动作是 `unchanged`，不写文件。扩展自己送回的 follow-up 不触发捕获。

`memory.worth` 是父会话记下 agent 自己提出的 lesson 的入口。候选是可复用、一次性、已知（`reusable`、`once`、`known`）。active 时只保留可复用，来源是 `agent`。这一阶段不从模型输出里猜测 lesson，也没有单独的命令。

子 agent 的捕获和 worth 请求直接丢弃：不询问裁判，不写文件，ledger 动作是 `discard`。

`memory.recall` 对文件里的每一条各问一次。候选是这条 lesson 的 id 和 `skip`。active 时，选中的正文按文件顺序写进本轮文字前面。父轮和子轮调用同一个函数。`shadow` 不改文字；ledger 的答案是本会送入的 lesson id，动作是 `unchanged`。默认 ledger 行不含正文。

同一条用户消息先 recall 已经存在的 lessons，再捕获这条消息。刚写下的那条从下一条用户消息起才参与 recall。

Ledger 的 `action` 因此多了 `capture`、`recall`、`discard`。

## 为什么存在

实现计划把阶段 3 定为 lessons 的读写，而不是角色。阶段 2 的门不读、不写 lessons。父 agent 和三个角色都要 recall，只有父 agent 可写。全文去重和矛盾并存是存储规则，合并留给以后的 `memory.merge`。

默认模式仍是全部 `shadow`。shadow 必须能询问并记账，同时不改本轮文字、不写 lessons 文件。active 只来自用户配置或当次进程覆盖。

子 agent 还没有会话。捕获入口先拒绝非父写入，是为了把「只有父 agent 可写」钉在角色接上之前。子轮上下文用同一个 recall 函数，三个角色的工具和沙箱要到阶段 4。

## 落在哪个模块以及用了 tau 的哪条缝

| 模块 | 职责 |
| --- | --- |
| `memory/store.py` | 读写 `.sillward/lessons.jsonl`，按规范化正文去重 |
| `memory/capture.py` | `memory.capture` 与 `memory.worth`。决定哪一条写入 |
| `memory/recall.py` | `memory.recall`。决定哪一条进入本轮文字 |
| `extension.py` | 父会话的 `input` 上先 recall，再 capture |
| `kernel/runner.py` | `discard`：不询问裁判，写一行 `discard` |

用到的 tau 缝：

| tau 的缝 | 本阶段用它做 |
| --- | --- |
| `input` | `task.frame` 仍不改用户原文。`memory.recall` 为 active 时，钩子返回 `transform`，把选中的 lessons 写进本轮用户消息 |
| `add_prompt_section` | 没有用。逐轮 lessons 不放进随会话重建的固定说明 |

`CodingSession.load` 本阶段没有用。子轮不是子会话，而是 `recall.apply(..., role="child")`。`implementer`、`reviewer`、`explorer` 以后调用同一个函数，选择规则相同。钩子不直接请求裁判，只走 kernel。

## 怎么测试

测试在 `tests/test_phase3.py`。用脚本化的假 provider 驱动 tau 的 `CodingSession`，用假裁判注入答案。不访问网络，不读开发者自己的 `~/.sillward` 或 `~/.tau`。家目录、tau home 和项目都是临时目录。模式用 `SILLWARD_MODE_OVERLAY` 强制。

阶段 2 的假裁判现在会回答 `memory.capture`（`no`）、`memory.recall`（`skip`）和 `memory.worth`（`known`）。父输入开始询问捕获之后，那些测试仍不写 lessons。

- 裁判同意的用户纠正追加一条 lesson。来源是 `user`，概率是同意那个答案的概率，创建时间可以解析。ledger 动作是 `capture`。默认行不含那句正文。
- 第二条只有空白不同，规范化之后与第一条相同，文件仍是一行，留下的是第一条原文。
- 「要写回归测试」和「不要写回归测试」按顺序都保留。
- `memory.recall` 为 active 时，选中的正文出现在父会话的用户消息里，没选中的不出现，用户原文还在。子轮用同一个函数得到的文字也是这样。选中那一行的 ledger 动作是 `recall`，答案是 lesson id，不含正文。
- 同一点为 `shadow` 时，用户消息原文不变，ledger 里仍有本会送入的 lesson id。
- `memory.capture` 为 `shadow` 时，裁判同意也不写 lessons 文件，ledger 动作是 `unchanged`。
- 子 agent 的捕获不产生 lessons 文件，不询问裁判，ledger 动作是 `discard`，答案为空。
- agent 提出的三条里，只有可复用的那条被写入，来源是 `agent`。一次性的和已经知道的不写。子 agent 的 worth 也不写、也不询问。
