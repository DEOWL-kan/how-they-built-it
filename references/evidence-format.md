# 证据记录 v1

标准库校验器：`python3 scripts/check_report.py <report.json>`。退出 0 为结构有效，1 为输入/引用/已声明证据边界错误；不修改文件。
完整可执行例子：[report.json](../examples/research/report.json)。它使用自制文档，不是真实竞品研究。

顶层六个字段：`schema_version: 1`、`context` 对象、`questions`、`evidence`、`claims`、`coverage` 四个数组。
每份记录最多 8 MiB；大体积事件和正文放仓库外附件，报告仅引用定位。拒绝重复 JSON 键、NaN/Infinity 和未知 schema 版本。

## context

必需字符串：product、platform、version（未知写 unknown）、goal、observed_at（含时区的 ISO 8601）、target_depth、achieved_depth。
深度取 L1…L5；gaps 为字符串数组。实际深度低于目标时必须解释缺口；校验器不自动评分深度。

## questions

每项：唯一非空 id、text、critical（布尔）。至少一个问题。每个关键问题必须有 claim；未覆盖也写 SKIP。

## evidence

每项包含 id、source、kind、locator、observation、observed_at、conditions 和 limitations。
conditions/limitations 是字符串数组，允许为空但不能省略；observation 只写这份来源直接承载的内容。

| source | 标签 | 支持范围 |
|---|---|---|
| device | 真机 | 设备上实际观察 |
| browser | 浏览器 | 页面/网络观察或已读取的公开代码，kind 分开 |
| package | 包内 | 已分析文件内存在的内容 |
| public-source | 公开资料 | 官方作出的声明 |
| user-report | 用户说法 | 该用户的陈述，未必能复现 |

kind 取 observation、network、code、document。
locator 必需 target（URL/文件/录制定位）和 position（行/方法/时间/章节）。公开资料与用户说法另需 published_at（未知写 unknown），用户说法另需 author。
network 仅允许 browser source，locator 另需 session_id、request_id、body_status；body_status 取 captured、empty、not_requested、unavailable、truncated。
时间、页面、frame、action、redirect_hop、附件哈希可补在 locator 中；指定 --capture 时按下方规则交叉检查附件；不指定时仅检查报告本身。
不要将 Cookie、令牌、原始个人输入或完整业务端点清单放进公开记录。

## claims

每项必需：id、question_id、text、scope、basis、status、evidence_ids、rationale、alternatives。
evidence_ids/alternatives 是数组；rationale 说明依据或未覆盖原因。

| 字段 | 允许值 | 含义 |
|---|---|---|
| scope | runtime / static / statement / design | 运行行为、静态内容、来源声明、我方方案 |
| basis | direct / inferred / proposal | 直接证据、推断、我方建议 |
| status | PASS / PLAUSIBLE / SKIP | 本命题已验证、推断/建议、未验证 |

- 非 SKIP 必须引用存在的 evidence；question_id 必须存在，各数组内 ID 唯一。
- inferred/proposal 不可 PASS；design 必须 proposal。
- runtime PASS 必须至少有 device/browser 的 observation/network，读取脚本不等于运行观察。
- static PASS 必须有 package/browser；statement PASS 必须有 public-source/user-report，仅表示声明内容已核对。
- 同时有观察与静态证据仍可能推不出架构结论；自然语言是否超出证据须人工复核，标签齐全不能替代判断。

示例：官方声明“支持导出”可以记 scope=statement、PASS；当前账号能否导出另写 scope=runtime、SKIP。不能把前者直接提升为后者。

## coverage

至少一项。每项 target、status（observed / partial / not_observed）、reason。缺包、解析失败、登录墙、不可读脚本、不可见正文分别说明原因。
网络抓包后端、采集窗口与每种事件覆盖应另在附件 manifest 中记录并通过 evidence 定位；未知不写为“不存在”。

## 最小拒绝样本

对完整样例做以下任一改变应失败，回归样本验证实际错误字段：

- 将 c1 的 scope 改成 runtime，仍只引用官方文档并保留 PASS。
- 将 c1 的 basis 改成 inferred，仍保留 PASS。
- 将 evidence_ids 改为 `["missing"]` 或将 e1.locator 清空。
- 重复证据 ID、增加未回答关键问题、使用无时区时间、降低深度却清空 gaps。

这些断言保护记录的最低要求；不能通过伪造 source 来证明事实。原始材料仍需复核。

## 操作路径交叉检查

`python3 scripts/check_report.py report.json --walkthrough steps.json` 核对 `walkthrough.py` 的每一步：`evidence` 必填且存在于本报告；
该证据须为 device/browser 的 observation；从录屏截取的步骤（有 `at` 或 `clip`），证据 `locator.target` 的文件名须与该步的录屏一致。
它只证明截图挂在了对的记录上，不证明截图内容与步骤描述相符——那仍要人看。

## HTTP / 流事件附件交叉检查

`python3 scripts/check_report.py report.json --capture /outside/repo/capture` 额外核对 manifest、network.jsonl、actions.jsonl 的会话/请求/动作 ID、正文状态及提供的页面/帧/重定向字段。动作必须是记录中的时间窗口候选；匹配不证明因果。引用 streams.jsonl 时 locator 另带 event_id，校验器按事件 ID 核对其 request_id、会话、候选动作与正文状态。CDP ID 不等于 HTTP ID，不能将候选关联当作同一条记录。candidate_action_ids 必须是非空字符串组成的数组，字符串子串不算关联。
