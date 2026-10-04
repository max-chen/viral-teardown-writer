#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest.py -- 内置回归用例集（蓝图 §14 的 32 项机检固化点）

三问裁决：确定性 ✅｜理解 ❌｜失败 ✅

覆盖（括号内为需求说明书 §9 的验收项编号）：
  A 组 单位归一三态      C3 / H2 裁决 D4
  B 组 中位数与阈值      F1 / F2 / F3 / F4
  C 组 充分性与五序分支  E1~E10 / D5
  D 组 溯源核验          E9 / E10
  E 组 排序与去重        C1 / C2 / C6 / G1 / G3 / G4
  F 组 渲染契约          B1 / B2 / B3 / B5 / B6 / B7 / B8 / B10 / G2 / G5
  G 组 探测与 0 条       E3 / E6 / E8 / C4
  H 组 短链与修饰词      H1 / H2 裁决 D2 / D4

测试数据纪律（蓝图 §6.6）：
  夹具只用**平台真实域名**（xiaohongshu.com / xhslink.com / douyin.com /
  v.douyin.com / bilibili.com / b23.tv），不使用 example.com / localhost /
  Foo / Bar / 张三 这类 token。数值是结构上明确标注为构造样本的合成数值，
  且每个用例集在输出里主动标注 synthetic: true，不隐瞒。
  **是否用构造样本自证由 QA 裁决**，本脚本不为此自我背书。

CLI:
    python3 selftest.py [--json] [--verbose]

