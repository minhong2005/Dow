# 第三方组件

Dow 原创代码采用 MIT 许可证，全文见 [LICENSE](LICENSE)。

| 组件 | 来源与使用方式 |
| --- | --- |
| AppArmor 与 seccomp 策略 | `codex/apparmor` 与 `codex/seccomp.json` 使用了 Moby 的容器策略。Dow 为无特权的 Codex 沙箱调整了规则。上游采用 Apache-2.0，许可证随源码提供。[Moby profiles](https://github.com/moby/profiles) · [Apache-2.0](LICENSES/Apache-2.0.txt) |
| xterm.js 和两个插件 | 安装时通过 npm 锁文件下载。桌面构建将三个软件包的 MIT 许可证放入应用的 `Contents/Resources/vendor/`。[xterm.js](https://github.com/xtermjs/xterm.js) |
| Antigravity、Claude Code、Codex、sing-box | 由安装脚本从各自发行渠道下载，版本和校验值见 [assets.py](assets.py)。使用和分发须遵守各自条款。 |
| Google Chrome 与 macOS 界面符号 | 使用用户系统上的 Chrome 和 AppKit，工具入口的字母图标由 Dow 在本机生成。 |

项目名称和商标属于各自权利人。
