# competitor-ui-teardown

**Reverse-engineer how a competitor's screen is actually built, and get specs you can hand to a designer.**

A [Claude Code](https://claude.com/claude-code) skill (also usable standalone — the scripts are plain Python).

[中文说明](#中文) ·  MIT

---

## The problem

You see a screen in a competitor's app that looks expensive, and you want to know why.

Looking at it gets you "it uses a big photo and soft light" — which nobody can build from. Worse, **it's
usually wrong**. A 2.7-second eased crossfade reads to the human eye as "changes every 2.5 seconds with a
0.6-second fade". That is a 4× error, and it went straight into a design document before anyone measured it.

This skill measures instead of guessing:

| Question | How it's answered |
|---|---|
| Is that background a video, a Lottie file, or five JPEGs? | Read the app package's asset inventory |
| How long is that transition, and what's its easing curve? | Per-frame difference over a screen recording |
| Does that gradient have grain, or can I just draw it in code? | Neighbour-pixel delta statistics |
| Where is the light actually centred? | Chroma peak on a downsampled grid |
| Will my text still pass contrast on it? | WCAG contrast against the measured extremes |

Anything that can't be measured gets labelled as inference. That distinction is the point.

## What's in here

```
SKILL.md                  The workflow (Claude Code skill; Chinese)
AGENTS.md                 Same workflow + boundaries for any agents.md-compatible agent
references/pitfalls.md    Fourteen ways this goes wrong, every one of them observed
references/web.md         Web products read their specs in the clear — a shorter route
references/android.md     Device + package commands
scripts/preflight.py      What's missing (ffmpeg / adb / device) before you start, with install hints
scripts/apk_assets.py     Asset inventory, grouped by screen
scripts/frame_diff.py     Motion rhythm from a screen recording
scripts/image_probe.py    Grain / gradient structure / colour / contrast
scripts/test_regressions.py  One sample per fixed bug — `python3 scripts/test_regressions.py`
evals/                    Trigger-accuracy eval sets (tuning + held out)
```

Requirements: Python 3.8+ (standard library only), `ffmpeg` and `ffprobe` on PATH,
plus `adb` if you're working with Android devices.

## Use it with your agent

```bash
git clone https://github.com/DEOWL-kan/competitor-ui-teardown.git
```

**Claude Code** — install it as a skill, then just ask for what you want:

```bash
ln -s "$PWD/competitor-ui-teardown" ~/.claude/skills/competitor-ui-teardown
```

> analyse how XYZ builds their login screen — ours feels cheap next to it

Triggering is measured, not hoped for: 20/20 on the tuning set in `evals/`, **7/8 on a
held-out set**. Phrasings like "their paywall looks expensive, ours doesn't" or "is that
background a video?" reach it; "design me a login page" deliberately does not.

**Codex, Cursor, Windsurf, Gemini CLI, opencode, Amp** and anything else that follows the
[agents.md](https://agents.md) convention — `AGENTS.md` at the repo root is the entry
point, including the authorization gates. Clone it next to your project, or point the
agent at the checkout:

> read AGENTS.md in ./competitor-ui-teardown, then tear down XYZ's onboarding

**Any other agent, or no agent at all** — the scripts are plain Python with `--help` on
each, and `SKILL.md` is the workflow in full. Nothing imports anything you do not
already have.

## Use the scripts directly

```bash
# What is this screen made of?
python3 scripts/apk_assets.py app.apk --screen login

# How does that animation actually behave?
python3 scripts/frame_diff.py recording.mp4 --suggest-crop   # where is the motion?
python3 scripts/frame_diff.py recording.mp4 --crop 1080x1200+0+200

# How was this background made, and is my text readable on it?
python3 scripts/image_probe.py bg.png --grid 9x20 --contrast '#1E2C28,#5C756F'
```

### A real result

`apk_assets.py`, pointed at one shipping app, replaced several rounds of guesswork with one line of output:

```
## login  (12 assets over 20 KB)
     311 KB  image      .../assets/images/login/bg_loop_2.jpg
     307 KB  image      .../assets/images/login/bg_loop_1.jpg
     ...
  => login is STATIC IMAGES (7 loop-ish files) — almost certainly a crossfade,
     not a video. Measure the rhythm with frame_diff.py.
```

That background had been assumed to be video. It is five JPEGs and a crossfade, totalling about 1.2 MB.
Knowing that changes the entire production plan.

## Boundaries

This is for understanding **mechanisms**, so you can build your own thing better. It is not for taking
anyone's work.

| | |
|---|---|
| Analysing timings, colours, structure, asset types | ✅ |
| Turning findings into your own specs and rebuilding | ✅ |
| Screenshots and frames for analysis and comparison | ✅ label them |
| **Downloading a free package from a public source** | ⚠️ **ask first**, then check its version |
| Signing into the user's store account to fetch one | ⛔ never |
| Paid apps, region-locked apps | ⛔ never |
| Shipping a competitor's assets in your product | ⛔ never |
| Committing competitor assets to your repo | ⛔ never |
| Bypassing paywalls, patching clients, circumventing DRM | ⛔ never — this reads publicly distributed packages only |

The skill asks for authorisation before downloading anything, and never drives an app-store account on the
user's behalf.

## Why the pitfalls file is the most useful part

Every entry in `references/pitfalls.md` is a mistake that actually happened — attributing an onboarding
video to a login screen (twice), estimating animation timing from 1 fps samples, inferring texture from
file size, painting a competitor's product details onto your own product's spec sheet.

Entries 9–14 came from turning the scripts loose on apps they had never seen, and five of the six are the
*scripts* being wrong rather than the analyst: a `ble` substring that matched `drawable`, a contrast check
that scored a headline against its own pixels, a "cycle length" reported for a sequence that never repeats.
Each one is now pinned by a sample in `scripts/test_regressions.py`. The scripts exist mostly to make these
specific mistakes harder to repeat; the tests exist to keep the scripts honest.

## Known limitations

Stated plainly, because a teardown tool that overstates its own reach is the worst kind:

**Android only, and iOS is an explicit non-goal.** There is no adb for iPhone, so the whole
device-to-package chain is missing and the asset inventory — the step that actually settles "is that
a video or five JPEGs" — cannot run at all. Plenty of design-leading apps are iOS-first; for those you
get motion analysis (`frame_diff.py`) and pixel analysis (`image_probe.py`) and nothing else. That is
a real ceiling, stated here rather than discovered halfway through a teardown.

**Distilled from two investigations.** A second teardown (2026-09-18) ran the workflow end to end on an
app it had never seen, and ran the scripts over five more shipping packages — Flutter and native, clean
naming and obfuscated. It found six bugs, all now fixed and pinned by `scripts/test_regressions.py`; the
worst was a `ble` substring that matched `drawable` and put 1976 of one app's 2886 assets in the
"device" bucket. See `references/pitfalls.md` 9–14. The screen buckets are still a keyword heuristic:
treat them as a lead, not an inventory, and grep the full paths with your own product's vocabulary.

**Subtle motion needs a higher analysis resolution.** `frame_diff.py` downsamples before differencing.
A 3px drift on a 1080px capture is 0.3px at the default resolution and gets smoothed away — the script
now warns and tells you to raise `--res`, but it cannot detect what it never resolved.

**Contrast wants a background, not a screenshot.** `image_probe.py --contrast` now takes the p10/p90 of
the sampled grid rather than the absolute extremes, because on a screenshot the darkest "background"
pixel is the body text itself — measured on a real sign-in screen it scored `#111111` text against
`#16181A` text and called it a fail. Percentiles fix that specific lie, not the general problem: feed it
the extracted background asset. And if any scrim or overlay sits between text and background, none of
these numbers are the shipping values — recompute on composited pixels.

**Split APKs aren't merged.** The script reads one package file. Measured across six shipping apps,
`base.apk` carried over 99% of the asset weight — the density and language splits held 0.1 MB of
leftovers — so pointing it at `base.apk` is normally the whole answer. `pm path` will still hand you
five or six lines to pick from.

**Trigger accuracy is measured, not assumed.** `evals/` holds 20 tuning queries and 8 held out.
The description scores 20/20 on the set it was tuned against and **7/8 on the held-out set** — believe
the second number. The one miss asks about implementation *source* ("did they draw that waveform or use
a library?") rather than visual mechanism.

**Getting from teardown to your own design is still on you.** The skill produces a specification of how
someone else did it. Deciding what of that applies to your product, brand and constraints — and what
should be deliberately different — is judgement the tooling does not supply.

## License

MIT — see [LICENSE](LICENSE).

---

<a name="中文"></a>

# 中文

**逆向拆解竞品某一屏是怎么做出来的，产出能直接照着做的规格。**

## 解决什么问题

看到竞品某屏很好看，想知道怎么做的。凭肉眼看只能得出「用了大图和柔和的光」这种没法落地的结论，
而且**大概率是错的** —— 一个 2.7 秒的缓动交叉溶解，肉眼会读成「每 2.5 秒换一张，淡化 0.6 秒」，
差了四倍多，而这个错误结论已经写进过交付文档。

本项目的做法是：**能测的一律测，不能测的标明是推断**。

两种入口：**拆这个**——你点名一屏，拿到它的规格；**帮我找**——你要做某一屏没思路，拿到一份对比简报
（web 与 App 混合的三个真实参考、各自机制、共同点与分歧点、结合你的约束该走哪条），选定后再深拆。

| 想知道 | 怎么测 |
|---|---|
| 背景是视频、Lottie 还是几张静态图 | 读安装包的资源清单 |
| 过渡多长、曲线什么形状 | 录屏逐帧差分 |
| 渐变有没有颗粒、能不能纯代码画 | 相邻像素差统计 |
| 光斑中心在哪 | 降采样网格上的色度峰值 |
| 文字放上去对比度够不够 | 对实测极值做 WCAG 计算 |

## 安装

```bash
git clone https://github.com/DEOWL-kan/competitor-ui-teardown.git
```

**Claude Code** —— 软链成 skill，然后直接说人话：

```bash
ln -s "$PWD/competitor-ui-teardown" ~/.claude/skills/competitor-ui-teardown
```

> 分析一下 XX 的登录页是怎么做的，我们的比它差太多

**Codex / Cursor / Windsurf / Gemini CLI / opencode** 等遵循 [agents.md](https://agents.md) 约定的 ——
根目录 `AGENTS.md` 就是入口（含授权边界），让 agent 读它即可：

> 读一下 ./competitor-ui-teardown/AGENTS.md，然后拆 XX 的引导页

**不用 agent** —— 三个脚本都是纯 Python 带 `--help`，完整流程见 `SKILL.md`。

⛔ **iOS 暂不支持**：iPhone 上没有 adb，拿不到安装包，资源清单这一步整条链路不成立。
录屏和截图分析仍然有效，但「用什么做的」量不了 —— 详见 `SKILL.md` 第 2 步上方。

## 边界

本项目用于理解**实现机制**，从而把自己的东西做得更好，**不是**用来拿别人的成果。

取安装包分三档：设备上已装的直接 `adb pull`（首选，零下载）；没装的**经授权后可以从公开渠道
下载免费安装包**，下完必须核 versionCode；⛔ 绝不替用户登录商店账号、绝不下付费应用或绕地区限制。
⛔ 不提取竞品素材用于我方产品，也不把竞品素材提交进仓库。

## 最有价值的部分是踩坑清单

`references/pitfalls.md` 里的每一条都是真实发生过的错误 ——
把引导页的视频当成登录页方案（犯了两次）、从 1fps 抽帧估读动效节奏、
用文件体积推断有没有颗粒、把竞品的产品特征画进自家的规格表。
那三个脚本存在的主要意义，就是让这些具体的错误更难重犯。

## 许可

MIT