退出码：0 全过 / 1 有用例失败。
"""

import argparse
import datetime
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import vpf_common as V            # noqa: E402
import normalize_feed as NF       # noqa: E402
import judge_evidence as JE       # noqa: E402
import rank_render as RR          # noqa: E402
import probe_d2 as PD             # noqa: E402

EXIT_OK, EXIT_FAIL = 0, 1

TODAY = "2026-10-04"
TODAY_DATE = V.parse_today(TODAY)

# 精确判定「去修饰形态」：本技能里只有 `约50万` 这一处含 `50万`。
# 因此断言 `50万` 的前一字符必须始终是模糊修饰词——若某处出现不带修饰的
# `50万`，说明我们把原文 `约50万` 改写成了去修饰的数值（违反 H2-6）。
# 不用泛化正则：EXACT 项（如 `1.1万`）本来就该以「数值+单位」形态出现，
# 泛化正则无法区分「合法的 EXACT 项」与「被去修饰的 VERBATIM 项」。
VERBATIM_PREFIX_RE = re.compile(r"(?<![约近莫超过大概＋+])50万")

# 全局用例计数
_RESULTS = []


class Ctx(object):
    """一次性临时目录，供各用例物化夹具。"""

    def __init__(self, root):
        self.root = pathlib.Path(root)

    def write(self, name, text):
        p = self.root / name
        p.write_text(text, encoding="utf-8")
        return str(p)

    def write_json(self, name, obj):
        return self.write(name, V.dumps_canonical(obj) + "\n")


def leaf(text, src="L1"):
    return {"raw": text, "src": src}


def feed(items, baselines=None):
    doc = {"schema_version": "feed.v1", "synthetic": True, "items": items}
    if baselines:
        doc["baselines"] = baselines
    return doc


def item(item_id, title, url, rank_label=None, rank_pos=None,
         play=None, like=None, collect=None, comment=None,
         publish=None, caption=None, author=None,
         ad=False, porn=False, ad_evidence=None, porn_evidence=None):
    """构造一个 feed 条目。指标参数传字符串原文或 None。"""
    flags = {"ad_hard_sell": bool(ad), "porn": bool(porn)}
    if ad and ad_evidence:
        flags["ad_hard_sell_evidence"] = leaf(ad_evidence)
    if porn and porn_evidence:
        flags["porn_evidence"] = leaf(porn_evidence)
    return {
        "item_id": item_id,
        "title": leaf(title) if title is not None else None,
        "url": leaf(url) if url is not None else None,
        "rank_label": leaf(rank_label) if rank_label is not None else None,
        "rank_pos": leaf(rank_pos) if rank_pos is not None else None,
        "metrics": {
            "play": leaf(play) if play is not None else None,
            "like": leaf(like) if like is not None else None,
            "collect": leaf(collect) if collect is not None else None,
            "comment": leaf(comment) if comment is not None else None,
        },
        "publish_time": leaf(publish) if publish is not None else None,
        "caption": leaf(caption) if caption is not None else None,
        "author_id": leaf(author) if author is not None else None,
        "content_flags": flags,
    }


def baseline_entry(like=None, play=None, collect=None, comment=None):
    mv = {}
    if like is not None:
        mv["like"] = leaf(like)
    if play is not None:
        mv["play"] = leaf(play)
    if collect is not None:
        mv["collect"] = leaf(collect)
    if comment is not None:
        mv["comment"] = leaf(comment)
    return {"metric_values": mv}


def check(name, condition, detail=""):
    _RESULTS.append({"name": name, "ok": bool(condition), "detail": detail})
    return bool(condition)


def eq(name, got, want, detail=""):
    ok = got == want
    return check(name, ok, detail or ("期望 {!r}，实际 {!r}".format(want, got)))


# ==========================================================================
# A 组：单位归一化三态（裁决 D4 / 验收 C3 / H2）
# ==========================================================================


def group_a_normalize():
    table = [
        # (输入, 期望态, 期望值)
        ("50", V.STATE_EXACT, 50),
        ("1,234", V.STATE_EXACT, 1234),
        ("1.2万", V.STATE_EXACT, 12000),
        ("1.2w", V.STATE_EXACT, 12000),
        ("1.2W", V.STATE_EXACT, 12000),
        ("3.5w", V.STATE_EXACT, 35000),
        ("1.2万点赞", V.STATE_EXACT, 12000),
        # N-09：「个」是真实页面常用量词（「收藏 500个」「1.2万个赞」）。
        # 该后缀自第 1 轮起就在 METRIC_SUFFIXES 里但无用例覆盖，砍掉它会让
        # 上述文案判 UNPARSABLE -> 该字段输出「未获取」，是数据丢失（功能退化）。
        ("1.2万个", V.STATE_EXACT, 12000),
        ("500个", V.STATE_EXACT, 500),
        ("约50万", V.STATE_VERBATIM, 500000),
        ("超10万", V.STATE_VERBATIM, 100000),
        ("3.5w+", V.STATE_VERBATIM, 35000),
        ("近3万", V.STATE_VERBATIM, 30000),
        ("点赞 3.5w", V.STATE_UNPARSABLE, None),
        ("一万", V.STATE_UNPARSABLE, None),
        ("十萬", V.STATE_UNPARSABLE, None),
        ("1.2万3", V.STATE_UNPARSABLE, None),
        ("1e6", V.STATE_UNPARSABLE, None),
        ("1.2E4", V.STATE_UNPARSABLE, None),
        ("-5万", V.STATE_UNPARSABLE, None),
        ("", V.STATE_UNPARSABLE, None),
    ]
    for raw, want_state, want_value in table:
        state, value, verbatim = V.normalize_metric(raw)
        ok = state == want_state and value == want_value
        check("A1 归一 {} -> {}".format(raw or "(空串)", want_state), ok,
              "期望 {}={}，实际 {}={}".format(want_state, want_value, state, value))
        if state in (V.STATE_EXACT, V.STATE_VERBATIM):
            check("A2 回写原文 {}".format(raw), verbatim == raw,
                  "原文必须逐字节保留，实际 {!r}".format(verbatim))

    # C3：1.2万 / 1.2w / 1.2W 三者归一值全等（排序结果因此全等）
    vals = {V.normalize_metric(s)[1] for s in ("1.2万", "1.2w", "1.2W")}
    eq("A3 C3 三种写法归一值全等", vals, {12000})

    # Decimal 路径：float 会有 11999.999… 误差
    state, value, _ = V.normalize_metric("1.2万")
    check("A4 Decimal 精确无浮点误差", state == V.STATE_EXACT and value == 12000,
          "得到 {!r}".format(value))

    # VERBATIM 不进算术
    check("A5 VERBATIM 不参与求和入口",
          V.exact_value("约50万") is None and V.exact_value("3.5w+") is None)


# ==========================================================================
# B 组：中位数与 3 倍阈值（验收 F1 / F2 / F3 / F4）
# ==========================================================================


def group_b_threshold():
    # F1 奇偶取值
    num, den, text = V.median_exact([5])
    eq("B1 奇数样本中位数", (num, den, text), (5, 1, "5"))
    num, den, text = V.median_exact([1, 2, 3])
    eq("B2 奇数 3 条取正中项", (num, den, text), (2, 1, "2"))
    num, den, text = V.median_exact([1, 2, 3, 4])
    eq("B3 偶数 4 条取中间两数均值", (num, den, text), (5, 2, "2.5"))
    num, den, text = V.median_exact([10, 20])
    eq("B4 偶数 2 条均值", (num, den, text), (30, 2, "15"))
    num, den, text = V.median_exact([3, 4])
    eq("B5 偶数均值落在 .5（禁 round 银行家舍入）", (num, den, text), (7, 2, "3.5"))

    # F2 3 倍边界：恰好 3 倍通过，2.99 倍不通过
    check("B6 恰好 3 倍通过（含等号）", V.ratio_passed(300, 100, 1))
    check("B7 2.99 倍不通过", not V.ratio_passed(299, 100, 1))
    check("B8 偶数基线恰好 3 倍通过", V.ratio_passed(450, 300, 2))
    check("B9 偶数基线 2.99 倍不通过", not V.ratio_passed(449, 300, 2))
    check("B10 纯整数路径无浮点抖动",
          V.ratio_passed(300, 100, 1) and not V.ratio_passed(299, 100, 1))

    # 空样本 / 中位数为 0
    eq("B11 空样本无中位数", V.median_exact([]), (None, None, None))
    check("B12 中位数为 0 时不判倍数", not V.ratio_passed(300, 0, 1))


# ==========================================================================
# C 组：喂料充分性 + 五序分支真值表（验收 E1~E7 / D5）
# ==========================================================================


def _run_stage1(ctx, raw_text, feed_doc, platform=None, probe=None,
                baseline_required=False, tag="case"):
    raw_path = ctx.write("{}_raw.txt".format(tag), raw_text)
    feed_path = ctx.write_json("{}_feed.json".format(tag), feed_doc)
    today = V.parse_today(TODAY)
    d2 = probe[0] if probe else False
    plan = NF.run(feed_path, raw_path,
                  V.normalize_platform_input(platform) if platform else None,
                  today, baseline_required, None)
    return plan


def _truth(ctx, tag, raw_text, feed_doc, d2_available, baseline_required=False):
    probe_path = None
    if d2_available is not None:
        probe_path = ctx.write_json(
            "{}_probe.json".format(tag),
            {"available": bool(d2_available), "tool": "agent-browser",
             "attempts": 1, "status": "ok"})
    raw_path = ctx.write("{}_raw.txt".format(tag), raw_text)
    feed_path = ctx.write_json("{}_feed.json".format(tag), feed_doc)
    plan = NF.run(feed_path, raw_path, None, V.parse_today(TODAY),
                  baseline_required, probe_path)
    return plan["feed_state"], plan["branch"], plan["d2_allowed"]


def group_c_branch(ctx):
    # 构造三种喂料状态
    raw_sufficient = (
        "热榜\n"
        "1. 甲内容 链接 https://xhslink.com/a/Suff01\n"
        "   点赞 1.2万  收藏 3500\n"
        "   发布于 2026-09-28\n"
    )
    f_sufficient = feed([
        item("s1", "甲内容", "https://xhslink.com/a/Suff01",
             rank_label="热榜", rank_pos="1", like="1.2万", collect="3500",
             publish="2026-09-28"),
    ])

    raw_insufficient = (
        "乙内容 链接 https://www.douyin.com/video/7000000000000000001\n"
        "   发布于 2026-09-28\n"
    )
    f_insufficient = feed([
        item("i1", "乙内容", "https://www.douyin.com/video/7000000000000000001",
             publish="2026-09-28"),
    ])

    raw_invalid = "这里只有一句没有任何链接和数字的说明文字\n"
    f_invalid = feed([item("v1", "丙内容", None, like="1.2万")])

    # 8 组真值表（feed_state x d2_available）
    truth = [
        ("F1", f_sufficient, raw_sufficient, True, V.FEED_STATE_SUFFICIENT, 1, False),
        ("F1", f_sufficient, raw_sufficient, False, V.FEED_STATE_SUFFICIENT, 1, False),
        ("F2", f_insufficient, raw_insufficient, True, V.FEED_STATE_INSUFFICIENT, 2, True),
        ("F2", f_insufficient, raw_insufficient, False, V.FEED_STATE_INSUFFICIENT, 3, False),
        ("F0", f_invalid, raw_invalid, True, V.FEED_STATE_INVALID, 4, True),
        ("F0", f_invalid, raw_invalid, False, V.FEED_STATE_INVALID, 5, False),
    ]
    for label, fdoc, raw, d2, want_state, want_branch, want_allowed in truth:
        state, branch, allowed = _truth(ctx, "t_{}_{}".format(label, d2),
                                        raw, fdoc, d2)
        check("C1 真值表 {}/d2={} -> branch {}".format(label, d2, want_branch),
              state == want_state and branch == want_branch and allowed == want_allowed,
              "期望 {} branch={} allowed={}，实际 {} branch={} allowed={}".format(
                  want_state, want_branch, want_allowed, state, branch, allowed))

    # 边界：只有一个数字 + 无榜单标记 -> 可判定 -> F1 充分 -> 序 1 不上网
    raw_one = "丁内容 链接 https://www.douyin.com/video/7000000000000000002\n   点赞 8888\n"
    f_one = feed([
        item("o1", "丁内容", "https://www.douyin.com/video/7000000000000000002",
             like="8888"),
    ])
    state, branch, allowed = _truth(ctx, "t_one", raw_one, f_one, False)
    check("C2 单数字无榜标记 -> F1 充分 -> 序 1 不联网",
          state == V.FEED_STATE_SUFFICIENT and branch == 1 and allowed is False,
          "实际 {} branch={} allowed={}".format(state, branch, allowed))

    # D5：喂了但全部条目缺入场券 -> F0（等价未喂料）
    raw_no_ticket = (
        "戊内容\n   点赞 1.2万\n"
        "己内容\n   点赞 2.4万\n"
    )
    f_no_ticket = feed([
        item("t1", "戊内容", None, like="1.2万"),
        item("t2", "己内容", None, like="2.4万"),
    ])
    state, branch, _ = _truth(ctx, "t_noticket", raw_no_ticket, f_no_ticket, True)
    check("C3 D5 全部缺入场券 -> F0 走序 4",
          state == V.FEED_STATE_INVALID and branch == 4,
          "实际 {} branch={}".format(state, branch))

    # D1：baseline_required=false 时「缺基线」不算不足（裁决 D1）
    raw_nobase = (
        "庚内容 链接 https://b23.tv/av1001\n"
        "   热搜榜第2位\n"
        "   点赞 5000\n"
    )
    f_nobase = feed([
        item("n1", "庚内容", "https://b23.tv/av1001",
             rank_label="热搜榜", rank_pos="2", like="5000"),
    ])
    state, _, _ = _truth(ctx, "t_nobase", raw_nobase, f_nobase, False)
    check("C4 D1 缺基线仍判 F1 充分（不上网）",
          state == V.FEED_STATE_SUFFICIENT, "实际 {}".format(state))

    # baseline_required=true 时同一喂料降为 F2（口径可翻转，只改常量/开关）
    state, _, _ = _truth(ctx, "t_nobase2", raw_nobase, f_nobase, False,
                         baseline_required=True)
    check("C5 baseline-required 开关生效 -> F2 不足",
          state == V.FEED_STATE_INSUFFICIENT, "实际 {}".format(state))

    # E7：同一份喂料两次运行走同一分支且 run-plan 逐字节一致
    raw_x = "辛内容 链接 https://xhslink.com/a/Idem01\n   点赞 1.2万\n"
    f_x = feed([item("x1", "辛内容", "https://xhslink.com/a/Idem01", like="1.2万")])
    s1 = V.dumps_canonical(_truth_feed(ctx, "idem", raw_x, f_x, None))
    s2 = V.dumps_canonical(_truth_feed(ctx, "idem", raw_x, f_x, None))
    check("C6 E7 同一喂料两次 run-plan 逐字节一致", s1 == s2)


def _truth_feed(ctx, tag, raw_text, feed_doc, probe):
    probe_path = None
    if probe:
        probe_path = ctx.write_json(
            "{}_probe.json".format(tag),
            {"available": bool(probe), "tool": "agent-browser",
             "attempts": 1, "status": "ok"})
    raw_path = ctx.write("{}_raw.txt".format(tag), raw_text)
    feed_path = ctx.write_json("{}_feed.json".format(tag), feed_doc)
    return NF.run(feed_path, raw_path, None, V.parse_today(TODAY), False, probe_path)


# ==========================================================================
# D 组：溯源核验（验收 E9 / E10）
# ==========================================================================


def group_d_provenance(ctx):
    raw = (
        "壬内容 链接 https://xhslink.com/a/Prov01\n"
        "   点赞 1.2万  收藏 800\n"
    )
    # E9：口头声称 50 万但原文无此串。
    # 注意语义：标题与地址在原文里（入场券齐备），伪造的只是指标。
    # 因此条目**存活**，但该指标降级为「未获取」，伪造数字不得进输出。
    fdoc = feed([
        item("p1", "壬内容", "https://xhslink.com/a/Prov01",
             like="50万", collect="800"),
    ])
    plan = _truth_feed(ctx, "e9", raw, fdoc, None)
    eq("D1 E9 入场券齐备时条目存活（只降级伪造字段）",
       plan["counts"]["alive"], 1)
    eq("D2 E9 伪造指标降级为未获取",
       plan["normalized_items"][0]["metrics"]["like"]["state"], V.STATE_UNPARSABLE)
    check("D3 E9 口头数字不得进输出字段",
          "50万" not in V.dumps_canonical(plan["normalized_items"]))

    # 合法子串通过
    fdoc_ok = feed([
        item("p2", "壬内容", "https://xhslink.com/a/Prov01",
             like="1.2万", collect="800"),
    ])
    plan_ok = _truth_feed(ctx, "ok", raw, fdoc_ok, None)
    eq("D4 溯源通过条目存活", plan_ok["counts"]["alive"], 1)

    # 非精确子串（原文没有的字符串）必须判伪
    fdoc_bad = feed([
        item("p3", "壬内容", "https://xhslink.com/a/Prov01", like="9999万"),
    ])
    plan_bad = _truth_feed(ctx, "bad", raw, fdoc_bad, None)
    check("D5 非子串指标判未获取",
          plan_bad["counts"]["alive"] == 1
          and plan_bad["normalized_items"][0]["metrics"]["like"]["state"] == V.STATE_UNPARSABLE)

    # 标题不在原文 -> 整条丢弃（入场券 1/2）
    fdoc_badtitle = feed([
        item("p4", "伪造的标题", "https://xhslink.com/a/Prov01", like="1.2万"),
    ])
    plan_bt = _truth_feed(ctx, "badtitle", raw, fdoc_badtitle, None)
    eq("D6 标题非原文子串 -> 整条丢弃", plan_bt["counts"]["alive"], 0)

    # E9 完整链路：渲染输出里不得出现口头数字
    raw2 = (
        "癸内容 链接 https://www.xiaohongshu.com/explore/abc123\n"
        "   热搜榜\n"
    )
    fdoc2 = feed([
        item("q1", "癸内容", "https://www.xiaohongshu.com/explore/abc123",
             rank_label="热搜榜", like="50万"),
    ])
    plan2 = _truth_feed(ctx, "e9full", raw2, fdoc2, None)
    plan_path = ctx.write_json("e9full_plan.json", plan2)
    judged = JE.run(plan_path, {}, TODAY_DATE)
    text, _summary = RR.build_final(judged, plan2["branch"], None)
    check("D7 E9 口头数字不出现在最终输出",
          "50万" not in text and "未获取" in text)


# ==========================================================================
# E 组：排序、去重、分档（验收 C1 / C2 / C6 / G1 / G3 / G4）
# ==========================================================================


def _pipeline(ctx, tag, raw_text, feed_doc, baselines=None, platform=None):
    """跑完三阶段，返回 (final_text, summary, plan, judged)。

    baselines 参数直接传 {author_id: [entry, ...]}（阶段 2 消费的形状）。
    """
    raw_path = ctx.write("{}_raw.txt".format(tag), raw_text)
    feed_path = ctx.write_json("{}_feed.json".format(tag), feed_doc)
    plan = NF.run(feed_path, raw_path,
                  V.normalize_platform_input(platform) if platform else None,
                  TODAY_DATE, False, None)
    plan_path = ctx.write_json("{}_plan.json".format(tag), plan)
    judged = JE.run(plan_path, baselines or {}, TODAY_DATE)
    judged_path = ctx.write_json("{}_judged.json".format(tag), judged)
    text, summary = RR.build_final(judged, plan["branch"], None)
    return text, summary, plan, judged


def entries(*vals):
    """把若干基线条目包成 {author: [entry...]} 之外的裸列表，便于展开。"""
    return list(vals)


def group_e_rank(ctx):
    # G1：仅「推荐」字样、无榜位 -> B 档（不算 A）
    raw = (
        "【为你推荐】\n"
        "1. 甲内容 链接 https://v.douyin.com/iRec0001/\n"
        "   点赞 2.4万\n"
    )
    fdoc = feed([
        item("r1", "甲内容", "https://v.douyin.com/iRec0001/",
             rank_label="为你推荐", like="2.4万"),
    ])
    _t, _s, plan, _j = _pipeline(ctx, "g1", raw, fdoc)
    eq("E1 G1 推荐流无榜位落 B 档", plan["normalized_items"][0]["tier"], "B")

    # 但有榜位时 -> A 档
    raw2 = (
        "【为你推荐】\n"
        "1. 甲内容 链接 https://v.douyin.com/iRec0002/\n"
        "   第4位\n"
        "   点赞 2.4万\n"
    )
    fdoc2 = feed([
        item("r2", "甲内容", "https://v.douyin.com/iRec0002/",
             rank_label="为你推荐", rank_pos="4", like="2.4万"),
    ])
    _t, _s, plan2, _j = _pipeline(ctx, "g1b", raw2, fdoc2)
    eq("E2 推荐流带榜位落 A 档", plan2["normalized_items"][0]["tier"], "A")

    # C2：去掉榜单标记后原 A 档条目不再占前排
    raw3 = (
        "热门榜\n"
        "1. 甲内容 链接 https://xhslink.com/a/CmpA01\n"
        "   点赞 100\n"
        "乙内容 链接 https://xhslink.com/a/CmpB01\n"
        "   点赞 9.9万\n"
    )
    fdoc3 = feed([
        item("c1", "甲内容", "https://xhslink.com/a/CmpA01",
             rank_label="热门榜", rank_pos="1", like="100"),
        item("c2", "乙内容", "https://xhslink.com/a/CmpB01", like="9.9万"),
    ])
    t3, _s3, _p3, _j3 = _pipeline(ctx, "c2on", raw3, fdoc3)
    order_on = t3.index("甲内容") < t3.index("乙内容")

    fdoc3b = feed([
        item("c1", "甲内容", "https://xhslink.com/a/CmpA01", like="100"),
        item("c2", "乙内容", "https://xhslink.com/a/CmpB01", like="9.9万"),
    ])
    t3b, _s3b, _p3b, _j3b = _pipeline(ctx, "c2off", raw3, fdoc3b)
    order_off = t3b.index("乙内容") < t3b.index("甲内容")
    check("E3 C2 去掉榜单标记后 A 档条目不再占前排",
          order_on and order_off,
          "有榜标记={}，去榜标记={}".format(order_on, order_off))

    # C3：1.2万 vs 1.2w 两份 fixture 排序结果全等
    base_raw = (
        "甲 链接 https://xhslink.com/a/Unit01\n   点赞 {}\n"
        "乙 链接 https://xhslink.com/a/Unit02\n   点赞 5000\n"
    )
    fa = feed([
        item("u1", "甲", "https://xhslink.com/a/Unit01", like="1.2万"),
        item("u2", "乙", "https://xhslink.com/a/Unit02", like="5000"),
    ])
    fb = feed([
        item("u1", "甲", "https://xhslink.com/a/Unit01", like="1.2w"),
        item("u2", "乙", "https://xhslink.com/a/Unit02", like="5000"),
    ])
    ta, _sa, _pa, ja = _pipeline(ctx, "unit_a", base_raw.format("1.2万"), fa)
    tb, _sb, _pb, jb = _pipeline(ctx, "unit_b", base_raw.format("1.2w"), fb)
    order_a = [i["item_id"] for i in ja["items"]]
    order_b = [i["item_id"] for i in jb["items"]]
    check("E4 C3 1.2万 与 1.2w 排序结果全等", order_a == order_b,
          "实际 {} vs {}".format(order_a, order_b))

    # G4：同 URL 跨榜重复 -> 输出 1 条、占 1 个名额
    raw_dup = (
        "热榜\n"
        "1. 甲内容 链接 https://xhslink.com/a/Dup0001\n"
        "   点赞 1.2万\n"
        "2. 乙内容 链接 https://xhslink.com/a/Dup0002\n"
        "   热搜榜\n"
        "   点赞 900\n"
        "热搜榜\n"
        "3. 甲内容 链接 https://xhslink.com/a/Dup0001\n"
        "   点赞 1.2万\n"
    )
    fdoc_dup = feed([
        item("d1", "甲内容", "https://xhslink.com/a/Dup0001",
             rank_label="热榜", rank_pos="1", like="1.2万"),
        item("d2", "乙内容", "https://xhslink.com/a/Dup0002",
             rank_label="热搜榜", like="900"),
        item("d3", "甲内容", "https://xhslink.com/a/Dup0001",
             rank_label="热搜榜", like="1.2万"),
    ])
    t_dup, s_dup, _p, _j = _pipeline(ctx, "dup", raw_dup, fdoc_dup)
    eq("E5 G4 同链接去重后只输出 1 条", s_dup["item_count"], 2)
    eq("E6 G4 被合并项不占名额", s_dup["item_count"], len(
        [1 for blk in t_dup.split("\n\n") if "标题：" in blk]))
    check("E7 G4 合并留痕", len(s_dup["merged_away"]) == 1,
          "实际 {}".format(s_dup["merged_away"]))

    # B1：>10 条截断到 10
    raw_many_lines = ["爆款清单"]
    many_items = []
    for n in range(1, 15):
        raw_many_lines.append(
            "{}. 内容{:02d} 链接 https://xhslink.com/a/Bulk{:04d}\n   点赞 {}万".format(
                n, n, n, n))
        many_items.append(item("b{:02d}".format(n), "内容{:02d}".format(n),
                              "https://xhslink.com/a/Bulk{:04d}".format(n),
                              like="{}万".format(n)))
    t_many, s_many, _p, _j = _pipeline(ctx, "bulk", "\n".join(raw_many_lines),
                                       feed(many_items))
    eq("E8 B1 超过 10 条截断到 10", s_many["item_count"], 10)
    check("E9 B1 不存在第 11 条", t_many.count("标题：") == 10,
          "实际 {} 个标题".format(t_many.count("标题：")))

    # C6：只有 3 条 -> 输出恰 3 条，不补齐
    raw_three = (
        "甲 链接 https://xhslink.com/a/Three01\n   点赞 1.2万\n"
        "乙 链接 https://xhslink.com/a/Three02\n   点赞 8000\n"
        "丙 链接 https://xhslink.com/a/Three03\n   点赞 5000\n"
    )
    f_three = feed([
        item("t1", "甲", "https://xhslink.com/a/Three01", like="1.2万"),
        item("t2", "乙", "https://xhslink.com/a/Three02", like="8000"),
        item("t3", "丙", "https://xhslink.com/a/Three03", like="5000"),
    ])
    t_three, s_three, _p, _j = _pipeline(ctx, "three", raw_three, f_three)
    eq("E10 C6 3 条合格内容输出恰 3 条", s_three["item_count"], 3)

    # G3：互动总量只对 EXACT 求和，且证据列出参与项
    raw_sum = (
        "甲 链接 https://xhslink.com/a/Sum0001\n"
        "   播放 1.2万  点赞 3000  收藏 500  评论 约50万\n"
    )
    f_sum = feed([
        item("s1", "甲", "https://xhslink.com/a/Sum0001",
             play="1.2万", like="3000", collect="500", comment="约50万"),
    ])
    t_sum, _s, _p, j_sum = _pipeline(ctx, "sum", raw_sum, f_sum)
    it = j_sum["items"][0]
    eq("E11 G3 互动总量只累加 EXACT 项", it["engagement_total"], 12000 + 3000 + 500)
    check("E12 G3 VERBATIM 项进 excluded_terms",
          any(x["raw"] == "约50万" for x in it["excluded_terms"]))
    check("E13 G3 VERBATIM 项不进 sum_terms",
          not any(x["raw"] == "约50万" for x in it["sum_terms"]))
    check("E14 G3 证据文本列出参与总量项", "参与总量" in it["evidence_text"])
    check("E15 G3 代码中无权重系数（总量为纯加和）",
          it["engagement_total"] == sum(x["value"] for x in it["sum_terms"]))

    # C1：同一输入两次 final.md 逐字节一致
    ta1, _sa1, _pa1, _ja1 = _pipeline(ctx, "idema", raw_three, f_three)
    ta2, _sa2, _pa2, _ja2 = _pipeline(ctx, "idemb", raw_three, f_three)
    check("E16 C1 同一输入两次输出逐字节一致", ta1 == ta2)


# ==========================================================================
# F 组：渲染契约（验收 B1/B2/B3/B5/B7/B8/B10/G2/G5）
# ==========================================================================


def group_f_render(ctx):
    raw = (
        "热榜\n"
        "1. 甲内容 链接 https://xhslink.com/a/Rnd0001\n"
        "   点赞 1.2万  收藏 800\n"
        "   发布于 2026-09-28\n"
    )
    long_caption = "这是一段很长的文案" * 60  # 明显超过 300 字
    fdoc = feed([
        item("r1", "甲内容", "https://xhslink.com/a/Rnd0001",
             rank_label="热榜", rank_pos="1", like="1.2万", collect="800",
             publish="2026-09-28", caption=long_caption),
    ])
    raw_long = raw + "   " + long_caption + "\n"
    t, s, _p, j = _pipeline(ctx, "render", raw_long, fdoc)

    # B2：每块恰 5 个字段，字段名与顺序固定
    blocks = [b for b in t.strip().split("\n\n") if "标题：" in b]
    check("F1 B2 输出块数与摘要一致", len(blocks) == s["item_count"])
    for blk in blocks:
        names = [ln.split("：")[0] for ln in blk.split("\n") if "：" in ln]
        eq("F2 B2 每块恰 5 个字段且顺序固定", names, list(RR.FIELD_NAMES))

    # B3：每条标题与地址非空
    check("F3 B3 每块都有非空标题与地址",
          all(ln.split("：", 1)[1].strip()
              for blk in blocks for ln in blk.split("\n")
              if ln.startswith(("标题：", "地址："))))

    # B7 / G2：文案超 300 字截断并标注，不补全
    cap_line = [ln for ln in t.split("\n") if ln.startswith("文案：")][0]
    cap_value = cap_line.split("：", 1)[1]
    check("F4 G2 超长文案截断到 300 字", len(cap_value) <= 300 + len("（已截断，原文超 300 字）"))
    check("F5 G2 截断有标注", "已截断" in cap_value)
    check("F6 G2 截断不改写不补全",
          cap_value.startswith("这是一段很长的文案"))

    # 无文案 -> 字面量「无文案」
    raw_nocap = (
        "热榜\n"
        "1. 甲内容 链接 https://xhslink.com/a/Nocap01\n"
        "   点赞 1.2万\n"
    )
    f_nocap = feed([
        item("n1", "甲内容", "https://xhslink.com/a/Nocap01",
             rank_label="热榜", rank_pos="1", like="1.2万"),
    ])
    t_nc, _s, _p, _j = _pipeline(ctx, "nocap", raw_nocap, f_nocap)
    check("F7 B7 无文案写「无文案」", "文案：无文案" in t_nc)

    # B10 / G5：末尾无总结/建议/占比统计
    check("F8 B10 输出无总结建议段",
          not any(w in t for w in ("总结", "规律", "建议", "下一步", "方法论")))
    check("F9 G5 无占比统计行", "占比" not in t and "本批" not in t)

    # B6 / B8 后置断言：自造修饰词与替代字面量必须被拦
    try:
        RR.post_assertions("标题：甲\n地址：https://xhslink.com/a/X\n"
                           "平台：小红书\n凭什么算爆款：播放 大约 50 万\n文案：无文案",
                           [])
        caught = False
    except V.VpfError:
        caught = True
    check("F10 B6 自造修饰词被后置断言拦截", caught)

    caught = False
    try:
        RR.post_assertions("标题：甲\n地址：https://xhslink.com/a/X\n"
                           "平台：小红书\n凭什么算爆款：未获取\n文案：暂无",
                           [])
    except V.VpfError:
        caught = True
    check("F11 B8 「暂无」替代写法被拦截", caught)

    for bad in ("N/A", "null", "0", "-"):
        caught = False
        try:
            RR.post_assertions("凭什么算爆款：{}".format(bad), [])
        except V.VpfError:
            caught = True
        check("F12 B8 替代写法 {} 被拦截".format(bad), caught)

    # 原文修饰词不误伤（裁决 D4 的正确边界）
    ok = True
    try:
        RR.post_assertions(
            "凭什么算爆款：点赞 约50万（参与总量：点赞 3000）", ["约50万"])
    except V.VpfError:
        ok = False
    check("F13 D4 原文修饰词照抄不误伤", ok)


# ==========================================================================
# G 组：D2 探测与 0 条（验收 E3 / E6 / E8 / C4）
# ==========================================================================


def _run_cli(fn, argv):
    """跑一个 CLI 的 main() 并吞掉它的 stdout/stderr，只取退出码。

    避免 probe_d2 的 JSON 输出污染 selftest 自身的报告。
    """
    import io
    import contextlib

    buf_out, buf_err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        code = fn(argv)
    return code


def group_g_probe(ctx):
    # E8：attempts != 1 -> exit 2
    p = ctx.write_json("bad_probe.json",
                       {"available": True, "tool": "x", "attempts": 2, "status": "ok"})
    code = _run_cli(PD.main, ["--probe", p])
    eq("G1 E8 attempts=2 必须 exit 2", code, 2)

    # E8：status=timeout -> 归一为 available=false 且 exit 0
    p2 = ctx.write_json("to_probe.json",
                        {"available": True, "tool": "x", "attempts": 1, "status": "timeout"})
    res = PD.validate(json.loads(pathlib.Path(p2).read_text(encoding="utf-8")))
    check("G2 E8 status=timeout 归一为无工具", res["available"] is False)
    eq("G3 E8 timeout 时 attempts 仍为 1", res["attempts"], 1)
    eq("G4 E8 timeout 正常 exit 0", _run_cli(PD.main, ["--probe", p2]), 0)

    # 缺字段 -> exit 2（不默认 false 继续跑）
    p3 = ctx.write_json("miss_probe.json", {"tool": "x", "attempts": 1, "status": "ok"})
    eq("G5 缺 available 字段 exit 2", _run_cli(PD.main, ["--probe", p3]), 2)

    # 类型错 -> exit 2
    p4 = ctx.write_json("type_probe.json",
                        {"available": "yes", "tool": "x", "attempts": 1, "status": "ok"})
    eq("G6 available 类型错 exit 2", _run_cli(PD.main, ["--probe", p4]), 2)

    # 正常形状 -> exit 0 且 available=true
    p5 = ctx.write_json("ok_probe.json",
                        {"available": True, "tool": "agent-browser",
                         "attempts": 1, "status": "ok"})
    eq("G7 正常探测 exit 0", _run_cli(PD.main, ["--probe", p5]), 0)

    # E3 / C4：F2 不足 + 无工具 -> 0 条 + blockers，且 exit 0
    raw_ins = "乙内容 链接 https://www.douyin.com/video/7000000000000000011\n"
    f_ins = feed([item("i1", "乙内容", "https://www.douyin.com/video/7000000000000000011")])
    raw_path = ctx.write("ins_raw.txt", raw_ins)
    feed_path = ctx.write_json("ins_feed.json", f_ins)
    plan = NF.run(feed_path, raw_path, None, TODAY_DATE, False, None)
    eq("G8 F2 + 无工具 -> branch 3", plan["branch"], 3)
    eq("G9 E3 branch3 输出 blockers", bool(plan["blockers"]), True)
    plan_path = ctx.write_json("ins_plan.json", plan)
    judged = JE.run(plan_path, {}, TODAY_DATE)
    text, summary = RR.build_final(judged, plan["branch"], None)
    eq("G10 E3 无通路输出 0 条", summary["item_count"], 0)
    # G11 是**规范声明**，不是行为断言（QA N-08）：它把「0 条绝不能用非 0 退出码」
    # 这条硬约束写在回归集里，让读用例的人知道这是有意为之。写死 True 意味着任何
    # 实现改动它都通过——真正的机器保障是 G10（item_count==0）加上 CLI 层的
    # 退出码实测（branch=3/5 时 normalize_feed 与 rank_render 均 exit 0）。
    # 保留它是为了留痕，不是为了测试。
    check("G11 （规范声明，非行为断言）0 条是合法输出，不得用非 0 退出码", True)
    check("G12 E3 0 条说明缺什么与需要喂什么",
          "缺什么" in text and "需要你喂什么" in text)
    check("G13 E6 0 条说明不教方法（不出现「你可以自己去搜」）",
          "自己去搜" not in text and "教你" not in text)

    # E6：F0 无效喂料 + 无工具 -> 0 条
    raw_inv = "这里没有任何链接和数字的说明文字\n"
    f_inv = feed([item("v1", "丙", None, like="1.2万")])
    raw_path2 = ctx.write("inv_raw.txt", raw_inv)
    feed_path2 = ctx.write_json("inv_feed.json", f_inv)
    plan2 = NF.run(feed_path2, raw_path2, None, TODAY_DATE, False, None)
    eq("G14 F0 + 无工具 -> branch 5", plan2["branch"], 5)
    plan_path2 = ctx.write_json("inv_plan.json", plan2)
    judged2 = JE.run(plan_path2, {}, TODAY_DATE)
    text2, summary2 = RR.build_final(judged2, plan2["branch"], None)
    eq("G15 E6 无效喂料输出 0 条", summary2["item_count"], 0)


# ==========================================================================
# H 组：短链与修饰词裁决（验收 H1 / H2）
# ==========================================================================


def group_h_rulings(ctx):
    # H1：短链照收，地址逐字节不变，平台未获取也不丢条目
    raw = (
        "甲内容 链接 https://xhslink.com/a/H1Short01\n"
        "   点赞 1.2万\n"
    )
    fdoc = feed([
        item("h1", "甲内容", "https://xhslink.com/a/H1Short01", like="1.2万"),
    ])
    t, s, plan, j = _pipeline(ctx, "h1", raw, fdoc)
    it = j["items"][0]
    check("H1-1 短链条目出现在输出中", "https://xhslink.com/a/H1Short01" in t)
    check("H1-2 地址与喂料原文逐字节相同",
          "地址：https://xhslink.com/a/H1Short01" in t)
    check("H1-3 输出中不存在展开后的真实域名",
          "xiaohongshu.com" not in t)
    eq("H1-4 短链 host 归属推断为小红书", it["platform_field"], "小红书")
    check("H1-5 短链标记留痕", it["url_is_shortlink"] is True)

    # H1 反向：无法归属的短链 -> 平台「未获取」但条目不丢
    raw2 = (
        "乙内容 链接 https://url.cn/H1Unknow1\n"
        "   点赞 2.4万\n"
    )
    fdoc2 = feed([
        item("h2", "乙内容", "https://url.cn/H1Unknow1", like="2.4万"),
    ])
    t2, s2, _p2, j2 = _pipeline(ctx, "h1b", raw2, fdoc2)
    eq("H1-6 无法归属短链条目仍输出", s2["item_count"], 1)
    eq("H1-7 平台字段填「未获取」不猜", j2["items"][0]["platform_field"], "未获取")

    # H1 反向 fixture：同一短链跨两条记录 -> 按 raw 键去重为 1 条
    raw3 = (
        "丙内容 链接 https://b23.tv/H1Dup0001\n"
        "   点赞 1.2万\n"
        "丁内容 链接 https://b23.tv/H1Dup0001\n"
        "   点赞 900\n"
    )
    fdoc3 = feed([
        item("h3", "丙内容", "https://b23.tv/H1Dup0001", like="1.2万"),
        item("h4", "丁内容", "https://b23.tv/H1Dup0001", like="900"),
    ])
    t3, s3, _p3, _j3 = _pipeline(ctx, "h1c", raw3, fdoc3)
    eq("H1-8 同一短链跨两条记录去重为 1 条", s3["item_count"], 1)

    # H2：修饰词照抄 + 不计入总量
    raw4 = (
        "戊内容 链接 https://xhslink.com/a/H2Verb01\n"
        "   播放 约50万  点赞 3000\n"
    )
    fdoc4 = feed([
        item("h5", "戊内容", "https://xhslink.com/a/H2Verb01",
             play="约50万", like="3000"),
    ])
    t4, _s4, _p4, j4 = _pipeline(ctx, "h2", raw4, fdoc4)
    it4 = j4["items"][0]
    check("H2-1 修饰词原文逐字节出现在输出中", "约50万" in t4)
    check("H2-2 该项在 excluded_terms",
          any(x["raw"] == "约50万" for x in it4["excluded_terms"]))
    check("H2-3 该项不在 sum_terms",
          not any(x["raw"] == "约50万" for x in it4["sum_terms"]))
    eq("H2-4 互动总量等于其余 EXACT 项之和", it4["engagement_total"], 3000)
    check("H2-5 输出中不出现被改写的 500000", "500000" not in t4)
    # QA 指出原断言 `count("50万")==count("约50万")` 数学上恒等（50万 只会出现在
    # 约50万 内部），测不出东西。改为精确判定：`50万` 前面不得是修饰词。
    check("H2-6 输出中不出现独立形态的 50万（前一字符不得为修饰词）",
          VERBATIM_PREFIX_RE.search(t4) is None,
          "实际命中：{}".format(VERBATIM_PREFIX_RE.search(t4)))

    # H2 反向 fixture：把 约50万 换成 50万 -> 总量必须变化（证明剔除生效）
    raw5 = (
        "己内容 链接 https://xhslink.com/a/H2Verb02\n"
        "   播放 50万  点赞 3000\n"
    )
    fdoc5 = feed([
        item("h6", "己内容", "https://xhslink.com/a/H2Verb02",
             play="50万", like="3000"),
    ])
    _t5, _s5, _p5, j5 = _pipeline(ctx, "h2b", raw5, fdoc5)
    eq("H2-7 去掉修饰词后总量变化（证明剔除生效）",
       j5["items"][0]["engagement_total"], 500000 + 3000)

    # 短链与长链不做同源归并（D2 副产物，两条各自参与排序）
    raw6 = (
        "庚内容 链接 https://xhslink.com/a/H1Pair001\n"
        "   点赞 1.2万\n"
        "庚内容 链接 https://www.xiaohongshu.com/explore/H1Pair001\n"
        "   点赞 1.2万\n"
    )
    fdoc6 = feed([
        item("h7", "庚内容", "https://xhslink.com/a/H1Pair001", like="1.2万"),
        item("h8", "庚内容", "https://www.xiaohongshu.com/explore/H1Pair001",
             like="1.2万"),
    ])
    _t6, s6, _p6, _j6 = _pipeline(ctx, "h1d", raw6, fdoc6)
    eq("H1-9 短链与长链不按同源归并（各占 1 席）", s6["item_count"], 2)


# ==========================================================================
# I 组：指标退化与基线（验收 F3 / F4 / C7 / F5）
# ==========================================================================


def group_i_metric(ctx):
    # F4：无 play 列 -> metric_used=like 且证据标明所用指标
    raw = (
        "甲账号主页近期开条 点赞 1000\n"
        "乙账号主页近期开条 点赞 1000\n"
        "甲账号主页近期开条 点赞 1000\n"
        "本次内容 链接 https://xhslink.com/a/Degr01\n"
        "   点赞 5万\n"
    )
    fdoc = feed(
        [item("m1", "本次内容", "https://xhslink.com/a/Degr01",
              like="5万", author="甲账号")],
        baselines={"甲账号": {"entries": [
            baseline_entry(like="1000"), baseline_entry(like="1000"),
            baseline_entry(like="1000"),
        ]}},
    )
    _t, _s, _p, j = _pipeline(ctx, "degrade", raw, fdoc,
                              baselines={"甲账号": entries(
                                  baseline_entry(like="1000"),
                                  baseline_entry(like="1000"),
                                  baseline_entry(like="1000"))})
    it = j["items"][0]
    eq("F1-1 F4 无 play 时 metric_used=like", it["metric_used"], "like")
    check("F1-2 F4 证据文本标明所用指标",
          "判定所用指标：点赞" in it["evidence_text"])

    # F3：样本不足 10 条 -> 标明实际条数，不虚报 10
    raw2 = (
        "乙账号主页近期开条 点赞 1000\n"
        "本次内容 链接 https://www.xiaohongshu.com/explore/Degr002\n"
        "   点赞 5万\n"
    )
    fdoc2 = feed(
        [item("m2", "本次内容", "https://www.xiaohongshu.com/explore/Degr002",
              like="5万", author="乙账号")],
        baselines={"乙账号": {"entries": [baseline_entry(like="1000")]}},
    )
    _t2, _s2, _p2, j2 = _pipeline(ctx, "few", raw2, fdoc2,
                                  baselines={"乙账号": entries(
                                      baseline_entry(like="1000"))})
    it2 = j2["items"][0]
    check("F1-3 F3 证据含实际样本数 1", "近 1 条" in it2["evidence_text"])
    check("F1-4 F3 不虚报「近 10 条」", "近 10 条" not in it2["evidence_text"])

    # C7 / F5：无基线 -> 全文不含「中位数」「倍」
    raw3 = (
        "丙内容 链接 https://www.douyin.com/video/7000000000000000099\n"
        "   点赞 5万\n"
    )
    fdoc3 = feed([
        item("m3", "丙内容", "https://www.douyin.com/video/7000000000000000099",
             like="5万"),
    ])
    t3, _s3, _p3, j3 = _pipeline(ctx, "nobase", raw3, fdoc3)
    check("F1-5 C7 无基线不写「中位数」", "中位数" not in t3)
    check("F1-6 F5 无基线不写「倍」", "倍" not in t3)
    eq("F1-7 无基线时 ratio_pass 为 None", j3["items"][0]["ratio_pass"], None)

    # VERBATIM 被评指标 -> 不参与倍数判定，且证据注明
    raw4 = (
        "丁账号主页近期开条 点赞 1000\n"
        "本次内容 链接 https://xhslink.com/a/VerbR1\n"
        "   点赞 约50万\n"
    )
    fdoc4 = feed(
        [item("m4", "本次内容", "https://xhslink.com/a/VerbR1",
              like="约50万", author="丁账号")],
        baselines={"丁账号": {"entries": [
            baseline_entry(like="1000"), baseline_entry(like="1000")]}},
    )
    _t4, _s4, _p4, j4 = _pipeline(ctx, "verbratio", raw4, fdoc4,
                                  baselines={"丁账号": entries(
                                      baseline_entry(like="1000"),
                                      baseline_entry(like="1000"))})
    it4 = j4["items"][0]
    check("F1-8 VERBATIM 被评指标不判倍数", it4["ratio_pass"] is not True)
    check("F1-9 VERBATIM 被评指标证据注明不参与倍数判定",
          "含模糊修饰" in it4["evidence_text"])


# ==========================================================================
# J 组：过滤与入口券（验收 B3 / B9）
# ==========================================================================


def group_j_filter(ctx):
    raw = (
        "甲内容 链接 https://xhslink.com/a/Flt0001\n"
        "   点赞 1.2万\n"
        "缺标题行\n"
        "   点赞 900\n"
        "丙内容 链接\n"
        "   点赞 800\n"
        "广告内容 链接 https://www.douyin.com/ad/7000000000000000001\n"
        "   点赞 500\n"
    )
    fdoc = feed([
        item("f1", "甲内容", "https://xhslink.com/a/Flt0001", like="1.2万"),
        item("f2", None, "https://xhslink.com/a/Flt0002", like="900"),
        item("f3", "丙内容", None, like="800"),
        item("f4", "广告内容", "https://www.douyin.com/ad/7000000000000000001",
             like="500", ad=True, ad_evidence="戳链接抢购"),
    ])
    t, s, plan, j = _pipeline(ctx, "filter", raw, fdoc)
    # 甲内容 + 缺标题丢弃 + 缺地址丢弃 + 广告丢弃 = 只剩 1 条
    eq("J1 入场券与广告过滤后只留 1 条", s["item_count"], 1)
    reasons = {d["reason"] for d in plan["dropped"]}
    check("J2 缺标题被丢弃", "missing_title" in reasons)
    check("J3 缺地址被丢弃", "missing_url" in reasons)
    check("J4 广告条目被丢弃", "content_flag:ad_hard_sell" in reasons)
    check("J5 B9 广告不出现在输出", "广告内容" not in t)
    check("J6 丢弃留痕可复核",
          all("evidence_raw" in d for d in plan["dropped"]))


# ==========================================================================
# K 组：E10 同构（喂料通道 vs 外网通道）
# ==========================================================================


def group_k_isomorphic(ctx):
    """E10 同构：同一条内容分别经 D1 喂料与 D2 读取获取，结构全等但证据值可不同。

    D-07：原实现是「同一份 fixture 跑两遍」，只能证明无随机性，不构成 E10 证据。
    E10 的真实要求是「证据值可不同，格式不得不同」，因此这里用**两组不同的
    证据值**（不同链接、不同数字、不同发布时间），断言结构侧全等。
    """
    # D1 通道：用户喂料，小红书短链 + 1.2万点赞 + 榜位 3
    raw_d1 = (
        "热榜\n"
        "甲内容 链接 https://xhslink.com/a/Iso0001\n"
        "   点赞 1.2万  收藏 800\n"
        "   发布于 2026-09-28\n"
    )
    f_d1 = feed([
        item("iso1", "甲内容", "https://xhslink.com/a/Iso0001",
             rank_label="热榜", rank_pos="3", like="1.2万", collect="800",
             publish="2026-09-28"),
    ])
    # D2 通道：工具读到的同一内容，完整长链 + 8.7万点赞 + 榜位 3
    raw_d2 = (
        "热榜\n"
        "甲内容 链接 https://www.xiaohongshu.com/explore/Iso0001\n"
        "   点赞 8.7万  收藏 2.4万\n"
        "   发布于 2026-10-01\n"
    )
    f_d2 = feed([
        item("iso1", "甲内容", "https://www.xiaohongshu.com/explore/Iso0001",
             rank_label="热榜", rank_pos="3", like="8.7万", collect="2.4万",
             publish="2026-10-01"),
    ])

    t1, s1, p1, j1 = _pipeline(ctx, "iso_d1", raw_d1, f_d1)
    t2, s2, p2, j2 = _pipeline(ctx, "iso_d2", raw_d2, f_d2)
    it1, it2 = j1["items"][0], j2["items"][0]

    # 前置：证据值确实不同（否则下面的「结构全等」是同义反复）
    check("K0 E10 两通道证据值确实不同（测试前提）",
          it1["evidence_text"] != it2["evidence_text"],
          "两组 fixture 的证据应不同，否则本组又变成同义反复")

    # 结构侧必须全等
    eq("K1 E10 tier 全等", it1["tier"], it2["tier"])
    eq("K2 E10 证据形态 id 全等",
       it1["evidence_template_id"], it2["evidence_template_id"])
    eq("K3 E10 排序位置全等", s1["items"][0]["rank"], s2["items"][0]["rank"])
    eq("K4 E10 五字段结构全等（字段名与顺序）",
       [ln.split("：")[0] for ln in t1.split("\n") if "：" in ln],
       [ln.split("：")[0] for ln in t2.split("\n") if "：" in ln])
    eq("K5 E10 条目数全等", s1["item_count"], s2["item_count"])
    # 五字段的「框架」全等：把证据文本与地址挖掉后应完全相同
    skeleton = lambda t: [ln.split("：")[0] for ln in t.split("\n") if "：" in ln]
    eq("K6 E10 输出骨架全等", skeleton(t1), skeleton(t2))
    check("K7 E10 证据数值允许不同（这是 E10 的前提）",
          ("1.2万" in t1) and ("8.7万" in t2))


# ==========================================================================
# L 组：零网络与路径基准（验收 D1 / E2）
# ==========================================================================


def group_l_runtime(ctx):
    """零网络与路径基准（验收 D1 / E2）。

    用 AST 解析 import 语句，而不是纯文本 grep——注释与文档字符串里提到这些
    模块名是合理的（例如说明「本库零网络依赖」），纯 grep 会误报。
    """
    import ast

    script_dir = pathlib.Path(__file__).resolve().parent
    net_modules = {"urllib", "requests", "socket", "http", "httplib",
                   "playwright", "selenium", "aiohttp", "ftplib", "telnetlib"}
    net_calls = {"urlopen", "get", "post", "request", "create_connection"}
    offenders = []
    getcwd_files = []

    for py in sorted(script_dir.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # 只看真正的 import，不看注释/字符串
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in net_modules:
                        offenders.append("{}: import {}".format(py.name, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root = node.module.split(".")[0]
                    if root in net_modules:
                        offenders.append(
                            "{}: from {} import ...".format(py.name, node.module))
            # os.getcwd() 调用
            elif isinstance(node, ast.Call):
                func = node.func
                fname = getattr(func, "attr", None) or getattr(func, "id", None)
                if fname == "getcwd":
                    getcwd_files.append("{}: os.getcwd()".format(py.name))
                if fname in net_calls:
                    mod = getattr(getattr(func, "value", None), "id", "")
                    if mod in net_modules:
                        offenders.append(
                            "{}: {}.{}()".format(py.name, mod, fname))

    check("L1 E2 脚本零网络 import / 调用（AST 断言）", not offenders,
          "命中：{}".format(offenders))
    check("L2 D1 脚本不出现 os.getcwd()（AST 断言）", not getcwd_files,
          "命中：{}".format(getcwd_files))

    # 路径基准以脚本位置为基准，不依赖 cwd
    check("L3 D1 SKILL_ROOT 以 __file__ 为基准",
          V.SKILL_ROOT == script_dir.parent and script_dir.name == "scripts")

    # 确定性 JSON：无时间戳、无随机
    payload = {"b": 1, "a": [1, 2, 3]}
    eq("L4 确定性 JSON 序列化稳定", V.dumps_canonical(payload), V.dumps_canonical(payload))

    # 发布日期白名单：时间点不是发布时间
    check("L5 字幕时间点 00:12 不被判为发布时间",
          V.parse_publish_time("00:12", TODAY_DATE) is None)
    check("L6 相对日期锚定 --today",
          V.parse_publish_time("3天前", TODAY_DATE) == "2026-10-01")

    # ASCII 直引号（脚本与配置须用直引号）
    bad_quotes = []
    for py in sorted(script_dir.glob("*.py")):
        for lineno, line in enumerate(py.read_text(encoding="utf-8").split("\n"), 1):
            if any(ch in line for ch in ("\u201c", "\u201d", "\u2018", "\u2019")):
                bad_quotes.append("{}:{}".format(py.name, lineno))
    check("L7 脚本与配置用 ASCII 直引号", not bad_quotes,
          "非 ASCII 引号行：{}".format(bad_quotes))

    # 全部脚本 3.8 语法可解析（禁用 3.9+ 语法）
    grammar_bad = []
    for py in sorted(script_dir.glob("*.py")):
        try:
            ast.parse(py.read_text(encoding="utf-8"), feature_version=(3, 8))
        except SyntaxError as exc:
            grammar_bad.append("{}: {}".format(py.name, exc))
    check("L8 全部脚本符合 Python 3.8 语法", not grammar_bad,
          "不兼容：{}".format(grammar_bad))


# ==========================================================================
# M 组：平台归属一致性 + QA 缺陷回归（C8 / D-01~D-08）
# ==========================================================================


def group_m_platform_and_regressions(ctx):
    """C8 平台归属一致性，以及 QA 报告 D-01~D-08 的回归固化。

    D-05：蓝图 §14 把 C8 列为机检（「URL host 与平台枚举交叉断言」），
    但实现从未落地（`grep C8` = 0 命中），改 host 表就会静默失守。
    """
    # ---------- C8：host 决定平台字段，且 --platform 不覆盖 host 判定 ----------
    c8_cases = [
        ("https://www.douyin.com/video/7412345678901234567", "douyin", "抖音"),
        ("https://v.douyin.com/iR8xQm2Ab/", "douyin", "抖音"),
        ("https://www.bilibili.com/video/BV1xx411c7mD", "bilibili", "B站"),
        ("https://b23.tv/av170001", "bilibili", "B站"),
        ("https://channels.weixin.qq.com/s/abc123", "wechat_channels", "视频号"),
        ("https://www.xiaohongshu.com/explore/66f1a2b3", "xiaohongshu", "小红书"),
        ("https://xhslink.com/a/Rt8xQm2", "xiaohongshu", "小红书"),
    ]
    for url, want_key, want_label in c8_cases:
        eq("M1 C8 host 归属 {}".format(want_key),
           V.platform_by_host(V.parse_url(url)["host"]), want_key)
        field, source = V.resolve_platform_field(url, None)
        eq("M2 C8 平台字段由 host 决定 {}".format(want_label), field, want_label)
        eq("M3 C8 平台来源标记为 host {}".format(want_label), source, "host")
        # 关键：--platform 不得覆盖 host 判定（否则抖音链接会被标成 B 站）
        other = "bilibili" if want_key != "bilibili" else "douyin"
        field2, source2 = V.resolve_platform_field(url, other)
        eq("M4 C8 --platform 不覆盖 host 判定 {}".format(want_key), field2, want_label)
        eq("M5 C8 来源仍是 host {}".format(want_key), source2, "host")

    # 归属不上的短链：平台填「未获取」，来源 none（裁决 D2 不猜）
    for url in ("https://url.cn/5abXyZ", "https://t.cn/abc123",
                "https://v.kuaishou.com/short/xyz"):
        field, source = V.resolve_platform_field(url, None)
        eq("M6 C8 无法归属短链填未获取 {}".format(V.parse_url(url)["host"]),
           field, V.MISSING_NUM)
        eq("M7 C8 无法归属短链来源为 none", source, "none")
        check("M8 C8 无法归属短链 host 不归属任何平台 {}"
              .format(V.parse_url(url)["host"]),
              V.platform_by_host(V.parse_url(url)["host"]) is None)

    # ---------- D-03：--platform 过滤掉能明确归属为其他平台的条目 ----------
    raw_mix = (
        "小红书内容 链接 https://www.xiaohongshu.com/explore/XHS001\n   点赞 9.9万\n"
        "抖音内容 链接 https://www.douyin.com/video/7412345678901234567\n   点赞 100\n"
        "未知内容 链接 https://url.cn/UNK001\n   点赞 500\n"
    )
    f_mix = feed([
        item("xhs", "小红书内容", "https://www.xiaohongshu.com/explore/XHS001",
             like="9.9万"),
        item("dy", "抖音内容", "https://www.douyin.com/video/7412345678901234567",
             like="100"),
        item("unk", "未知内容", "https://url.cn/UNK001", like="500"),
    ])
    raw_path = ctx.write("mix_raw.txt", raw_mix)
    feed_path = ctx.write_json("mix_feed.json", f_mix)
    plan = NF.run(feed_path, raw_path, V.normalize_platform_input("douyin"),
                  TODAY_DATE, False, None)
    plan_path = ctx.write_json("mix_plan.json", plan)
    judged = JE.run(plan_path, {}, TODAY_DATE)
    text, summary = RR.build_final(judged, plan["branch"], None)
    ids = [i["item_id"] for i in judged["items"]]
    check("M9 D03 指定抖音时过滤掉小红书条目", "xhs" not in ids, "实际保留：{}".format(ids))
    check("M10 D03 保留抖音条目", "dy" in ids)
    check("M11 D03 保留无法归属的短链条目（裁决 D2）", "unk" in ids)
    check("M12 D03 平台不匹配的条目进了 dropped 且理由可复核",
          any(d["reason"] == "platform_mismatch" for d in plan["dropped"]))
    check("M13 D03 输出中不含被过滤平台的条目", "小红书内容" not in text)

    # 不指定 --platform 时不过滤（保持原行为：全平台都收）
    plan_nofilter = NF.run(feed_path, raw_path, None, TODAY_DATE, False, None)
    eq("M14 D03 未指定平台时不过滤",
       plan_nofilter["counts"]["alive"], 3)

    # ---------- D-01：VERBATIM 项在证据文本里必须有「未计入」标注 ----------
    for raw_val, tag in (("约50万", "约50万"), ("3.5w+", "3.5w+"),
                         ("超10万", "超10万"), ("约50万点赞", "约50万点赞")):
        raw_v = "条目甲 链接 https://www.xiaohongshu.com/explore/DEF001\n   点赞 {}  收藏 1.1万\n".format(raw_val)
        f_v = feed([item("v", "条目甲", "https://www.xiaohongshu.com/explore/DEF001",
                         like=raw_val, collect="1.1万")])
        t_v, _s, _p, j_v = _pipeline(ctx, "verb_{}".format(tag), raw_v, f_v)
        ev = j_v["items"][0]["evidence_text"]
        check("M15 D01 {} 证据文本含「未计入」标注".format(tag),
              "未计入" in ev, "实际：{}".format(ev))
        check("M16 D01 {} 被剔除项原文仍逐字节照抄".format(tag), tag in ev)
        check("M17 D01 {} 标注与原文分层（剔除项不在参与总量串里）".format(tag),
              tag in ev and V.MISSING_NUM not in ev)

    # ---------- D-02：合法文案放行，真汇总行仍拦 ----------
    legal_captions = ['占比高达 60%，太夸张了', '共12条笔记',
                      '这条视频占比很高', '命中率不错', '占比',
                      '这条共 12 条笔记整理', '建议：先买后看']
    for cap in legal_captions:
        it = {"title_raw": "标题甲", "url_raw": "https://xhslink.com/a/SUM01",
              "platform_field": "小红书", "evidence_text": "点赞 1.2万",
              "caption_raw": cap, "sum_terms": [], "excluded_terms": [],
              "tier": "A", "tier_rank": 0, "rank_pos": None,
              "engagement_total": 1, "publish_iso": None}
        caught = False
        try:
            RR.post_assertions(RR.render_item(it, 1), RR.collect_raw_values([it]))
        except V.VpfError:
            caught = True
        check("M18 D02 合法文案放行 {}".format(cap), not caught,
              "页面原文含这些字属正常，不该被拦（QA D-02）")

    # 关于 M18 与 M19 的列表重叠（QA N-05）：**这不是自相矛盾，两者测的是不同形态**。
    #   M18 传 raw_values（该字来自页面原文）-> 放行，因为页面原文不受我们约束；
    #   M19 不传 raw_values（该字是我们自己写的）-> 拦截。
    # 例如「共12条」：作为 caption 原文是放行的；作为我们自己拼的汇总行必须拦。
    # 真实渲染路径的行只有两种形态（块首「第 N 条」与「字段名：值」），代码自己
    # 从不产生裸汇总行，因此 M19 里「共12条 / 命中率不错 / 占比」三条测的是
    # **防御性状态**——它们保证将来若有人在末尾拼这类行会被拦住。
    illegal_lines = ['本批 10 条中 3 条未获取', '本批10条中3条未获取',
                     '共12条', '命中率不错', '## 总结', '占比 30%']
    for bad in illegal_lines:
        caught = False
        try:
            RR.post_assertions(bad, [])
        except V.VpfError:
            caught = True
        check("M19 D02 真汇总行仍被拦 {}".format(bad), caught,
              "我们自己写的汇总行必须拦住（无 raw 背书；与 M18 的差异仅在 raw_values）")

    # ---------- D-04：含换行的 caption 放行（口播文案常态多行） ----------
    multiline = "第一行文案\n第二行大约 5000 播放\n第三行占比很高"
    it = {"title_raw": "标题乙", "url_raw": "https://xhslink.com/a/NL01",
          "platform_field": "小红书", "evidence_text": "点赞 1.2万",
          "caption_raw": multiline, "sum_terms": [], "excluded_terms": [],
          "tier": "A", "tier_rank": 0, "rank_pos": None,
          "engagement_total": 1, "publish_iso": None}
    caught = False
    try:
        RR.post_assertions(RR.render_item(it, 1), RR.collect_raw_values([it]))
    except V.VpfError:
        caught = True
    check("M20 D04 含换行且含「大约」的 caption 放行", not caught,
          "折叠后的形态也必须能被 raw 剔除（QA D-04）")
    # 反向：无 raw 背书的自造修饰词仍必须拦
    caught = False
    try:
        RR.post_assertions("文案：第二行大约 5000 播放", [])
    except V.VpfError:
        caught = True
    check("M21 D04 反向：无 raw 背书的自造修饰词仍被拦", caught)

    # ---------- D-06：色情丢弃缺证据时留警告（fail-safe 仍执行） ----------
    raw_p = "丙内容 链接 https://www.douyin.com/ad/7000000000000000001\n   点赞 500\n"
    f_p = feed([item("p", "丙内容", "https://www.douyin.com/ad/7000000000000000001",
                     like="500", porn=True, porn_evidence="疑似擦边")])
    plan_p = _truth_feed(ctx, "porn_ev", raw_p, f_p, None)
    dropped_p = [d for d in plan_p["dropped"] if d["reason"] == "content_flag:porn"]
    eq("M22 D06 色情条目被 fail-safe 丢弃", len(dropped_p), 1)
    check("M23 D06 色情丢弃带证据片段",
          dropped_p and dropped_p[0]["evidence_raw"] == "疑似擦边")
    f_p2 = feed([item("p2", "丙内容", "https://www.douyin.com/ad/7000000000000000001",
                      like="500", porn=True)])
    plan_p2 = _truth_feed(ctx, "porn_noev", raw_p, f_p2, None)
    check("M24 D06 色情无证据片段时仍丢弃（fail-safe 优先）",
          any(d["reason"] == "content_flag:porn" for d in plan_p2["dropped"]))
    check("M25 D06 色情无证据片段时记 drop_without_evidence 警告",
          any(w["code"] == "drop_without_evidence" for w in plan_p2["warnings"]),
          "缺证据时复核能力下降，必须留痕")

    # ---------- D-08：基线截断下沉到 compute_baseline（直调也不越界） ----------
    many = entries(*[baseline_entry(like=str(100 + 10 * i)) for i in range(15)])
    bl = JE.compute_baseline(many, "like")
    eq("M26 D08 直调路径基线也被截断到 10 条", bl["n"], 10)
    check("M27 D08 基线上限常量与证据文本一致",
          bl["n"] <= V.BASELINE_MAX_ENTRIES)

    # ---------- N-01：不可归属短链的平台字段（传与不传 --platform 都要验） ----------
    # 裁决 D2 明文：url.cn 这类推断不出就填「未获取」，不猜。
    # 且条目**照常保留**（D2 放行条件）。QA 指出原 H1-7 只在不传 --platform 时
    # 成立，是假保护；这里对两种调用方式都断言。
    unattributable = [
        ("https://url.cn/N01Unk01", "url.cn"),
        ("https://t.cn/N01Unk02", "t.cn"),
        ("https://v.kuaishou.com/short/N01Unk03", "v.kuaishou.com"),
    ]
    for url, host_tag in unattributable:
        # (a) 不传 --platform
        f_no, s_no = V.resolve_platform_field(url, None)
        eq("M28 N01 {} 不传平台时填未获取".format(host_tag), f_no, V.MISSING_NUM)
        eq("M29 N01 {} 不传平台时来源为 none".format(host_tag), s_no, "none")
        # (b) 传 --platform（关键：原实现在这里填用户平台，违反 D2）
        for plat_ctx in ("douyin", "xiaohongshu", "bilibili", "wechat_channels"):
            f_ctx, s_ctx = V.resolve_platform_field(url, plat_ctx)
            eq("M30 N01 {} 传 --platform={} 仍填未获取".format(host_tag, plat_ctx),
               f_ctx, V.MISSING_NUM)
            eq("M31 N01 {} 传 --platform={} 来源仍为 none".format(host_tag, plat_ctx),
               s_ctx, "none")

    # 端到端：传 --platform 时 url.cn 条目不猜平台、但也不丢条目
    raw_unk = "无法归属 链接 https://url.cn/N01E2E1\n   点赞 7777\n"
    f_unk = feed([item("unk", "无法归属", "https://url.cn/N01E2E1", like="7777")])
    _t_unk, s_unk, _p_unk, j_unk = _pipeline(ctx, "n01_e2e", raw_unk, f_unk,
                                             platform="douyin")
    eq("M32 N01 端到端传平台时 url.cn 条目仍保留", s_unk["item_count"], 1)
    eq("M33 N01 端到端传平台时平台字段为未获取",
       j_unk["items"][0]["platform_field"], V.MISSING_NUM)
    # 对照：可归属短链在同样传参下必须**仍走 host 判定填正确平台**，防止 N-01 修过头。
    # 原写法是 `items == [] or platform_field == "小红书"`，其中「条目被丢」本身
    # 就是一种失败却被当成通过条件，使本断言同时接受两种互斥结果、对「修过头」
    # 无判别力（QA N-06）。改为 `and` 收紧：条目必须存活**且**平台字段必须是 host
    # 判定值。同一场景下 `xhslink.com` 遇 `--platform=douyin` 会被 D-03 的
    # platform_mismatch 丢弃，那是 M9~M13 覆盖的另一条路径，不在本用例范围。
    f_own = feed([item("own", "归属短链", "https://xhslink.com/a/N01Own01", like="8888")])
    raw_own = "归属短链 链接 https://xhslink.com/a/N01Own01\n   点赞 8888\n"
    _t_own, s_own, _p_own, j_own = _pipeline(ctx, "n01_own", raw_own, f_own,
                                             platform="xiaohongshu")
    check("M34 N01 可归属短链在同参下仍走 host 判定（不过修）",
          bool(j_own["items"]) and j_own["items"][0]["platform_field"] == "小红书",
          "可归属短链必须存活且平台字段由 host 判定为小红书，不应被当成不可归属"
          "（传与指定平台一致的平台，避免与 D-03 的 platform_mismatch 路径混淆）")

    # ---------- N-02：渲染层兜底丢弃（B3 第二道保险，零用例 -> 变异 G4/P2 0 红）----
    base_item = {
        "index": 0, "item_id": "r1", "title_raw": "标题甲",
        "url_raw": "https://xhslink.com/a/N02Keep1", "url_host": "xhslink.com",
        "url_is_shortlink": True, "platform_field": "小红书", "tier": "B",
        "tier_rank": 1, "rank_pos": None, "rank_label_norm": None,
        "metric_used": "like", "metric_raw": "1.2万", "baseline": None,
        "ratio_pass": None, "engagement_total": 12000,
        "sum_terms": [{"metric": "like", "label": "点赞", "raw": "1.2万", "value": 12000}],
        "excluded_terms": [], "missing_terms": [], "metrics": {},
        "publish_iso": "2026-09-28", "caption_raw": None,
        "evidence_template_id": "absolute", "evidence_text": "点赞 1.2万",
    }
    # (a) 缺地址 -> 必须丢弃，且留痕
    no_url = dict(base_item)
    no_url["url_raw"] = ""
    no_url["item_id"] = "r2"
    t_a, s_a = RR.build_final({"items": [no_url], "branch": 1}, 1, None)
    eq("M35 N02 缺地址条目被渲染层丢弃", s_a["item_count"], 0)
    check("M36 N02 缺地址丢弃有留痕",
          any(d["item_id"] == "r2" for d in s_a["discarded_at_render"]),
          "discarded_at_render 缺该条则变异 G4/P2 不会被抓住")
    check("M37 N02 输出中无空地址行", "地址：\n" not in t_a and "地址： " not in t_a)
    # (b) 缺标题 -> 必须丢弃
    no_title = dict(base_item)
    no_title["title_raw"] = ""
    no_title["item_id"] = "r3"
    t_b, s_b = RR.build_final({"items": [no_title], "branch": 1}, 1, None)
    eq("M38 N02 缺标题条目被渲染层丢弃", s_b["item_count"], 0)
    check("M39 N02 缺标题丢弃有留痕",
          any(d["item_id"] == "r3" for d in s_b["discarded_at_render"]))
    # (c) 对照：完整条目照常输出（证明上面不是「全丢」）
    t_c, s_c = RR.build_final({"items": [base_item], "branch": 1}, 1, None)
    eq("M40 N02 完整条目照常输出（对照）", s_c["item_count"], 1)
    eq("M41 N02 完整条目无丢弃留痕（对照）", s_c["discarded_at_render"], [])

    # ---------- N-03：凭据型 query 拦截（N9 安全红线，零用例 -> 变异 H8 0 红）----
    cred_url = "https://www.douyin.com/video/7000000000000000009?token=SECRET123"
    check("M42 N03 凭据型 query 被识别（单元级）",
          V.url_has_credential_query(V.parse_url(cred_url)))
    check("M43 N03 普通 query 不误伤（单元级）",
          not V.url_has_credential_query(
              V.parse_url("https://www.douyin.com/video/7000000000000000009?xid=99")))
    for key in ("token", "sign", "session", "cookie", "auth"):
        u = "https://www.douyin.com/video/7000000000000000009?{}=SECRET123".format(key)
        check("M44 N03 凭据参数名 {} 被识别".format(key),
              V.url_has_credential_query(V.parse_url(u)))
    # 端到端：带凭据的 URL 必须被丢，且理由可复核
    raw_cred = "凭据条目 链接 {}\n   点赞 5000\n".format(cred_url)
    f_cred = feed([item("c1", "凭据条目", cred_url, like="5000")])
    plan_cred = _truth_feed(ctx, "n03", raw_cred, f_cred, None)
    eq("M45 N03 端到端带凭据条目被丢弃", plan_cred["counts"]["alive"], 0)
    check("M46 N03 丢弃理由为 url_credential_query",
          any(d["reason"] == "url_credential_query" for d in plan_cred["dropped"]),
          "凭据 URL 放行等于把用户临时凭证泄露到清单里")

    # ---------- N-04：相对日期 今天/昨天/前天（零用例 -> 变异 P4 0 红）----------
    for word, delta_days in (("今天", 0), ("今日", 0), ("昨天", 1),
                             ("昨日", 1), ("前天", 2)):
        want = (TODAY_DATE - datetime.timedelta(days=delta_days)).isoformat()
        eq("M47 N04 相对日期 {} 解析正确".format(word),
           V.parse_publish_time(word, TODAY_DATE), want)
    check("M48 N04 DATE_REL_DAYS 全部键都有覆盖",
          all(k in V.DATE_REL_DAYS for k in ("今天", "昨天", "前天")))
    # 端到端：用「今天」的条目必须有 publish_iso（否则排序键静默取末位）
    raw_today = "今天条目 链接 https://xhslink.com/a/N04Today\n   点赞 9999\n   发布于 今天\n"
    f_today = feed([item("t1", "今天条目", "https://xhslink.com/a/N04Today",
                         like="9999", publish="今天")])
    _t_td, _s_td, _p_td, j_td = _pipeline(ctx, "n04", raw_today, f_today)
    eq("M49 N04 端到端「今天」解析为执行当天",
       j_td["items"][0]["publish_iso"], TODAY_DATE.isoformat())


# ==========================================================================
# main
# ==========================================================================


def group_n_draft(ctx):
    """N 组：写稿段（第二段）硬红线回归。

    覆盖 scripts/draft_check.py 的全部规则号，并对每条做判别力验证——
    「改坏什么，这条用例会红」答不上来的用例不算数。
    """
    import draft_check as dc

    # --- 夹具 -------------------------------------------------------------
    # 合规稿：素材里有第一人称，参考原文与正文无长公共片段
    good = (
        "1. 标题\n"
        "辞职第八个月，我收到了比上班时高四成的工资\n"
        "2. 正文\n"
        "第八个月，我在出租屋里收到那封邮件。\n"
        "工资比我上班时高四成。我第一反应不是高兴，是手心发凉。\n"
        "前面八个月，我每天流水从三百八到一千二不等。最少的一天，八十九块。\n"
        "3. 内核\n"
        "真正让人撑住的不是希望，是知道自己能撑多久。\n"
        "4. 改口\n"
        "这件事让我明白了很多 → 我第一反应不是高兴，是手心发凉\n"
        "然后我就开始想以后怎么干 → 前面八个月，我每天流水从三百八到一千二\n"
        "所以这个机会我一定要抓住 → 工资比我上班时高四成\n"
    )
    material = "我在化工厂干了八年质检。2024年3月辞职。\n每天流水380到1200不等，最少的一天89块。\n"
    source = "辞职第二天，我删掉了所有工作群。\n后来我才知道，那一刻我以为的天塌下来。\n"

    def _rules(text, mat="", src=""):
        r = dc.check_draft(text, material=mat, source=src)
        return {v["rule"] for v in r["violations"]}

    def _ok(text, mat="", src=""):
        return dc.check_draft(text, material=mat, source=src)["ok"]

    # --- N1 合规稿放行（防误杀是第一优先级）------------------------------
    eq("N1 合规稿无违规", True, _ok(good, material, source), True)
    eq("N1 合规稿含全部四块", ["标题", "正文", "内核", "改口"],
       dc.check_draft(good)["blocks"])

    # 素材非空时，第一人称断言必须放行（真实转述，不是编造）
    first_person_ok = good.replace("我在出租屋里收到那封邮件。", "我收到那封邮件。")
    eq("N1 素材非空时第一人称放行", True, _ok(first_person_ok, material, source), True)

    # --- N2 四块结构 ------------------------------------------------------
    eq("N2 缺标题块被拦",
       "W-BLOCK-COUNT" in _rules(good.split("2. 正文")[0] + good.split("2. 正文")[1]), True)
    # 越界第5 块：块名可能是合法名（"5. 正文"）也可能越界（"5. 附记" / "9. 方法论"）。
    # 三种都必须拦——否则模型多输出第5 段方法教学时，校验器只数到4 块、零违规通过。
    for extra_name in ("5. 正文", "5. 附记", "9. 方法论"):
        five_blocks = good + "{}\n\n多写了第五块\n".format(extra_name)
        eq("N2 越界块「{}」被拦".format(extra_name),
           "W-BLOCK-COUNT" in _rules(five_blocks), True)
    # 越界块名必须被拦：白名单只有四个名字，别的名字（附记/方法论）必须报错。
    # 缺这条用例时，把白名单放宽到含「附记」不会有任何用例变红——已实测确认。
    for bad_name in ("5. 附记", "9. 方法论"):
        extra = good + "{}\n\n多写了第五块\n".format(bad_name)
        eq("N2 越界块名「{}」必须报W-BLOCK-NAME".format(bad_name),
           "W-BLOCK-NAME" in _rules(extra), True)
    # 判别力：真正决定 W-BLOCK-NAME 的是 REQUIRED_BLOCKS 这个白名单常量，
    # 不是 BLOCK_HEADER_RE 里的正则（实测：改正则不影响该规则）。
    # 故变异必须针对常量本身，否则是在验一条不存在的因果链。
    _saved_names = dc.REQUIRED_BLOCKS[:]
    dc.REQUIRED_BLOCKS.append("附记")
    try:
        names_off = "W-BLOCK-NAME" in _rules(good + "5. 附记\n\n多写了第五块\n")
    finally:
        dc.REQUIRED_BLOCKS[:] = _saved_names
    eq("N2 判别力：白名单加入附记后必须不报 W-BLOCK-NAME", names_off, False)

    # 判别力：把宽松块头关闭，五块与合法四块将无法区分——合规稿必须仍通过
    _saved_loose = dc.LOOSE_HEADER_RE
    dc.LOOSE_HEADER_RE = re.compile(r"^(?!x)x")   #永不匹配
    try:
        eq("N2 判别力：关掉宽松块头后合规稿仍过",
           _ok(good, material, source), True)
    finally:
        dc.LOOSE_HEADER_RE = _saved_loose
    swapped = good.replace("3. 内核", "X. 内核").replace("2. 正文", "3. 内核").replace("X. 内核", "2. 正文")
    eq("N2 块顺序错被拦", "W-BLOCK-ORDER" in _rules(swapped), True)
    eq("N2 缺改口块被拦",
       "W-BLOCK-COUNT" in _rules(good.split("4. 改口")[0]), True)

    # --- N3 禁用词 --------------------------------------------------------
    for w in dc.BANNED_WORDS:
        eq("N3 禁用词「{}」被拦".format(w), True,
           "W-BANNED-WORD" in _rules(good.replace("手心发凉", w)))
    # 判别力：禁用词表清空后，上面的用例必须变红
    # 判别力：表里有「绝了」时必须拦，表清空后必须不拦。
    # 两个方向都要断言，否则清空表也可能因为别的原因变红/不变红。
    with_banned = "W-BANNED-WORD" in _rules(good.replace("手心发凉", "绝了"))
    _saved_words = dc.BANNED_WORDS[:]
    dc.BANNED_WORDS.clear()
    try:
        without_banned = "W-BANNED-WORD" in _rules(good.replace("手心发凉", "绝了"))
    finally:
        dc.BANNED_WORDS[:] = _saved_words
    eq("N3 判别力：表有词时拦", with_banned, True)
    eq("N3 判别力：表清空后同一输入必须不拦", without_banned, False)

    # 禁用词只扫标题与正文；改口块出现禁用词必须放行
    rev_with_banned = good.replace("这件事让我明白了很多", "这件事质感拉满")
    eq("N3 改口块含禁用词放行（不扫改口）", True, _ok(rev_with_banned, material, source), True)
    eq("N3 内核块含禁用词放行（不扫内核）", True, _ok(good.replace("真正让人撑住的不是希望", "这波操作质感极高"),
           material, source), True)

    # --- N4 段首套话 ------------------------------------------------------
    for phrase in dc.BANNED_OPENERS:
        eq("N4 段首「{}」被拦".format(phrase),
           "W-OPENER" in _rules(good.replace("第八个月，我在出租屋", phrase + "，第八个月")),
           True)
    # 判别力：两个方向都要断言。变异脚本清空 BANNED_OPENERS 时，
    # 「有词时拦」必须变红——只断言反向（清空后不拦）的话，
    # 正向那条断了也无人发现。实测：曾因只断言反向导致 0 红。
    _opener_in = good.replace("第八个月，我在出租屋", "因此，第八个月")
    _saved_open = dc.BANNED_OPENERS[:]
    dc.BANNED_OPENERS.clear()
    try:
        opener_off = "W-OPENER" in _rules(_opener_in)
    finally:
        dc.BANNED_OPENERS[:] = _saved_open
    eq("N4 判别力：段首表清空后同一输入必须不拦", opener_off, False)
    eq("N4 判别力：段首表恢复后同一输入必须拦",
       "W-OPENER" in _rules(_opener_in), True)
    # 只看第一句：正文中段出现该词必须放行
    mid = good.replace("我在化工厂", "因此我在化工厂")
    eq("N4 正文中段出现「因此」放行（只扫第一句）",
       _ok(mid, material, source), True)

    # --- N5 内核单句 ------------------------------------------------------
    core_two = good.replace(
        "真正让人撑住的不是希望，是知道自己能撑多久。\n",
        "选对方法比努力更重要。\n每个人都可以复制。\n")
    eq("N5 内核两行被拦", "W-CORE-ONELINE" in _rules(core_two), True)

    # --- N6 改口恰好三处 --------------------------------------------------
    two_rev = (
        good.rsplit("所以这个机会我一定要抓住 → 工资比我上班时高四成\n", 1)[0]
        + "4. 改口\n"
        + "这件事让我明白了很多 → 我第一反应不是高兴，是手心发凉\n"
        + "然后我就开始想以后怎么干 → 前面八个月，我每天流水从三百八到一千二\n")
    eq("N6 改口两处被拦", "W-REV-COUNT" in _rules(two_rev), True)
    bad_fmt = good.replace("这件事让我明白了很多 → 我第一反应不是高兴，是手心发凉",
                           "这件事让我明白了很多")
    eq("N6 改口缺箭头被拦", "W-REV-FORMAT" in _rules(bad_fmt), True)
    # 箭头变体容忍
    for arrow in ("->", "➜"):
        eq("N6 改口容忍箭头「{}」".format(arrow),
           _ok(good.replace("→", arrow), material, source), True)

    # --- N7 素材溯源 ------------------------------------------------------
    empty_mat = good.replace("我在出租屋里收到那封邮件。", "我当时收到那封邮件。")
    eq("N7 素材为空却写「我当时」被拦",
       "W-MATERIAL" in _rules(empty_mat, ""), True)
    eq("N7 同一句素材非空时放行", True, _ok(empty_mat, material, source), True)
    for phrase in ("我测过", "我发现", "我买过"):
        eq("N7 空素材「{}」被拦".format(phrase), True,
           "W-MATERIAL" in _rules(good.replace("第八个月", phrase + "第八个月"), ""))

    # 判别力：断言表清空后必须变红
    # 判别力：断言表有效时必须拦，换成空表后同一输入必须不拦。
    fp_active = "W-MATERIAL" in _rules(empty_mat, "")
    _saved_fp = dc.FIRST_PERSON_CLAIM_RE
    dc.FIRST_PERSON_CLAIM_RE = re.compile(r"(永远不会匹配的词XYZ)")
    try:
        fp_disabled = "W-MATERIAL" in _rules(empty_mat, "")
    finally:
        dc.FIRST_PERSON_CLAIM_RE = _saved_fp
    eq("N7 判别力：断言表有效时拦", fp_active, True)
    eq("N7 判别力：断言表失效后同一输入必须不拦", fp_disabled, False)

    # --- N8 洗稿检测 ------------------------------------------------------
    # 正文整句照搬原文
    plagiarized = (
        "1. 标题\n我的辞职那天\n"
        "2. 正文\n" + source.replace("\n", "\n") +
        "3. 内核\n人最难的不是放弃。\n"
        "4. 改口\n" + "改完了 → 改完了\n" * 2 + "再改 → 再改\n"
    )
    eq("N8 整句搬运被拦", "W-PLAGIARISM" in _rules(plagiarized, material, source), True)
    # 合规稿不得被误判
    eq("N8 合规稿不被误判洗稿", True, _ok(good, material, source), True)
    # 阈值边界：恰好 12 字不拦（>= 才拦），11 字不拦
    eq("N8 阈值 11 字不拦", True, _ok(good.replace("我在化工厂", "我在化工厂里干过质检"), material, source), True)
    span_12 = source[:12]
    body_12 = good.replace("第八个月，我在出租屋里收到那封邮件。", span_12)
    eq("N8 阈值边界可复算：最长公共片段实测", True, len(dc.longest_shared_span(dc._squeeze(good), dc._squeeze(source))) < dc.MAX_SHARED_SPAN, True)

    # 换行不得参与比对（若参与，合规稿会因换行差异白送公共片段）
    squeezed = dc._squeeze(good)
    eq("N8 换行已从洗稿比对中压掉", True, "\n" not in squeezed, True)
    eq("N8 归一化保留换行（结构解析依赖行边界）", True, "\n" in dc._norm(good), True)

    # 判别力：阈值调到 0后必须全拦
    _saved_span = dc.MAX_SHARED_SPAN
    dc.MAX_SHARED_SPAN = 0
    try:
        eq("N8 判别力：阈值归零后合规稿也须变红",
           _ok(good, material, source), False)
    finally:
        dc.MAX_SHARED_SPAN = _saved_span

    # --- N9 无参考原文时不得拦洗稿 --------------------------------------
    eq("N9 无参考原文不拦洗稿", True, _ok(good, material, ""), True)

    # --- N10 CLI 退出码 ---------------------------------------------------
    draft_p = str(ctx.root / "draft_n.md")
    mat_p = str(ctx.root / "mat_n.txt")
    src_p = str(ctx.root / "src_n.txt")
    with open(draft_p, "w", encoding="utf-8") as fh:
        fh.write(good)
    with open(mat_p, "w", encoding="utf-8") as fh:
        fh.write(material)
    with open(src_p, "w", encoding="utf-8") as fh:
        fh.write(source)

    dpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "draft_check.py")
    dpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "draft_check.py")
    rc = _run_cli(dc.main, ["--draft", draft_p,
                            "--material", mat_p, "--source", src_p])
    eq("N10 CLI 合规稿 exit 0", 0, rc)
    rc = _run_cli(dc.main, ["--draft", draft_p, "--material", mat_p,
                            "--source", src_p, "--json"])
    eq("N10 CLI --json exit 0", 0, rc)
    rc = _run_cli(dc.main, ["--draft", str(ctx.root / "nope.md")])
    eq("N10 CLI 文件不存在 exit 2", 2, rc)

    bad_p = ctx.root / "draft_bad.md"
    bad_p.write_text(good.replace("手心发凉", "神器"), encoding="utf-8")
    rc = _run_cli(dc.main, ["--draft", str(bad_p)])
    eq("N10 CLI 违规稿 exit 2", 2, rc)


def run_all(verbose=False):
    _RESULTS[:] = []
    tmp = tempfile.mkdtemp(prefix="vpf_selftest_")
    try:
        ctx = Ctx(tmp)
        group_a_normalize()
        group_b_threshold()
        group_c_branch(ctx)
        group_d_provenance(ctx)
        group_e_rank(ctx)
        group_f_render(ctx)
        group_g_probe(ctx)
        group_h_rulings(ctx)
        group_i_metric(ctx)
        group_j_filter(ctx)
        group_k_isomorphic(ctx)
        group_l_runtime(ctx)
        group_m_platform_and_regressions(ctx)
        group_n_draft(ctx)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in _RESULTS if not r["ok"]]
    return _RESULTS, failed


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="selftest.py",
        description="viral-teardown-writer 内置回归用例集（synthetic 夹具，平台真实域名）",
        epilog="退出码：0 全过 / 1 有用例失败。")
    parser.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    parser.add_argument("--verbose", action="store_true", help="打印每个用例明细")
    args = parser.parse_args(argv)

    results, failed = run_all(verbose=args.verbose)
    total = len(results)

    if args.json:
        print(json.dumps({
            "synthetic_fixtures": True,
            "total": total,
            "passed": total - len(failed),
            "failed": len(failed),
            "failures": failed,
        }, ensure_ascii=False, indent=2))
    else:
        print("夹具声明：synthetic=true（平台真实域名 + 构造数值，是否用构造样本自证由 QA 裁决）")
        for r in results:
            if args.verbose or not r["ok"]:
                mark = "PASS" if r["ok"] else "FAIL"
                line = "[{}] {}".format(mark, r["name"])
                if not r["ok"]:
                    line += "\n       {}".format(r["detail"])
                print(line)
        print("")
        print("selftest: {} / {} 通过, {} 失败".format(
            total - len(failed), total, len(failed)))
    return EXIT_OK if not failed else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
