# iOS：能做到哪一步

结论：**iOS 包仍然拿不到 IPA，但有一条合法的路能读到同一个包**——在 Apple Silicon Mac 上，
由用户自己从 Mac App Store 装上的 iPhone 版 App。资源、Info.plist、隐私清单、框架都是明文，
FairPlay 只加密主程序里声明的那一段。录屏和像素分析照旧可用。

实测环境：macOS 26.4 arm64、Xcode 26.6，两个 Mac 上已装的 iPhone App（一个原生、一个 Flutter），2026-10-20。
连接 iPhone 的部分**没有设备，未实测**，下表单独标出。

## 能用的路

| 方法 | 能得到什么 | 前提 | 状态 |
|---|---|---|---|
| **Mac 上的 iPhone 版 App**：`/Applications/X.app/Wrapper/Y.app` | 与 IPA 的 `Payload/Y.app` 同结构：资源、Assets.car、nib/storyboard、`.bundle`、Lottie、字体、音频、模型、H5、隐私清单、Frameworks | Apple Silicon Mac；用户自己用自己的账号装（免费 App）；开发者没关掉 Mac 上架（默认是开的） | ✅ 实测 |
| `scripts/ios_bundle.py` | 构成（按类型的数量与体积、最大文件）、Info.plist（URL scheme、会查询哪些 App、后台模式、ATS、权限说明）、隐私清单、`.bundle`/框架/扩展、每个 Mach-O 的加密范围、Assets.car 资源名数量与瘦身目标 | 标准库；Assets.car 需要 `xcrun assetutil` | ✅ 实测 |
| `scripts/feature_probe.py <X.app>` | 原生库、域名、关键词、H5、Flutter iOS 的 Dart 包（`App.framework/App`） | 同上 | ✅ 实测；**加密段被置空后才读** |
| REA `decode-interface-builder` | nib/storyboard 解成控件树：类名、文字、字体、颜色、约束、outlet 与 target-action 方法名 | REA（见 `rea.md`） | ✅ 实测：64 个文件、3027 个节点；默认 64 个文件封顶并标 truncated |
| QuickTime 经 USB 录 iPhone 屏幕 | 原分辨率录屏 → `frame_diff.py` | Mac + 数据线 + 设备上点「信任」 | ⛔ 未实测；**先用 `ffprobe` 看是不是变帧率** |
| iOS 自带录屏 | 设备上录 mp4 | 无 | ⛔ 未实测；有第三方文章说是变帧率，未核实 |
| Mac 上直接录这个 App | 冷启动入场动效 | App 装在 Mac 上 | ⚠️ 布局是 Mac 窗口里的，⛔ 不等于手机上的 |
| XCUITest / WebDriverAgent | 任意已装 App 的无障碍树（文字、类型、位置），也能点按 | 自己签名测试工程；免费 Apple ID 的描述文件 7 天过期；开发者模式 | ⛔ 未实测；驱动对方 App 会产生真实数据，⚠️ 与 Android 一样先征求同意 |
| `rvictl` + `tcpdump` | 整机抓包：域名、端口、时序、大小；TLS 内容仍是密文 | Mac + USB，用户自己的设备 | ⛔ 未实测 |

## 读出来的东西怎么用、哪里会错

- **瘦身目标因 App 而异**。商店给 Mac 的副本只瘦身成一种设备：实测一个是 iPad（`idiom 2`），一个是 iPhone（`idiom 1`）。
  `ios_bundle.py` 会打印「thinned for」；是 iPad 时，资源尺寸和布局⛔ 不能当 iPhone 版写进规格。
- **`Wrapper/` 本身不说明这是 iPhone App**。实测一个 Mac 应用的 `Wrapper/X.app` 里只有图标和 Info.plist；
  两个脚本都要求 Info.plist 指向一个 Mach-O 可执行文件，否则拒绝。
- **加密范围很小，但从不读**。实测主程序与扩展各加密 4096 字节，框架 `cryptid 0`。
  `feature_probe.py` 把加密段置零后再扫字符串；⛔ 不解密、不越狱、不碰 `SC_Info/`。
- **SDK 线索**：`.bundle` 名（常见 `*_Privacy.bundle`）、框架名、隐私清单。实测一个 Flutter App 的 iOS 包里，
  `.bundle` 直接给出了 Stripe、Sentry、Firebase Auth——而同一项目的 Android `libapp.so` 里 `flutter_stripe`、`sentry_flutter`、`firebase_auth` 一个都搜不到。
  两端互相补。⚠️ 名字只是线索，归属哪个功能是 `[inferred]`。
- **隐私清单是开发者的声明**（跟踪域名、收集的数据类型、要求说明理由的 API），⛔ 不是观测到的行为，记 `[package]`，结论不超过「声明了」。
- **Flutter iOS 的 Dart 包名可能几乎没有**：实测一个上架 iOS 版 `App.framework/App` 里只有 7 个 `package:` 名，
  同一项目本地构建的 Android `libapp.so` 有 138 个。很可能是发布构建做了混淆或拆分调试信息。少≠没有依赖，看 `.bundle` 补。
- **nib 只覆盖用 Interface Builder 做的界面**。SwiftUI、纯代码、Flutter 的界面没有 nib，REA 解不出来——这时只剩录屏与截图。

## 边界

- ⛔ 不替用户登录 App Store、不用 `ipatool` 一类工具代下载；用户自己装，我们只读。付费 App 不做。
- ⛔ **永远不读 `Wrapper/iTunesMetadata.plist`**：它可能含购买者账号信息。两个脚本只进入 `Wrapper/` 下的 `.app`。
- ⛔ 不越狱、不解密、不注入（不用 Frida）、不改包。
- ⚠️ 用户自装代理证书 + mitmproxy 能看到未做证书锁定的 App 的 HTTPS 明文；做了锁定的 App 只能改包才能看，那越界。
  **本技能目前不采用这条路**，要用须用户明确决定，并写进报告的覆盖范围。
- 读出来的资源、截图都是分析草稿，放仓库外，⛔ 不进我方产品。

## 推荐流程

1. 问用户：这个 App 能不能在 Mac 上装（Mac App Store 搜得到 iPhone 版）？能的话请用户自己装。
2. `python3 scripts/ios_bundle.py "/Applications/X.app"` —— 看构成、声明、SDK 线索、瘦身目标。
3. `python3 scripts/feature_probe.py "/Applications/X.app" --keyword <词干>` —— 原生库、域名、关键词、H5。
4. 界面是 nib 做的：`rea decode-interface-builder "/Applications/X.app/Wrapper/Y.app"`（见 `rea.md`）。
5. 动效：录屏（QuickTime 或设备录屏，先 `ffprobe`）→ `frame_diff.py`；颜色：截图 → `image_probe.py`。
6. 报告里写清：读的是 Mac 副本、瘦身目标、没覆盖什么（设备侧、加密段、运行时）。
