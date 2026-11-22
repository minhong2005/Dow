# Dow

Dow 是 Apple Silicon Mac 上的 AI 会话工作台。你可以在一个窗口里打开多个 Antigravity、Codex 和 Claude Code 会话，按项目切换，需要对照时再并排。关掉标签页后，后台的 tmux 会话继续运行。

## 日常使用

- 用 **⌘N** 新建会话，给它选工具、项目和任务名。用 **⌘P** 在会话之间跳转。同一种工具可以同时开多份。
- **终端出口** 显示容器访问外部网站时观察到的 IP。点击 **浏览器出口** 会让受保护的 Chrome 打开临时检测页，分别请求 ipify 和 Cloudflare。界面会区分两个网站一致、只有一个返回、出口不符和结果过期。
- 可选的 Chrome 集成会打开独立的原生 Chrome 窗口，沿用当前用户的日常登录资料。OAuth 链接也能从 CLI 送到这个窗口。
- 工具默认在项目目录工作。启用本机集成后，它们可以申请在 Mac 上执行命令。Dow 会显示完整命令和工作目录，每次由你确认。

## 从源码安装

需要 Apple Silicon Mac、Xcode Command Line Tools、位于 `/opt/homebrew` 的 Homebrew，以及自己准备的固定出口 SOCKS5 代理。你需要知道**代理服务器的 IPv4、端口，以及网站最终应看到的 IPv4**。部分供应商提供的服务器地址与最终出口不同。

```sh
brew install colima docker node
git clone https://github.com/minhong2005/Dow.git
cd Dow
./configure
./deploy
./desktop/install
```

`./configure` 会询问地址和认证信息，密码输入时隐藏。如果 SOCKS5 需要再经过一个 HTTP CONNECT 代理，填写从 Colima 虚拟机可达的前置代理 IPv4 和端口；直接访问 SOCKS5 时该项留空。设置保存在本机 `~/.config/dow/network.json`，仅当前用户可读。

首次部署会建立单独的 Colima 环境 `dow`，下载并校验工具，挂载 `~/DowProjects` 到容器的 `/work`。首次安装前可通过 `DOW_PROJECTS` 指定其他项目目录。应用安装到 `~/Applications/Dow.app`。构建与下载使用宿主及构建虚拟机自己的网络；运行中的工具使用下述隔离网络。

## Chrome 与本机集成（按需启用）

需要标准位置的 Google Chrome（`/Applications/Google Chrome.app`）。先保存浏览器工作，再用 **⌘Q** 完全退出 Chrome。

```sh
./browser/native/install-user
./browser/native/install-firewall
open ./browser/native/Dow-Chrome.mobileconfig
```

第二步需要在 macOS 的管理员弹窗里确认，第三步需要在系统设置的「设备管理」中确认描述文件。它会把当前 macOS 用户的 Chrome 代理指向 Dow。请通过 Dow 的「专用 Chrome」打开浏览器，再点「浏览器出口」。受保护进程使用单独的 macOS 组，PF 只允许它连接本机中转端口。日常 Chrome 共用这份资料和代理设置，网关离线会影响它上网。

防误开入口是可选项。正常退出 Chrome 后，`./browser/native/launch-lock lock` 会移动已安装的 Chrome 应用包并设置目录访问权限；可用 `./browser/native/launch-lock unlock` 还原。`./launch-lock-cli lock` 会为识别到的用户级 CLI 安装建立备份和启动提示，`./launch-lock-cli unlock` 还原。其他安装路径和其他用户需要单独管理。

要停用 Chrome 集成，先退出 Chrome，在系统设置的「设备管理」中移除 Dow 的描述文件，再运行 `./browser/native/uninstall`。它会还原 Chrome 启动入口，卸载 Dow 的中转和专用防火墙规则。

## 网络边界与验证

工具容器只有回环接口。终端请求通过一个 Unix 套接字进入网关；网关防火墙只允许代理进程连接配置中的第一个地址和端口。忽略代理的程序会连接失败。Codex 的命令在单独的无特权沙箱中执行，沿用工具容器的网络。Chrome 通过 Mac 上的本机中转连接同一个网关。

固定出口依赖代理供应商。指示灯显示最近一次测量；如果供应商在同一服务器上轮换出口，已经运行的请求可能早于下一次检测。通过本机命令打开的其他 Mac 应用可能使用自己的网络。这里的控制范围是 Dow 管理的进程。

在已配置真实代理的空闲工作台上运行以下验收：

```sh
./verify                  # 终端 IPv4/IPv6、UDP、代理和网关中断、恢复
./codex/verify            # Codex 沙箱的项目权限和出口
./browser/native/verify   # 可选 Chrome 集成，真实浏览器发起请求
```

第一项会短暂停用专用网关，第三项会短暂停用 Chrome 中转，现有连接会受到影响。浏览器检测会联系上述两个外部网站。

### 更换代理

先结束正在进行的联网任务并退出 Dow 和受保护的 Chrome，再执行下面的命令。只重建专用网关，项目和工具的登录卷保留。

```sh
./configure
docker --context colima-dow rm -f aiwork-gateway
./deploy
```

如果已启用 Chrome 集成，请重启当前用户的中转，使其读取新的预期出口，然后重新打开 Dow。

```sh
launchctl kickstart -k "gui/$(id -u)/org.dow-workbench.chrome-relay"
```

## 参与开发

欢迎通过 GitHub Issues 提交复现步骤，或通过 Pull Request 提交改动。开发检查、仓库结构和测试范围见 [贡献指南](CONTRIBUTING.md)。Dow 原创代码采用 [MIT 许可证](LICENSE)，第三方组件见 [第三方说明](THIRD_PARTY.md)。
