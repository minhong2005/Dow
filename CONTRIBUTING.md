# 参与 Dow 开发

如果想改善某个场景，可以先开一个 Issue，写下 macOS 版本、芯片、工具名称、实际表现和期望表现。报告网络问题时，请用文档示例地址替换真实 IP，删掉账号、代理密码、登录链接和会话内容。

## 源码入口

| 目录 | 职责 |
| --- | --- |
| `desktop/` | AppKit 窗口、会话视图、终端与浏览器的出口显示、本机命令确认 |
| `workspace/`、`codex/` | CLI 工具、tmux 会话、Codex 专用沙箱 |
| `gateway/`、`configure` | 网络配置、套接字中转、防火墙与上游代理 |
| `browser/native/` | Chrome 启动、出口检测、PF 规则、可选的启动锁 |

桌面改动可以在 Apple Silicon Mac 上单独构建。下面的命令不会启动应用、安装防火墙或注册系统服务。

```sh
./tests/check
out=$(mktemp -d)
./desktop/install --build-only "$out"
codesign --verify --strict "$out/Dow.app"
```

改变出口、沙箱或浏览器集成时，请在可中断的测试环境执行 README 中对应的验收命令。在 PR 里写明实际运行了哪些检查、使用的平台，以及需要评审者验证的部分。提交时只包含源代码，固定出口和代理凭据留在本机。
