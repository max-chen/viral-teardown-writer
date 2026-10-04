#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""normalize_feed.py -- 阶段 1：喂料归一 + 充分性判定 + 五序分支决策

职责：读 feed.json（模型转写产物）+ 原始喂料文本，做
  溯源核验 → 单位归一 → 字段完整性判定 → 喂料充分性判定 → 五序分支决策
  → 产出 run-plan.json。

三问裁决（蓝图 §6.2）：
  确定性 ✅ 字段存在性、子串核验、枚举校验、计数、分支真值表全部可枚举。
  理解   ❌ 明确不做「这坨文字看起来够不够」这类判断——那正是 E7 要消灭的东西。
  失败   ✅ 三类畸形输入各有确定错误码与修法，全部 exit 2。

CLI:
    python3 normalize_feed.py --feed <feed.json> --raw <喂料原文.txt>
                             [--platform <枚举>] [--today YYYY-MM-DD]
                             [--baseline-required] [--out run-plan.json] [--json]

退出码：0 正常（含 0 条、含 branch=3|5）/ 1 未预期错误 / 2 用法或输入结构错误。
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import vpf_common as V  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2


# ------------------------------------------------------------------ schema --

FEED_REQUIRED_TOP = ("schema_version", "items")
ITEM_REQUIRED = ("title", "url")
ITEM_METRIC_KEYS = V.METRIC_KEYS


