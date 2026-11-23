# Dow

Dow 是一款用于 Apple Silicon Mac 的多智能体工作台。它把 Claude Code、Codex 和 Antigravity 的终端会话放进同一个窗口，也让远端 Mac 上受管理的终端和 Google Chrome 通过你自己的美国家庭静态公网 IPv4 联网。

## 为什么做 Dow

假设你定居美国，人在家里远程操作一台留在海外的 Mac。你在屏幕上输入命令，但发起 API 请求、打开登录页的是远端 Mac。如果终端走家庭出口，浏览器却从设备所在地联网，同一个账号的登录和后续请求就可能呈现不同的网络位置。

Dow 面向这种人和设备分处两地的情况，包括设备留在某项服务尚未开放的地区、而服务条款允许该远程使用方式的情形。你需要在自己的美国住所准备可从远端访问的网络入口和固定公网 IPv4。配置后，可以在工作台分别核对终端与 Chrome 的实际出口；专用网关中断时，受隔离的终端请求会失败。这样可以减少因意外直连或出口切换而触发额外验证、账号限制或停用的风险。服务商仍会独立判断账号资格，固定 IP 无法保证账号免于限制。

## 谁可以使用这一部署方式

这里的“支持地区”按**实际访问的服务**分别判断。例如，在 Chrome 中打开 Claude，应核对 Claude 的地区政策；使用 Codex 时，应核对所用 OpenAI 产品及账号的适用条款。请同时确认服务允许远端设备所在的位置和你的访问方式。美国出口本身不能赋予访问资格。[Anthropic 支持地区政策](https://www.anthropic.com/legal/supported-regions) · [OpenAI API 支持地区](https://help.openai.com/en/articles/5347006-openai-api-supported-countries-and-territories)

- **个人使用**仅面向操作时实际身处美国、远程使用本人有权操作的 Mac，并连接本人美国家庭网络的用户。禁止实际身处该服务不支持地区的人员通过这一部署接入该服务。
- **雇主和组织使用**须同时核对注册地和总部所在地。雇主或所代表组织的注册地、总部所在地任一位于该服务不支持的地区，禁止代表该主体或为其业务使用这一部署，即使操作者本人身在美国。
- **所有权和控制关系**须追溯到间接持股。禁止由不支持地区主体直接或间接持股超过 50% 的组织使用这一部署，其人员也不得代为使用。服务商另有限制实际控制权等更严格条件时，同样适用；持股比例至多 50% 并不自动满足资格。

这些是项目对所述服务接入场景的使用约定。Dow 的出口检测只能验证公网 IP，人员所在地和组织资格需由使用者自行核实。源代码的复制、修改和分发等权利依照 [MIT 许可证](LICENSE)；第三方服务的访问资格由各服务商决定。

## 多智能体会话管理

Dow 按**项目和任务**组织会话。同一种工具可以开多份，不同项目也可以同时推进。比如修一个登录问题时，可以给 Claude Code 建一个“审查登录流程”会话，给 Codex 建一个“复现失败测试”会话，同时保留另一个项目里正在运行的 Codex。回到某个任务时，可以从会话列表按名字接回之前的终端。

平时用单个标签专注当前任务，用 **⌘P** 切换；需要对照两份输出时，选中相关会话并排查看。也可以为一个项目一次打开每种工具各一个会话。关闭标签只会离开当前视图，后台的 tmux 会话继续运行，稍后可以重新接入。

## 日常使用

- 在终端正文上滚动鼠标滚轮或用触控板双指上下滑动，可以查看该会话的输出。直接拖选终端文字，或双击选中一个词，再按 ⌘C。写入 Mac 剪贴板后会显示「已复制」。
- 用 **⌘N** 新建会话，给它选工具、项目和任务名。用 **⌘P** 在会话之间跳转。同一种工具可以同时开多份。
- **终端出口** 显示容器访问外部网站时观察到的 IP。点击 **浏览器出口** 会让受保护的 Chrome 打开临时检测页，分别请求 ipify、Cloudflare 和 AWS。界面会区分至少两个网站一致、只有一个返回、出口不符和结果过期。
- 可选的 Chrome 集成会打开独立的原生 Chrome 窗口，可以选择独立资料目录，也可以沿用日常登录资料。OAuth 链接也能从 CLI 送到这个窗口。
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
./browser/native/install-user --profile isolated
./browser/native/install-firewall
open ./browser/native/Dow-Chrome.mobileconfig
```

第一步使用独立资料目录，不继承日常 Chrome 的登录态。需要共享日常登录时可显式选择 `--profile shared`。升级时省略 `--profile` 会保留已有选择，早期安装继续使用共享模式。切换前请完全退出 Chrome。第二步需要在 macOS 的管理员弹窗里确认，第三步需要在系统设置的「设备管理」中确认描述文件。它会把当前 macOS 用户的 Chrome 代理指向 Dow。请通过 Dow 的「专用 Chrome」打开浏览器，再点「浏览器出口」。受保护进程使用单独的 macOS 组，PF 只允许它连接本机中转端口。浏览器资料可以分开，但描述文件的代理策略仍适用于该 macOS 用户的日常 Chrome，网关离线也会影响它上网。

防误开入口是可选项。正常退出 Chrome 后，`./browser/native/launch-lock lock` 会移动已安装的 Chrome 应用包并设置目录访问权限；可用 `./browser/native/launch-lock unlock` 还原。`./launch-lock-cli lock` 会为识别到的用户级 CLI 安装建立备份和启动提示，`./launch-lock-cli unlock` 还原。其他安装路径和其他用户需要单独管理。

要停用 Chrome 集成，先退出 Chrome，在系统设置的「设备管理」中移除 Dow 的描述文件，再运行 `./browser/native/uninstall`。它会还原 Chrome 启动入口，卸载 Dow 的中转和专用防火墙规则。

## 网络边界与验证

工具容器只有回环接口。终端请求通过一个 Unix 套接字进入网关；网关防火墙只允许代理进程连接配置中的第一个地址和端口。忽略代理的程序会连接失败。Codex 的命令在单独的无特权沙箱中执行，沿用工具容器的网络。Chrome 通过 Mac 上的本机中转连接同一个网关。

默认配置下，Codex 在工作区里运行的 Git 和 curl 可以通过 Dow 访问 `github.com`、`api.github.com` 和 `raw.githubusercontent.com`。可以让它直接克隆公开仓库或读 README。安装程序会保留已有的自定义网络策略。升级时，已经打开的 Codex 对话可以退出后用 `codex resume` 在 Dow 中接回，使新的域名规则生效。

网关会持续通过配置的代理向 ipify、Cloudflare、AWS 和 ifconfig.me 核验出口。至少两个站点返回预期 IP 且没有站点返回其他 IP 时才开始转发；检测失败、结果不符或验证超过 15 秒未刷新时，网关会拒绝新连接并关闭已有转发。固定出口依赖代理供应商。指示灯显示最近一次测量；如果供应商在同一服务器上轮换出口，已经运行的请求可能早于下一次检测。通过本机命令打开的其他 Mac 应用可能使用自己的网络。这里的控制范围是 Dow 管理的进程。

在已配置真实代理的空闲工作台上运行以下验收：

```sh
./verify                  # 终端 IPv4/IPv6、UDP、代理和网关中断、恢复
./codex/verify            # Codex 沙箱的项目权限和出口
./browser/native/verify   # 可选 Chrome 集成，真实浏览器发起请求
```

第一项会短暂停用专用网关，第三项会短暂停用 Chrome 中转，现有连接会受到影响。浏览器检测会联系上述三个外部网站。

### 升级网关与更换代理

先结束联网任务并退出 Dow 和受保护的 Chrome。仅升级旧网关时跳过 `./configure`，沿用现有配置。更换线路或轮换密码时，先列出本机、网关和自建中转里持有该凭据的配置，并确认有独立于这条线路的管理通道。若中转服务器也向上游认证，需同步它的出站凭据。下面的步骤只重建专用网关，保留项目和工具的登录卷。完成后分别验收终端与 Chrome 的出口和断网恢复；若日常网络也依赖此链路，另行验证其连通性。

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
