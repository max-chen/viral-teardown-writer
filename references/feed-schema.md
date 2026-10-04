# feed.json v1 结构与字段级判定表

模型把喂料或读到的页面内容转写为 **feed.json** v1；`../scripts/normalize_feed.py` 逐字段做确定性核验。
**模型只被允许摘抄，不允许断言**（公理 3：喂料不是许可证）。

## 顶层结构

```json
{
  "schema_version": "feed.v1",
  "items": [ { ... } ],
  "baselines": {
    "<author_id>": { "entries": [ { "metric_values": { "like": { "raw": "620", "src": "L88" } } } ] }
  }
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `schema_version` | 是 | 固定 **feed.v1** |
| `items` | 是 | 条目数组，可为空数组 |
| `baselines` | 否 | 账号主页近期开条，用于算同账号中位数；缺失即所有条目只走榜单判定 |

**baselines 的排列约定（必须遵守）**：entries 必须按发布时间**新到旧**排列
（最新一条在最前）。脚本取数组**前 10 条**作为「最近 10 条」（需求 Q3），
因此若按时间正序（oldest-first）喂料，取到的会是最旧 10 条，中位数失真。
脚本不判断时间先后——顺序由喂料约定保证。

## 条目结构

```json
{
  "item_id": "xhs-01",
  "title": { "raw": "客厅装修避坑：这5个插座位置我全做错了", "src": "L2" },
  "url": { "raw": "https://xhslink.com/a/Rt8xQm2", "src": "L2" },
  "rank_label": { "raw": "热榜", "src": "L1" },
  "rank_pos": { "raw": "1", "src": "L2" },
  "metrics": {
    "play": null,
    "like": { "raw": "1.2万", "src": "L3" },
    "collect": { "raw": "3500", "src": "L3" },
    "comment": { "raw": "820", "src": "L3" }
  },
  "publish_time": { "raw": "2026-09-28", "src": "L4" },
  "caption": { "raw": "客厅装修避坑：这5个插座位置我全做错了", "src": "L2" },
  "author_id": { "raw": "木木家", "src": "L5" },
  "content_flags": { "ad_hard_sell": false, "porn": false }
}
```

每个叶子值只有两种合法形态：`{"raw": "<原文片段>", "src": "<位置>"}` 或 `null`。
`src` 可选（用于人工复核定位），`raw` 必填。

`content_flags` 的 `ad_hard_sell` / `porn` 是**模型语义判定**（禁止用正则实现语义任务）。
标 `true` 即 fail-safe 丢弃，证据片段放在 `ad_hard_sell_evidence` / `porn_evidence`。

## 溯源核验规则

每个叶子值的 `raw` 必须是 `--raw` 指向的原始喂料文本的**精确子串**：

1. `null` → 该字段缺失，按各字段规则降级
2. `raw` 非空**且**是原文精确子串 → 有效
3. `raw` 为空串或纯空白 → 判伪，按缺失处理
4. 不做模糊匹配、不做去空格宽松匹配、不做大小写折叠

核验不过时记 **warning.code** 为 provenance_failed，该字段降级为「未获取」。

## 字段级判定表

由 `normalize_feed.normalize_item` 实现，零模型参与。

| # | 字段 | 有值的判定 | 缺失或判伪后的降级 | 影响条目存活 |
|---|---|---|---|---|
| F1 | **title.raw** | 非空且子串核验通过 | 丢弃该条目 | 是（入场券 1/2） |
| F2 | **url.raw** | 非空、核验通过、协议属于 http/https、query 不含凭据型参数名 | 丢弃该条目 | 是（入场券 2/2） |
| F3 | **rank_label.raw** | 非空、核验通过、归一化后属榜单位白名单或推荐流例外集 | 视为无榜标记 | 否 |
| F4 | **rank_pos.raw** | 非空、核验通过、纯数字（1 至 4 位） | 视为无榜位 | 否 |
| F5 | **metrics.play.raw** | 非空、核验通过、归一化非 UNPARSABLE | UNPARSABLE 计 0 并标「未获取」；VERBATIM 照抄但不计入总量 | 否 |
| F6 | **metrics.like.raw** | 同 F5 | 同 F5 | 否 |
| F7 | **metrics.collect.raw** | 同 F5 | 同 F5 | 否 |
| F8 | **metrics.comment.raw** | 同 F5 | 同 F5 | 否 |
| F9 | **publish_time.raw** | 非空、核验通过、命中日期格式白名单 | 视为缺失，排序键取末位 | 否 |
| F10 | **caption.raw** | 非空、核验通过 | 写「无文案」 | 否 |
| F11 | **author_id.raw** | 非空、核验通过 | 该条无基线资格，只走榜单判定 | 否 |
| F12 | `content_flags.ad_hard_sell` | 标 `true` 即丢弃（无论证据片段核验是否通过） | — | 是（丢弃） |
| F13 | **content_flags.porn** | 同 F12 | — | 是（丢弃） |
| F14 | `baselines[author_id]` | 基线块存在、`entries` 至少 1 条、每个 entry 的 `metric_values.*.raw` 核验通过 | 该条无基线资格，只走榜单判定 | 否 |

F12 / F13 采用 fail-safe：模型标 `true` 即丢弃，即便证据片段核验未过。
代价（模型可能误删合法条目）由「0 条是合法输出」吸收，收益是过滤有硬保证。
每次丢弃都写进 **run-plan.json** 的 `dropped[]`（含理由与证据片段），供 QA 抽检复核。

## 条目级存活与可判定

```text
存活     = F1 通过 且 F2 通过
可判定   = 存活 且（榜单位字样命中 或 有榜位 或 四项指标任一为 EXACT 或 VERBATIM）
```

`UNPARSABLE` 视为「无数」。`VERBATIM`（如 `约50万`）算「有数」——它确实是页面上
写着的数值，只是不可用于算术。

## 完整示例

原始喂料文本（片段）：

```text
【小红书 · 家居赛道 · 本周热榜】
1. 客厅装修避坑：这5个插座位置我全做错了   链接 https://xhslink.com/a/Rt8xQm2
   点赞 1.2万  收藏 3500  评论 820
   发布于 2026-09-28
   作者：木木家
