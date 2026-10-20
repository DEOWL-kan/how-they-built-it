# CHANGELOG

更新方法见 README「更新」一节。只记对使用者有影响的变化；完整历史看 `git log`。

## 2026-10-20

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
