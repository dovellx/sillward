# 命令

`sillward` 在进程内启动 tau，并且只额外加载本扩展。

## `sillward --version`

打印两行后退出，不启动 tau：

```text
sillward <本包版本>
tau-ai <所钉的 tau-ai 版本>
```

第一行是已安装的 sillward 版本。第二行是已安装的 `tau-ai` 版本。项目把依赖钉为 `tau-ai==0.4.8`，所以第二行在按锁定安装时是 `0.4.8`。

## 启动

没有 `--version` 时，命令会启动 tau，并固定带上：

- `--no-extensions`：不从用户目录或项目目录发现扩展
- `--extension`：只指向本包的 `extension.py`

下面这些参数即使写在命令行上也不会再生效，避免再加载别的扩展：

- `-e`
- `--extension`
- `--extension=路径`
- `--project-extensions`

其余参数原样交给 tau。例如 `sillward --print "说明任务"` 就是让 tau 以 print 模式跑这条说明。`sillward --help` 显示的是 tau 的帮助。

tau 随安装自带的内置扩展仍会加载。`--no-extensions` 关掉的是目录发现，不是 tau 自己带的内置能力。

会话第一次处理用户消息、工具或回合结束时，本扩展读取 `~/.sillward/config.json`（没有文件就用默认值），并按当前进程的 `SILLWARD_MODE_OVERLAY` 叠上模式。裁决写到项目里的 `.sillward/ledger.jsonl`。

默认每个决策点都是 `shadow`：会询问并记账，但不挡住命令、不改写工具输出、不追加 follow-up、不写 lesson、不把 lesson 放进用户消息。把某个点设成 `active` 之后，下面这些行为才会出现。细节和阈值见 [配置](config.md)。

- `tool.approval`：`bash`，以及路径落到项目外的 `write` / `edit`。达到阈值才执行。交互界面里低于阈值会询问；`sillward --print`（`-p`）和没有界面的运行里低于阈值直接挡住，理由回到模型，不会等待输入。项目内的 `write` 和 `edit` 不经过这一点。
- `tool.constraint`：`bash`、`write`、`edit` 被判为违反已陈述的约束时挡住。
- `tool.injection`：从 `bash` 输出里去掉被判为给模型的指令段落。其它工具只在输出很长时才分段检查。
- `tool.admission`：很长的工具输出里，当前用不上的段落换成 `[omitted]`。
- `turn.continue`：回合停住、裁判认为该继续时，用 follow-up 送回。同一条用户消息最多两次。
- `turn.completion`：模型声称完成且这次用户消息里没有跑过 `pytest` 或 `ruff` 时，送回一次。
- `task.frame`：用户消息改变了任务时，把新的 frame 记在这次会话里。用户看到的那条消息原文不变。
- `memory.capture`：这条用户消息被判为纠正或规则时，往项目的 `.sillward/lessons.jsonl` 追加一条。两端空白去掉、连续空白收成一个空格之后正文相同，就不追加第二条。正文不同的两条都留着。扩展送回的 follow-up 不会被当成用户纠正。
- `memory.recall`：已有 lessons 里被选中的条目，写到模型看到的这条用户消息前面。没选中的不出现。
- `memory.worth`：父会话记下的、agent 自己提出的 lesson，只有被判为可复用才写入同一文件。没有单独的命令。

`shadow` 下这些都不会发生，ledger 里仍有对应的一行。会挡住工具的点如果裁判失败，即使当时是 `shadow` 也会挡住该次调用。子会话的捕获不写 lessons 文件。

## 配置

用户配置、进程内的模式覆盖、阈值和 ledger 字段见 [配置](config.md)。