def load_feed(path):
    """读 feed.json。结构不符 → VpfError（打印期望字段清单 + 首个出错路径）。"""
    p = pathlib.Path(str(path))
    if not p.exists():
        raise V.VpfError(
            "找不到喂料文件：{}\n"
            "喂料文件应由模型按 references/feed-schema.md 转写为 feed.json v1，"
            "本脚本只做确定性核验，不猜不填。".format(path)
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise V.VpfError("喂料文件不是合法 JSON：{}\nJSON 解析失败：{}".format(path, exc))
    if not isinstance(data, dict):
        raise V.VpfError(
            "喂料文件根节点必须是对象，实际是 {}。\n"
            "期望结构：{{\"schema_version\": \"feed.v1\", \"items\": [...]}}".format(
                type(data).__name__)
        )
    for key in FEED_REQUIRED_TOP:
        if key not in data:
            raise V.VpfError(
                "喂料文件缺少顶层字段 '{}'（{}）。\n"
                "期望顶层字段清单：{}".format(key, path, " / ".join(FEED_REQUIRED_TOP))
            )
    if not isinstance(data["items"], list):
        raise V.VpfError(
            "feed.items 必须是数组，实际是 {}（{}）。".format(
                type(data["items"]).__name__, path)
        )
    return data


def read_leaf(item, key, ctx):
    """取叶子值 raw 字符串。不存在 → None；结构错 → VpfError 带路径。"""
    if key not in item:
        return None
    try:
        return V.leaf_value(item[key])
    except V.VpfError as exc:
        raise V.VpfError("{}：{}".format(ctx, exc))


# -------------------------------------------------------- 字段级判定 F1~F14 --


def normalize_item(item, index, corpus, platform_ctx, baselines):
    """按字段级判定表处理单条。返回 (normalized, dropped|None, warnings)。

    normalized 字段含义：
      title/url            入场券（F1/F2），缺失即丢弃
      rank_label/rank_pos  F3/F4，缺失降级为「无榜标记/无榜位」
      metrics[k]           F5~F8，UNPARSABLE -> 未获取计 0；
                           VERBATIM -> 照抄但不计入总量（D4）
      publish_time         F9，溯源核验；日期格式归一在阶段 2 用 --today 做
      caption              F10，核验失败 -> 无文案
      author_id            F11，缺失 -> 无基线资格
      flags                F12/F13，模型标 true 即丢弃（fail-safe）
    """
    ctx = "items[%d]" % index
    warnings = []

    if not isinstance(item, dict):
        raise V.VpfError(
            "{}：条目必须是对象，实际是 {}。\n"
            "期望条目字段清单：{} + metrics{} + content_flags".format(
                ctx, type(item).__name(),
                " / ".join(ITEM_REQUIRED),
                "{play,like,collect,comment}")
        )

    item_id = item.get("item_id") if isinstance(item.get("item_id"), str) else "item#%d" % index

    # -- F12 / F13：广告硬广 / 色情，模型语义判定 + fail-safe 丢弃 --
    flags = item.get("content_flags") or {}
    if not isinstance(flags, dict):
        raise V.VpfError(
            "{}.content_flags：必须是对象，实际是 {}。\n"
            "期望键：ad_hard_sell / porn（布尔）+ 对应 *_evidence（raw+src）。".format(
                ctx, type(flags).__name__)
        )
    for flag_key, flag_label in (("ad_hard_sell", "广告/带货硬广"), ("porn", "色情/擦边引流")):
        if flags.get(flag_key) is True:
            evidence = flags.get(flag_key + "_evidence")
            ev_raw = None
            if isinstance(evidence, dict):
                ev_raw = evidence.get("raw")
            if not ev_raw:
                # fail-safe 丢弃本身优先（不因缺证据放过），但记一条警告：
                # 没有证据片段时 QA 无法复核模型是否滥用了删除权（QA D-06）。
                warnings.append({
                    "item_id": item_id, "field": "content_flags." + flag_key,
                    "code": "drop_without_evidence",
                    "detail": "模型标记为%s但未给证据片段，丢弃仍执行（fail-safe），"
                              "该条复核能力下降" % flag_label,
                })
            return None, {
                "item_id": item_id,
                "reason": "content_flag:%s" % flag_key,
                "reason_text": "模型判定为%s，fail-safe 丢弃" % flag_label,
                "evidence_raw": ev_raw,
                "evidence_verified": bool(ev_raw) and V.verify_raw(ev_raw, corpus),
            }, warnings

    # -- F1：标题（入场券 1/2） --
    title_raw = read_leaf(item, "title", ctx)
    title_ok = bool(title_raw) and V.verify_raw(title_raw, corpus)
    if not title_ok:
        if title_raw:
            warnings.append({"item_id": item_id, "field": "title",
                             "code": "provenance_failed",
                             "detail": "title.raw 不是喂料原文子串，按缺失处理"})
        return None, {
            "item_id": item_id,
            "reason": "missing_title",
            "reason_text": "缺标题（入场券 1/2）",
            "evidence_raw": title_raw,
            "evidence_verified": bool(title_ok),
        }, warnings

    # -- F2：地址（入场券 2/2）。裁决 D2：域名不设准入门槛，短链照收 --
    url_raw = read_leaf(item, "url", ctx)
    url_ok = False
    url_reason = "missing_url"
    if url_raw and V.verify_raw(url_raw, corpus):
        parsed = V.parse_url(url_raw)
        if parsed is None:
            url_reason = "url_scheme_invalid"
        elif V.url_has_credential_query(parsed):
            url_reason = "url_credential_query"
        else:
            url_ok = True
            url_reason = ""
    elif url_raw:
        url_reason = "provenance_failed"
        warnings.append({"item_id": item_id, "field": "url",
                         "code": "provenance_failed",
                         "detail": "url.raw 不是喂料原文子串，按缺失处理"})
    if not url_ok:
        return None, {
            "item_id": item_id,
            "reason": url_reason,
            "reason_text": {
                "missing_url": "缺地址（入场券 2/2）",
                "provenance_failed": "地址未通过溯源核验（入场券 2/2）",
                "url_scheme_invalid": "地址协议不是 http/https（入场券 2/2）",
                "url_credential_query": "地址含凭据型 query，违反不猜链接红线（入场券 2/2）",
            }.get(url_reason, "地址不合法（入场券 2/2）"),
            "evidence_raw": url_raw,
            "evidence_verified": bool(url_raw) and V.verify_raw(url_raw, corpus),
        }, warnings

    parsed_url = V.parse_url(url_raw)

    # -- 平台收敛：host 明确属于**另一个**平台时丢弃（QA D-03） --
    # 平台是用户回显确认过的必填参数（需求 §4.1），C8 要求内容确实属于该平台。
    # 只在 host **能明确归属为别的平台**时丢弃；host 无法归属（url.cn / t.cn 类
    # 短链）时一律保留并把平台字段填「未获取」——裁决 D2 明文「推断不出填未获取，
    # 不猜」，且 D2 放行条件明确要求这类条目不得因此丢失。
    if platform_ctx:
        host_platform = V.platform_by_host(parsed_url["host"])
        if host_platform is not None and host_platform != platform_ctx:
            return None, {
                "item_id": item_id,
                "reason": "platform_mismatch",
                "reason_text": "地址归属平台（%s）与用户指定平台（%s）不一致" % (
                    V.PLATFORM_ENUM[host_platform], V.PLATFORM_ENUM[platform_ctx]),
                "evidence_raw": url_raw,
                "evidence_verified": True,
            }, warnings

    # -- F3：榜单位字样 --
    rank_label_node = read_leaf(item, "rank_label", ctx)
    rank_label_raw = None
    rank_label_state = "NONE"
    rank_label_norm = None
    if rank_label_node:
        if V.verify_raw(rank_label_node, corpus):
            rank_label_raw = rank_label_node
            rank_label_norm, rank_label_state = V.normalize_rank_label(rank_label_node)
            if rank_label_state == "NONE":
                warnings.append({"item_id": item_id, "field": "rank_label",
                                 "code": "rank_label_not_allowlisted",
                                 "detail": "榜位字样不在白名单内，按无榜标记处理"})
        else:
            warnings.append({"item_id": item_id, "field": "rank_label",
                             "code": "provenance_failed",
                             "detail": "rank_label.raw 不是喂料原文子串，按无榜标记处理"})

    # -- F4：榜位 --
    rank_pos_node = read_leaf(item, "rank_pos", ctx)
    rank_pos = None
    if rank_pos_node:
        if V.verify_raw(rank_pos_node, corpus):
            rank_pos = V.normalize_rank_pos(rank_pos_node)
            if rank_pos is None:
                warnings.append({"item_id": item_id, "field": "rank_pos",
                                 "code": "rank_pos_invalid",
                                 "detail": "榜位不是 1~4 位纯数字，按无榜位处理"})
        else:
            warnings.append({"item_id": item_id, "field": "rank_pos",
                             "code": "provenance_failed",
                             "detail": "rank_pos.raw 不是喂料原文子串，按无榜位处理"})

    # -- F5~F8：四项指标 --
    metrics_node = item.get("metrics")
    if metrics_node is None:
        metrics_node = {}
    if not isinstance(metrics_node, dict):
        raise V.VpfError(
            "{}.metrics：必须是对象或缺省，实际是 {}。\n"
            "期望键：{}。".format(ctx, type(metrics_node).__name__,
                                  " / ".join(ITEM_METRIC_KEYS))
        )
    metrics = {}
    has_metric = False
    for key in ITEM_METRIC_KEYS:
        raw = read_leaf(metrics_node, key, "%s.metrics" % ctx)
        state, value, verbatim = V.normalize_metric(raw) if raw else (
            V.STATE_UNPARSABLE, None, None)
        if raw and not V.verify_raw(raw, corpus):
            warnings.append({"item_id": item_id, "field": "metrics.%s" % key,
                             "code": "provenance_failed",
                             "detail": "指标原文未通过溯源核验，按「%s」处理" % V.MISSING_NUM})
            state, value, verbatim = V.STATE_UNPARSABLE, None, None
        elif raw and state == V.STATE_VERBATIM:
            warnings.append({"item_id": item_id, "field": "metrics.%s" % key,
                             "code": "verbatim_excluded_from_sum",
                             "detail": "原文含模糊修饰，照抄展示但不计入互动总量"})
        if state in (V.STATE_EXACT, V.STATE_VERBATIM):
            has_metric = True
        metrics[key] = {
            "raw": verbatim,
            "state": state,
            "value": value,
            "display": verbatim if state in (V.STATE_EXACT, V.STATE_VERBATIM) else V.MISSING_NUM,
        }

    # -- F9：发布时间（只做溯源核验；日期格式归一在阶段 2 用 --today 做） --
    publish_node = read_leaf(item, "publish_time", ctx)
    publish_raw = None
    if publish_node:
        if V.verify_raw(publish_node, corpus):
            publish_raw = publish_node
        else:
            warnings.append({"item_id": item_id, "field": "publish_time",
                             "code": "provenance_failed",
                             "detail": "publish_time.raw 不是喂料原文子串，按缺失处理"})

    # -- F10：文案 --
    caption_node = read_leaf(item, "caption", ctx)
    caption = None
    if caption_node and V.verify_raw(caption_node, corpus):
        caption = caption_node
    elif caption_node:
        warnings.append({"item_id": item_id, "field": "caption",
                         "code": "provenance_failed",
                         "detail": "caption.raw 不是喂料原文子串，按「%s」处理" % V.MISSING_TEXT})

    # -- F11：作者 ID（缺失 -> 无基线资格，只走条件①） --
    author_node = read_leaf(item, "author_id", ctx)
    author_id = None
    if author_node and V.verify_raw(author_node, corpus):
        author_id = author_node
    elif author_node:
        warnings.append({"item_id": item_id, "field": "author_id",
                         "code": "provenance_failed",
                         "detail": "author_id 未通过溯源核验，该条无基线资格"})

    # -- F14：基线齐备性 --
    baseline_ok = False
    baseline_detail = "no_baseline_block"
    if author_id is None:
        baseline_detail = "no_author_id"
    elif author_id not in baselines:
        baseline_detail = "author_not_in_baselines"
    else:
        entries = baselines[author_id]
        if not entries:
            baseline_detail = "baseline_empty"
        else:
            bad = False
            for ent in entries:
                for mkey, node in (ent.get("metric_values") or {}).items():
                    mraw = V.leaf_value(node)
                    if mraw is None or not V.verify_raw(mraw, corpus):
                        bad = True
            baseline_ok = not bad
            baseline_detail = "ok" if baseline_ok else "baseline_provenance_failed"

    tier, tier_rank = V.assign_tier(rank_label_state, rank_pos, has_metric)

    platform_field, platform_source = V.resolve_platform_field(url_raw, platform_ctx)

    normalized = {
        "index": index,
        "item_id": item_id,
        "title_raw": title_raw,
        "url_raw": url_raw,
        "url_host": parsed_url["host"],
        "url_is_shortlink": V.is_shortlink_host(parsed_url["host"]),
        "platform_field": platform_field,
        "platform_source": platform_source,
        "rank_label_raw": rank_label_raw,
        "rank_label_norm": rank_label_norm,
        "rank_label_state": rank_label_state,
        "rank_pos": rank_pos,
        "metrics": metrics,
        "publish_time_raw": publish_raw,
        "publish_iso": None,   # 阶段 2 按 --today 归一，阶段 1 只透传原文
        "caption_raw": caption,
        "author_id": author_id,
        "baseline_ok": baseline_ok,
        "baseline_detail": baseline_detail,
        "tier": tier,
        "tier_rank": tier_rank,
        "judgeable": bool(
            rank_label_state == "ALLOW" or rank_pos is not None or has_metric
        ),
    }
    return normalized, None, warnings


def extract_baselines(data):
    """读 baselines：{author_id: [{metric_values: {play:{raw,src}, ...}}, ...]}。

    结构错 → VpfError。溯源核验在 normalize_item 的 F14 里做（按条目作者）。
    """
    node = data.get("baselines")
    if node is None:
        return {}
    if not isinstance(node, dict):
        raise V.VpfError(
            "feed.baselines 必须是对象（键为 author_id），实际是 {}。".format(
                type(node).__name__)
        )
    out = {}
    for author, block in node.items():
        if not isinstance(block, dict):
            raise V.VpfError("feed.baselines['{}'] 必须是对象，实际是 {}。".format(
                author, type(block).__name__))
        entries = block.get("entries")
        if entries is None:
            entries = []
        if not isinstance(entries, list):
            raise V.VpfError(
                "feed.baselines['{}'].entries 必须是数组，实际是 {}。".format(
                    author, type(entries).__name__)
            )
        if len(entries) > V.BASELINE_MAX_ENTRIES:
            # 只取最近 N 条（Q3 口径）。截取是确定性的，不影响可复算。
            entries = entries[:V.BASELINE_MAX_ENTRIES]
        out[author] = entries
    return out


# ------------------------------------------------- 喂料级状态 + 五序分支 --


def decide_feed_state(normalized_items, baseline_required):
    """喂料充分性判定（蓝图 §4.4）。

    F0 无有效喂料：n_存活 == 0
    F1 充分    ：n_可判定 >= 1
    F2 不足    ：n_存活 >= 1 且 n_可判定 == 0
    """
    alive = [it for it in normalized_items if it is not None]
    if not alive:
        return V.FEED_STATE_INVALID, alive, 0
    judgeable = [it for it in alive if it["judgeable"]]
    if baseline_required:
        judgeable = [it for it in judgeable if it["baseline_ok"]]
    if judgeable:
        return V.FEED_STATE_SUFFICIENT, alive, len(judgeable)
    return V.FEED_STATE_INSUFFICIENT, alive, 0


def decide_branch(feed_state, d2_available):
    """五序分支决策真值表（蓝图 §5）。按序短路，编号与需求书一一对应。

    返回 (branch, d2_allowed, d2_scope, reason)。
    """
    if feed_state == V.FEED_STATE_SUFFICIENT:
        return 1, False, None, "喂料充分，仅用喂料出结果（序 1 命中，禁止任何网络调用）"
    if feed_state == V.FEED_STATE_INSUFFICIENT:
        if d2_available:
            return 2, True, "fill_evidence_gap", "喂料不足且探测到 D2 工具，允许仅补证据缺口"
        return 3, False, None, "喂料不足且无 D2 工具，输出 0 条并说明缺什么"
    if d2_available:
        return 4, True, "full_search", "无有效喂料且探测到 D2 工具，允许自主检索/翻榜/读主页"
    return 5, False, None, "无有效喂料且无 D2 工具，输出 0 条并说明缺什么"


def build_blockers(dropped, alive, feed_state):
    """0 条时的原因说明（蓝图 §5）。由代码从字段级统计渲染，模型不得自撰。

    只说「缺什么」与「需要喂什么」，不给方法指导（N5 / E6）。
    """
    reason_counts = {}
    for d in dropped:
        reason_counts[d["reason"]] = reason_counts.get(d["reason"], 0) + 1

    missing_fields = []
    if reason_counts.get("missing_title"):
        missing_fields.append({
            "field": "标题", "count": reason_counts["missing_title"],
            "need": "每条内容的标题原文（页面上写着的那句，不改写）"})
    if reason_counts.get("missing_url") or reason_counts.get("url_scheme_invalid") \
            or reason_counts.get("url_credential_query") or reason_counts.get("provenance_failed"):
        n = sum(reason_counts.get(k, 0) for k in
                ("missing_url", "url_scheme_invalid", "url_credential_query", "provenance_failed"))
        missing_fields.append({
            "field": "地址", "count": n,
            "need": "每条内容的原始链接（http/https 开头，短链照收，不展开不改写）"})
    no_evidence = len([it for it in alive if not it["judgeable"]])
    if no_evidence:
        missing_fields.append({
            "field": "榜单标记或可见数字", "count": no_evidence,
            "need": "榜单名与名次（如「第 3 位」），或页面上写着的播放/点赞/收藏数字"})
    dropped_by_flag = sum(v for k, v in reason_counts.items() if k.startswith("content_flag:"))
    if dropped_by_flag:
        missing_fields.append({
            "field": "非广告硬广且非色情的内容", "count": dropped_by_flag,
            "need": "去掉带货硬广与色情内容后的条目"})

    if not missing_fields:
        missing_fields.append({
            "field": "可判定爆款的页面证据", "count": 0,
            "need": "至少一条带标题、地址，且带榜单标记或一个可见数字的内容"})

    return {
        "feed_state": feed_state,
        "missing_fields": missing_fields,
        "accepted_fields": [
            "平台链接原文（短链照收，不展开、不改写）",
            "页面截图里的文字（榜单名与名次、标题、链接、可见数字）",
            "复制粘贴的列表内容（含每条标题、链接与页面上写着的数字）",
            "账号主页近期列表内容（用于同账号中位数比较）",
        ],
        "need": "把上面「缺什么」里列的字段按原样喂过来；本技能只认页面上写着的，不估算、不补全。",
    }


# ------------------------------------------------------------------- main --


def run(feed_path, raw_path, platform, today, baseline_required, probe_path):
    """执行阶段 1，返回 run-plan 字典。失败抛 VpfError。"""
    corpus = V.load_corpus(raw_path)
    data = load_feed(feed_path)
    baselines = extract_baselines(data)

    items = data["items"]
    normalized_items = []
    dropped = []
    warnings = []
    for idx, item in enumerate(items):
        norm, drop, warns = normalize_item(item, idx, corpus, platform, baselines)
        warnings.extend(warns)
        if drop is not None:
            dropped.append(drop)
        else:
            normalized_items.append(norm)

    feed_state, alive, n_judgeable = decide_feed_state(
        normalized_items, baseline_required)

    # -- D2 可用性：优先读 probe 文件；无 probe 文件一律按无工具 --
    probe_result = {"probe_file_present": bool(probe_path), "available": False,
                    "tool": None, "attempts": None, "status": None}
    if probe_path:
        probe_result = load_probe_availability(probe_path)

    branch, d2_allowed, d2_scope, reason = decide_branch(feed_state, probe_result["available"])

    blockers = None
    if branch in (3, 5):
        blockers = build_blockers(dropped, alive, feed_state)

    return {
        "schema_version": "run-plan.v1",
        "today": today.isoformat(),
        "params": {
            "platform": V.PLATFORM_ENUM.get(platform, V.MISSING_NUM) if platform else V.MISSING_NUM,
            "platform_key": platform,
        },
        "baseline_required_for_sufficiency": baseline_required,
        "feed_state": feed_state,
        "branch": branch,
        "branch_reason": reason,
        "d2_allowed": d2_allowed,
        "d2_scope": d2_scope,
        "probe": probe_result,
        "counts": {
            "input": len(items),
            "alive": len(alive),
            "judgeable": n_judgeable,
            "dropped": len(dropped),
        },
        "normalized_items": normalized_items,
        "dropped": dropped,
        "warnings": warnings,
        "blockers": blockers,
    }


def load_probe_availability(probe_path):
    """读 d2_probe.json 的 available 结论（形状校验由 probe_d2.py 负责）。"""
    p = pathlib.Path(str(probe_path))
    if not p.exists():
        raise V.VpfError(
            "找不到 D2 探测结果文件：{}\n"
            "若已探测，请把结论写入该文件（形状见 references/feed-schema.md）；"
            "若未探测，去掉 --probe 参数即可（按无工具处理）。".format(probe_path)
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise V.VpfError("D2 探测结果不是合法 JSON：{}\n{}".format(probe_path, exc))
    if not isinstance(data, dict) or "available" not in data:
        raise V.VpfError(
            "D2 探测结果缺少 available 字段（{}）。\n"
            "期望形状：{{\"available\": true, \"tool\": \"...\", \"attempts\": 1, \"status\": \"ok\"}}。"
            "形状非法不默认 false 继续跑，请先跑 scripts/probe_d2.py 校验。".format(probe_path)
        )
    return {
        "probe_file_present": True,
        "available": bool(data.get("available")),
        "tool": data.get("tool"),
        "attempts": data.get("attempts"),
        "status": data.get("status"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="normalize_feed.py",
        description="阶段 1：喂料归一 + 充分性判定 + 五序分支 -> run-plan.json",
        epilog="退出码：0 正常（含 0 条与 branch=3/5）/ 1 未预期错误 / 2 用法或输入结构错误。")
    parser.add_argument("--feed", required=True, help="feed.json 路径（模型转写产物）")
    parser.add_argument("--raw", required=True, help="原始喂料文本路径（溯源核验基准）")
    parser.add_argument("--platform", help="平台枚举或中文写法（回显确认后的值）")
    parser.add_argument("--today", help="今天日期 YYYY-MM-DD（默认系统当天，测试可注入）")
    parser.add_argument("--baseline-required", action="store_true",
                        help="把「基线齐备」也算作充分性条件（默认 false，裁决 D1）")
    parser.add_argument("--probe", help="d2_probe.json 路径（缺省按无 D2 工具处理）")
    parser.add_argument("--out", help="run-plan.json 输出路径（缺省只写 stdout）")
    parser.add_argument("--json", action="store_true", help="stdout 输出紧凑 JSON")
    args = parser.parse_args(argv)

    try:
        platform = V.normalize_platform_input(args.platform)
        today = V.parse_today(args.today)
        plan = run(args.feed, args.raw, platform, today,
                   args.baseline_required, args.probe)
        text = V.emit(plan, compact=args.json, out=args.out)
    except V.VpfError as exc:
        sys.stderr.write("错误：%s\n" % exc)
        return EXIT_USAGE
    except (OSError, KeyError, TypeError, ValueError) as exc:
        sys.stderr.write("未预期错误：%s: %s\n" % (type(exc).__name__, exc))
        return EXIT_FAIL

    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text + "\n")
    except AttributeError:
        sys.stdout.write(text + "\n")
    # 非判定元数据走 stderr，保证 stdout 可 sha256 比对（C1 / E7）
    sys.stderr.write("[normalize_feed] branch=%s feed_state=%s counts=%s\n" % (
        plan["branch"], plan["feed_state"], json.dumps(plan["counts"], ensure_ascii=False)))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
