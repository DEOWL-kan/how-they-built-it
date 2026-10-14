# REA：可选的深挖工具

[REA](https://github.com/morluto/rea)（MIT，npm `rea-agents`）是一个面向 Agent 的逆向 MCP/CLI，
能做 JS 应用模块图、Dart AOT 字符串池、JADX 方法追踪和原生二进制反编译。
本技能**不依赖、不安装**它：核心脚本仍只用标准库。只有本技能的静态脚本到了上限，才换 REA 接着查。

**实测状态**（2026-10-14，`npx rea-agents@latest` = 6.5.0，未全局安装、未跑 `setup`）：

| 命令 | 结果 | 样本 |
|---|---|---|
| `analyze-javascript-application` + `trace-application-feature` | ✅ 能跑 | 自制 3 文件 H5 |
| `identify-flutter-build` / `inspect-dart-aot` | ✅ 能跑，依赖清单被截断（见下） | 自有 Flutter release APK |
| `search-android-classes` 等 Android 命令 | ⛔ 未验证：光有 `jadx` 不够，还要另外提供 `jadx-headless-mcp 0.7.1` JAR 并设 `REA_JADX_MCP_JAR` | — |
| `xrefs` / `trace --provider ghidra` | ⛔ 未验证：本机没有 Ghidra | — |

版本变化快，用之前先跑 `rea --help` 确认命令还在。

## 什么时候用

| 我方脚本的上限 | REA 能补什么 | 命令 |
|---|---|---|
| `feature_probe.py` 读到了 H5，但只给 host/关键词；从压缩后的 JS 长行里不抠路径（pitfalls 第 23、35 条） | 模块图、路由、API、字符串种子的可达追踪 | 先把 H5 目录解到**仓库外**的临时目录，`rea analyze-javascript-application <目录> --json > app.json`，再按其文档构造种子跑 `rea trace-application-feature` |
| Flutter 应用的 `report_sdk` 一项总是「none」（第三方包在 `libapp.so` 里） | Dart 快照里的 `package:` URI 清单，也就是依赖清单 | `rea inspect-dart-aot <含 lib/<abi>/libapp.so 的 APK>`；Flutter 一般在 ABI split 里，别只给 base |
| `code-tracing.md` 里手工跑 JADX | 封装好的类、方法搜索和引用追踪（未验证，需另配 JAR） | `rea inspect-android-package` / `search-android-classes` / `inspect-android-method` / `trace-android-references` |
| 「包里有 onnxruntime」以后，想知道业务代码是否调用它 | 原生库的 xrefs 和调用追踪（L4） | `rea xrefs` / `rea trace` 加 `--provider ghidra`，需要本机已有的 Ghidra |

## 实测出来的限制

- **H5：拼接出来的 URL 识别不了。** `fetch`、`EventSource`、`WebSocket` 的字面量地址都能精确识别
  （`mechanism: call:EventSource` 等），但 `'https://…/x?q=' + q` 这种拼接出来的，fetch 和 SSE 都不进端点列表。
  生产包里 base URL 加路径的写法很常见，所以「端点列表里没有」⛔ 不等于没有这个接口。
- **H5：目录里有 `package.json` 的 `main` 字段，入口会被标成 `electron-main`**（状态是 inferred）。
  普通 H5 也会这样，⛔ 不能拿它当 Electron 的证据。
- **Dart AOT：`package:` 清单按字母序只留前 500 条。** 自有 Flutter App 实测：`package_uri_count_at_least` 为 1000，
  只返回 500 条，已排序，止于 `package:flutter/…`，能看到 27 个不同的包。字母序排在后面的全部看不到：
  直接依赖 `flutter_localizations` 不在清单里，`feature_probe.py --keyword package:flutter_localizations/` 却在同一个 `libapp.so` 里查到 24 处。
  `inspect-analysis-view` 的分页也只在这 500 条里翻。
  ⛔ 清单里没出现某个包，只能记「未覆盖」；要查具体某个包，用 `feature_probe.py --keyword package:<包名>/`。
- **快照哈希**两条命令都给出了；REA 不负责把哈希对应到 Dart SDK 版本，需要外部数据集。

## 证据怎么记

- REA 的静态输出一律记 `[package]`。定位写 REA 的 `evidence_id`、`target.sha256` 和具体文件或方法。
  ⛔ 调用链、模块图都**不证明运行时走了这条路**，这一点和第 32 条一样：要有 `[device]` 才能升级。
- REA 结果里带 `coverage: "partial"` 时，报告里照写「部分覆盖」，⛔ 不能当完整清单。
- `inspect-dart-aot` 的 `package:` 清单是字节模式投影，不是反序列化结果，可能夹带误匹配的 token；
  依赖名要和别的线索（资源、host、dex 中的 SDK）交叉核对。
- 报告里写清 REA 版本，以及喂进去的是哪几个 APK 或目录。

## 边界

- ⛔ **不对竞品应用用 Frida**（`instrument-with-frida`、`start_frida_session`）：脚本会注入并运行在对方进程里，属于修改客户端，越过了 AGENTS.md 的边界。
- ⛔ Hopper、IDA、JEB 只用合法授权；`npx rea-agents setup` 会改 Agent 配置，可能提议安装 Hopper，必须由用户本人决定，Agent 不代跑。
- 设备、浏览器采集沿用本技能的授权规则：先说明会在对方 App 里产生什么数据，再动手。
- 解出来的 H5、反编译产物都是分析草稿，放仓库外；⛔ 不提交，也不搬进自己的产品。
- ⛔ 拿 REA 找到的接口去请求对方服务器，和 `feature_probe.py` 的 API 列表一样不允许。
