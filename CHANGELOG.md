# CHANGELOG

更新方法见 README「更新」一节。只记对使用者有影响的变化；完整历史看 `git log`。
标题里的版本号必须等于 `.claude-plugin/plugin.json` 的 `version`（回归测试会检查），插件靠它判断有没有更新。

## 1.2.0 · 2026-10-20

- **iOS 能读包了**（限 Apple Silicon Mac 上用户自装的 iPhone App）：新增 `scripts/ios_bundle.py`；`feature_probe.py` 接受 `.app` 目录，
  加密段置空后才读，永不读 `iTunesMetadata.plist`。见 `references/ios.md`。
- `feature_probe.py --section dart`：Flutter 的 Dart 包完整清单（Android `libapp.so` 与 iOS `App.framework/App`）。
- `walkthrough.py` 每一步可以用自己的录屏；`check_report.py --walkthrough steps.json` 核对每步挂的证据。
- `references/rea.md` 新增「配合 REA 有没有质的提升」；README 重写。

## 1.1.0 · 2026-10-20

- 可以作为 **Claude Code 插件**和 **Codex 插件**安装与更新（README「安装」「更新」）。两个平台共用 `.claude-plugin/plugin.json`。
- SKILL.md 写明脚本路径相对技能目录，不是用户的项目目录。

## 1.0.0 · 2026-10-20（插件化之前的基线）

- 「现状与不足」整理进 `ROADMAP.md` 顶部。
- `feature_probe.py` 读到 H5 或 Flutter 时，提示下一步可以怎么用 REA（`references/rea.md` 新增「怎么搭配」一节）。
- README 新增「更新」一节；新增本文件。

## 2026-10-15 · PR #10

- 功能调研报告**必须有操作路径**：1、2、3、4 编号步骤，每步截图 + 录屏片段。新增 `scripts/walkthrough.py`，
  生成 `walkthrough.html`（可播放片段）和 `walkthrough.md`。
- 撤回 #8 对 `frame_diff.py` 时间轴的改动：ffmpeg 自己的 `-ss`/`select`/`showinfo` 都从首帧算，旧版本来就对（pitfalls 第 36 条）。
- 截帧不要用 `ffmpeg -ss T -frames:v 1`：变帧率录屏会给出 T 之后的下一次变化。

## 2026-10-15 · PR #9

- 接入 [REA](https://github.com/morluto/rea) 作为可选深挖工具，附实测限制（`references/rea.md`）。
- `preflight.py --android-static` 检查 `rea`。

## 2026-10-14 · PR #8

- `feature_probe.py` 读随包 H5（`.js/.mjs/.html/.htm`）和 assets 里的离线包 zip，抬头报告读到的 H5 文件数。
- 文档补上：飞行模式不等于离线（pitfalls 第 37 条）、logcat 环形缓冲会覆盖（第 38 条）。
