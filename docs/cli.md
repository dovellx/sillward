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

本扩展的 `setup` 目前是空的。tau 启动时会调用它，它不注册工具、命令或钩子。

## 配置

用户配置、进程内的模式覆盖和 ledger 字段见 [配置](config.md)。

`sillward` 启动 tau 时还不会读取 `~/.sillward/config.json`，也不会写 `.sillward/ledger.jsonl`。改这些文件不会改变这一次命令的工具行为。
