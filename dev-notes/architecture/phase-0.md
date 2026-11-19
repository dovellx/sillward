# 阶段 0：骨架

读者如果只看过实现计划，这一页说明阶段 0 落地了什么。

## 加了什么

一个 uv 项目。导入名、命令名和发行名都是 `sillward`，源码在 `src/sillward`，许可证是 MIT。Python 要求 3.12 及以上。依赖钉死为 `tau-ai==0.4.8`。

控制台脚本有两种用法：

- `sillward --version` 打印两行后退出：本包版本，以及当前安装的 `tau-ai` 版本。
- 其它参数会在进程内启动 tau。目录发现被关掉，唯一额外挂上的扩展文件是 `src/sillward/extension.py`。

`setup(tau)` 存在，函数体是空的。tau 加载扩展时会调用它，这一阶段不注册工具、命令或钩子。

## 为什么存在

实现计划把阶段 0 定为后面所有决策点的落点。判断核、记忆和角色都要挂在「tau 已经加载了本扩展」之后，所以先要有可安装的包、可执行的命令，以及 tau 认的 `setup` 入口。

版本行用来核对两件事：跑的是 sillward 自己的版本，以及进程里的 `tau-ai` 就是项目钉住的那一版。升级 `tau-ai` 在计划里是单独的变更，这一行让钉住的版本在命令行上可见。

## 落在哪个模块以及用了 tau 的哪条缝

| 模块 | 职责 |
| --- | --- |
| `src/sillward/cli.py` | 读 `--version`，或在进程内调用 tau 的命令入口 |
| `src/sillward/extension.py` | 提供 `setup(tau)` |

计划里的架构是：sillward 的命令启动 tau 的 `CodingSession`，带 `--no-extensions`，并且只加载本扩展，然后由 `setup` 接上。阶段 0 走的就是这条缝。脚本调用 `tau_coding.cli` 的入口，并传入：

- `--no-extensions`：不扫描 `~/.tau/extensions`，也不扫描项目里的 `.tau/extensions`
- `--extension`：指向本包的 `extension.py`

传给 `--extension` 的是 `extension.py` 这一个文件。同目录的 `cli.py` 因此不会被当成第二个扩展。

计划里那张钩子表（`input`、`tool_call`、`tool_result`、`send_user_message`、`append_entry`、`CodingSession.load`、`add_prompt_section`）这一阶段都还没有使用。那些钩子要等决策点接上之后才用。

tau 随包自带的内置扩展仍会加载。`--no-extensions` 在 tau 里关闭的是文件系统上的扩展发现；内置扩展不属于那次发现。用户在命令行上再写的 `-e`、`--extension` 或 `--project-extensions` 会被丢掉，避免在本扩展之外再挂一个显式扩展。`--print` 这类其余参数会原样交给 tau。

## 怎么测试

测试在 `tests/test_phase0.py`，不访问网络，也不读开发者自己的 `~/.sillward` 或 `~/.tau`。

- `sillward --version` 的两行分别等于 `pyproject.toml` 里的包版本和 `tau-ai==` 后面的钉。
- `from sillward.extension import setup` 之后，用一个不提供任何方法的对象调用 `setup`，返回值是 `None`。
- 临时目录里放好用户扩展和项目扩展，启动参数里再夹一个别人的 `-e`。这些参数交给 tau 的扩展加载器之后，加载到的文件只有本包的 `extension.py`。`--print` 和它后面的文本仍留在交给 tau 的参数里。