```

对应 **feed.json** 条目：

```json
{
  "item_id": "xhs-01",
  "title": { "raw": "客厅装修避坑：这5个插座位置我全做错了", "src": "L2" },
  "url": { "raw": "https://xhslink.com/a/Rt8xQm2", "src": "L2" },
  "rank_label": { "raw": "热榜", "src": "L1" },
  "rank_pos": { "raw": "1", "src": "L2" },
  "metrics": {
    "play": null,
    "like": { "raw": "1.2万", "src": "L3" },
    "collect": { "raw": "3500", "src": "L3" },
    "comment": { "raw": "820", "src": "L3" }
  },
  "publish_time": { "raw": "2026-09-28", "src": "L4" },
  "caption": { "raw": "客厅装修避坑：这5个插座位置我全做错了", "src": "L2" },
  "author_id": { "raw": "木木家", "src": "L5" },
  "content_flags": { "ad_hard_sell": false, "porn": false }
}
```

注意 **rank_label.raw** 写的是 `热榜`——它是页面上的**字样**，模型不写「这个号在热榜上」
这种推断。字样归一化由脚本完成。

## d2_probe.json 形状

模型探测后写入，`../scripts/probe_d2.py` 只校验形状、不联网、不重试：

```json
{"available": true, "tool": "agent-browser", "attempts": 1, "status": "ok"}
```

| 字段 | 类型 | 约束 |
|---|---|---|
| `available` | 布尔 | 必填 |
| `tool` | 字符串 | 必填，探测到的工具名 |
| `attempts` | 整数 | 必填，**必须等于 1** |
| `status` | 字符串 | 必填，取值 `ok` / `timeout` / `error` |

- `attempts != 1` → exit 2，**探测禁止重试**，一次失败即按无工具处理
- `status` 为 `timeout` 或 `error` → 归一为 `available = false`，正常 exit 0
- 缺字段或类型不对 → exit 2，**不默认 false 继续跑**（避免「探测失败」与「未探测」不可区分）
