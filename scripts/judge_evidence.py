#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""judge_evidence.py -- 阶段 2：榜位解析、指标退化、中位数、3 倍阈值、A/B/C 分档

职责：读 run-plan.json → 榜位解析 → 指标退化 → 同账号近 N 条中位数 →
      3 倍阈值（含等号，纯整数比较）→ A/B/C 分档 → 条件①②合成 →
      生成每条的证据形态与参与求和项清单 -> judged.json

三问裁决（蓝图 §6.3）：
  确定性 ✅ 榜位解析、指标优先级、排序取值、中位数、阈值比值全是可枚举计算；
          中位数与阈值是数学，不含判断。
  理解   ❌ 不做「这算不算明显高于」的主观裁量——阈值 3 倍已固定为常量。
  失败   ✅ 偶数条、空样本、中位数为 0、指标全缺各有确定分支。

CLI:
    python3 judge_evidence.py --plan run-plan.json [--out judged.json] [--json]

退出码：0 正常 / 1 未预期错误 / 2 用法或输入结构错误。
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import vpf_common as V  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2


def load_plan(path):
    """读 run-plan.json。结构破损 → VpfError 并指出路径。"""
    p = pathlib.Path(str(path))
    if not p.exists():
        raise V.VpfError(
            "找不到 run-plan.json：{}\n"
            "请先运行 scripts/normalize_feed.py 生成。".format(path)
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise V.VpfError("run-plan.json 不是合法 JSON：{}\n{}".format(path, exc))
    if not isinstance(data, dict):
        raise V.VpfError(
            "run-plan.json 根节点必须是对象，实际是 {}。".format(type(data).__name__))
    for key in ("feed_state", "branch", "normalized_items"):
        if key not in data:
            raise V.VpfError(
                "run-plan.json 缺少字段 '{}'（{}）。\n"
                "期望它由 scripts/normalize_feed.py 生成，不要手工构造。".format(key, path))
    if not isinstance(data["normalized_items"], list):
        raise V.VpfError(
            "run-plan.normalized_items 必须是数组，实际是 {}（{}）。".format(
                type(data["normalized_items"]).__name__, path))
    return data


# ------------------------------------------------------------ 指标退化 --


def pick_metric(item, baseline_entries):
    """指标退化优先级（Q3 / F4）：play → like → collect。

    取第一个「条目侧 EXACT 且基线有同指标 EXACT 列」的指标；
    若基线全缺，退化为「条目侧第一个 EXACT」；
    再退化为「条目侧第一个 VERBATIM」（照抄展示，不判定倍数）；
    全缺 → None（只走榜单）。
    """
    order = V.METRIC_DEGRADE_ORDER
    exact_candidates = [k for k in order
                        if item["metrics"].get(k, {}).get("state") == V.STATE_EXACT]
    baseline_metrics = set()
    for ent in baseline_entries or []:
        for mkey, node in (ent.get("metric_values") or {}).items():
            raw = V.leaf_value(node)
            state, _, _ = V.normalize_metric(raw)
            if state == V.STATE_EXACT:
                baseline_metrics.add(mkey)

    for key in exact_candidates:
        if key in baseline_metrics:
            return key
    if exact_candidates:
        return exact_candidates[0]
    for key in order:
        if item["metrics"].get(key, {}).get("state") == V.STATE_VERBATIM:
            return key
    return None


def compute_baseline(baseline_entries, metric):
    """同账号近 N 条同指标中位数。只收 EXACT 项（VERBATIM/UNPARSABLE 不入基线）。

    截断在这里兜底（QA D-08）：`load_baselines` 也会截，但那是 CLI 路径。
    直调 `run()` 时只有本函数这道防线，否则 n=15 会进证据文本，违反 F3
    「取最近 10 条」。两处都截是纵深防御，消除「谁负责截断」的分歧。

    返回 (n, num, den, exact_text) 或 None（无基线资格）。
    """
    if not baseline_entries:
        return None
    xs = []
    # 基线按时间「新到旧」排列（feed-schema.md 已写明该约定），取前 N 条即最近 N 条
    for ent in list(baseline_entries)[:V.BASELINE_MAX_ENTRIES]:
        node = (ent.get("metric_values") or {}).get(metric)
        raw = V.leaf_value(node)
        if raw is None:
            continue
        state, value, _ = V.normalize_metric(raw)
        if state == V.STATE_EXACT and value is not None:
            xs.append(value)
    num, den, text = V.median_exact(xs)
    if num is None:
        return None
    return {"n": len(xs), "num": num, "den": den, "text": text}


# ------------------------------------------------------- 互动总量与求和项 --


def compute_engagement(metrics):
    """互动总量 = 播放 + 点赞 + 收藏 + 评论，只累加 EXACT 项（无权重系数）。

    VERBATIM 项从求和中剔除并进 excluded_terms（D4）；
    UNPARSABLE 项计 0 且标「未获取」，不进 sum_terms 也不进 excluded_terms
    （它连数值都没有，列出无意义）。
    """
    total = 0
    sum_terms = []
    excluded_terms = []
    missing_terms = []
    for key in V.METRIC_KEYS:
        node = metrics.get(key) or {}
        state = node.get("state")
        raw = node.get("raw")
        label = V.METRIC_LABELS[key]
        if state == V.STATE_EXACT:
            total += node.get("value") or 0
            sum_terms.append({"metric": key, "label": label, "raw": raw,
                              "value": node.get("value")})
        elif state == V.STATE_VERBATIM:
            excluded_terms.append({"metric": key, "label": label, "raw": raw,
                                   "reason": "原文含模糊修饰，未计入互动总量"})
        else:
            missing_terms.append({"metric": key, "label": label,
                                  "display": V.MISSING_NUM})
    return total, sum_terms, excluded_terms, missing_terms


# --------------------------------------------------------- 证据形态拼装 --


def compose_evidence(item, metric_key, baseline, ratio_ok, engagement_total,
                     sum_terms, excluded_terms):
    """按需求 6.3 的四种形态拼装「凭什么算爆款」。返回 (template_id, text)。

    形态选择严格按蓝图 §8.5 的判定顺序短路：
      1. A 档且有榜位          -> 形态 1 榜单形态（带名次）
      2. 指标缺失 / UNPARSABLE -> 形态 4「未获取」
      3. 无基线 NO_BASELINE    -> 形态 1 / 2
      4. 倍数通过              -> 形态 3 相对比较形态
      5. A / B 档              -> 形态 1 / 2
      6. 其余（C 档）          -> 形态 4「未获取」

    公理 1：五字段结构与证据模板只在这里拼装一次，D1/D2 两条通道共用，
    因此不可能出现「喂料通道一种格式、外网通道另一种格式」（验收 E10）。

    数值一律回写该字段 raw 原文（含 `约` / `+` / `w` / `万` 等一切原文形态），
    中位数用精确值；禁止由归一化整数反推展示（裁决 D4）。
    """
    tier = item["tier"]
    rank_pos = item.get("rank_pos")
    rank_label = item.get("rank_label_norm") or "热门榜"

    metric_node = item["metrics"].get(metric_key) if metric_key else None
    metric_raw = metric_node.get("raw") if metric_node else None
    metric_state = metric_node.get("state") if metric_node else None
    has_displayable = metric_state in (V.STATE_EXACT, V.STATE_VERBATIM)
    label = V.METRIC_LABELS[metric_key] if metric_key else None

    def rank_form():
        """形态 1：榜单形态。带榜位写名次，不带榜位只写榜单名。"""
        if rank_pos is not None:
            return "rank", "在{}（榜单名「第 {}」位）".format(rank_label, rank_pos)
        return "rank", "在{}".format(rank_label)

    def absolute_form():
        """形态 2：绝对数值形态。VERBATIM 额外标注不参与倍数判定。"""
        text = "{} {}".format(label, metric_raw)
        if metric_state == V.STATE_VERBATIM:
            text += "（该值含模糊修饰，不参与倍数判定）"
        return "absolute", text

    # 1) A 档且有榜位 -> 榜单形态
    if tier == "A" and rank_pos is not None:
        return rank_form()
    # 2) 无可展示指标 -> 形态 4
    if not has_displayable:
        return "none", V.MISSING_ANY
    # 3) 无基线 -> 形态 1 / 2（此时绝不能出现「中位数」「倍」字样）
    if baseline is None:
        return rank_form() if tier == "A" else absolute_form()
    # 4) 倍数通过且被评指标为 EXACT -> 相对比较形态
    #    被评指标若是 VERBATIM，拿模糊数算比值等于变相估算（踩 N6），故不参与
    if ratio_ok and metric_state == V.STATE_EXACT:
        text = "{} {}，该账号近 {} 条{}中位数 {}（≥{} 倍，判定为爆款）".format(
            label, metric_raw, baseline["n"], label, baseline["text"], V.RATIO_FACTOR)
        return "relative", text
    # 5) A / B 档 -> 形态 1 / 2
    if tier in ("A", "B"):
        return rank_form() if tier == "A" else absolute_form()
    # 6) 其余（C 档）-> 形态 4
    return "none", V.MISSING_ANY


def append_sum_note(text, template_id, sum_terms, excluded_terms, metric_key):
    """在证据文本里补三类必写信息。

    1) Q8 / G3：列出实际参与求和的项。
    2) 裁决 D4 / H2：被剔除的 VERBATIM 项必须**显式标注未计入总量**。
       缺这个标注会让剔除项与参与项在同一串里并列而无区分，用户会误以为
       `点赞 3.5w+` 参与了互动总量——这正是 Q8「避免口径混淆」要防的事（QA D-01）。
    3) F4：指标退化可见——若本条判定所用指标不是 play（该平台不公开播放量
       或页面未写播放量），必须在证据里标明用的是哪个指标。
    """
    if template_id == "none":
        return text
    notes = []
    if metric_key and metric_key != V.METRIC_DEGRADE_ORDER[0]:
        notes.append("判定所用指标：{}".format(V.METRIC_LABELS[metric_key]))
    if sum_terms:
        notes.append("参与总量：{}".format("、".join(
            "{} {}".format(t["label"], t["raw"]) for t in sum_terms)))
    for t in excluded_terms:
        # 标注文案是**我们写的固定措辞**，不含任何数字，因此不会触发渲染层的
        # 自造修饰词拦截（该拦截只在我们自己生成的字上生效，见 strip_raw_literals）。
        # 被引用的 raw 仍是逐字节照抄的页面原文，其中的 约 / + 属照抄不违规。
        notes.append("{} {}（{}）".format(t["label"], t["raw"], t["reason"]))
    if not notes:
        return text
    return text + "（" + "；".join(notes) + "）"


def judge_item(item, baselines, today):
    """判定单条。返回 judged item。"""
    warnings = []
    author_id = item.get("author_id")
    baseline_entries = (baselines or {}).get(author_id) if author_id else None
    if item.get("baseline_detail") == "baseline_provenance_failed":
        # F14：基线存在但溯源未过 -> 不给基线资格，只走条件①
        baseline_entries = None
        warnings.append({"item_id": item["item_id"], "code": "baseline_rejected",
                         "detail": "基线数据未通过溯源核验，该条只走榜单判定"})

    total, sum_terms, excluded_terms, missing_terms = compute_engagement(item["metrics"])

    metric_key = pick_metric(item, baseline_entries)
    baseline = None
    ratio_ok = False
    if metric_key:
        baseline = compute_baseline(baseline_entries, metric_key)
        if baseline is None:
            warnings.append({"item_id": item["item_id"], "code": "no_baseline",
                             "detail": "无同账号基线，只走榜单判定，不写相对结论"})
        else:
            node = item["metrics"].get(metric_key) or {}
            if node.get("state") != V.STATE_EXACT:
                warnings.append({
                    "item_id": item["item_id"], "code": "verbatim_no_ratio",
                    "detail": "被评指标含模糊修饰，不参与倍数判定（避免变相估算）"})
            elif baseline["num"] == 0:
                warnings.append({"item_id": item["item_id"], "code": "median_zero",
                                 "detail": "基线中位数为 0，不写相对结论"})
            else:
                ratio_ok = V.ratio_passed(node.get("value"), baseline["num"], baseline["den"])

    tier = item["tier"]
    template_id, evidence_text = compose_evidence(
        item, metric_key, baseline, ratio_ok, total, sum_terms, excluded_terms)
    evidence_text = append_sum_note(evidence_text, template_id, sum_terms,
                                    excluded_terms, metric_key)

    # NO_BASELINE 时证据文本不得出现「中位数」「倍」字样（F5 / C7）
    if baseline is None and ("中位数" in evidence_text or "倍" in evidence_text):
        raise AssertionError("内部一致性错误：无基线却写出了相对结论")

    publish_iso = V.parse_publish_time(item.get("publish_time_raw"), today)

    return {
        "index": item["index"],
        "item_id": item["item_id"],
        "title_raw": item["title_raw"],
        "url_raw": item["url_raw"],
        "url_host": item.get("url_host"),
        "url_is_shortlink": item.get("url_is_shortlink", False),
        "platform_field": item["platform_field"],
        "tier": tier,
        "tier_rank": item["tier_rank"],
        "rank_pos": item.get("rank_pos"),
        "rank_label_norm": item.get("rank_label_norm"),
        "metric_used": metric_key,
        "metric_raw": (item["metrics"].get(metric_key) or {}).get("raw") if metric_key else None,
        "baseline": baseline,
        "ratio_pass": ratio_ok if baseline else None,
        "engagement_total": total,
        "sum_terms": sum_terms,
        "excluded_terms": excluded_terms,
        "missing_terms": missing_terms,
        "metrics": item["metrics"],
        "publish_iso": publish_iso,
        "caption_raw": item.get("caption_raw"),
        "evidence_template_id": template_id,
        "evidence_text": evidence_text,
        "_warnings": warnings,
    }


def run(plan_path, baselines, today):
    plan = load_plan(plan_path)
    items = plan["normalized_items"]
    judged = []
    warnings = []
    for item in items:
        result = judge_item(item, baselines, today)
        warnings.extend(result.pop("_warnings", []))
        judged.append(result)
    return {
        "schema_version": "judged.v1",
        "today": today.isoformat(),
        "feed_state": plan["feed_state"],
        "branch": plan["branch"],
        "baseline_required_for_sufficiency": plan.get("baseline_required_for_sufficiency"),
        # blockers 必须原样透传：0 条时的原因说明由它渲染（矩阵第 25 行），
        # 缺失会导致阶段 3 拿不到「缺什么 / 需要喂什么」而退化成一句话。
        "blockers": plan.get("blockers"),
        "items": judged,
        "warnings": warnings,
    }


def load_baselines(path):
    """读 feed.json 的 baselines 段（阶段 2 算中位数要用）。

    返回 {author_id: [entry, ...]}，entry 形如
        {"metric_values": {"like": {"raw": "620", "src": "L88"}}}
    这里把 block 拆成 entries 列表并做上限截断，使下游只面对一种形状。
    """
    if not path:
        return {}
    p = pathlib.Path(str(path))
    if not p.exists():
        raise V.VpfError(
            "找不到 baselines 来源文件：{}\n"
            "中位数计算需要账号主页基线数据；不给 --baselines 时所有条目只走榜单判定"
            "（这是合法降级，不是错误）。".format(path)
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise V.VpfError("baselines 来源不是合法 JSON：{}\n{}".format(path, exc))
    if not isinstance(data, dict):
        raise V.VpfError("baselines 来源根节点必须是对象（{}）。".format(path))
    node = data.get("baselines")
    if node is None:
        return {}
    if not isinstance(node, dict):
        raise V.VpfError("feed.baselines 必须是对象（{}）。".format(path))
    out = {}
    for author, block in node.items():
        if not isinstance(block, dict):
            raise V.VpfError(
                "feed.baselines['{}'] 必须是对象，实际是 {}。".format(
                    author, type(block).__name__))
        entries = block.get("entries") or []
        if not isinstance(entries, list):
            raise V.VpfError(
                "feed.baselines['{}'].entries 必须是数组，实际是 {}。".format(
                    author, type(entries).__name__))
        # 只取最近 N 条（Q3 口径），截取是确定性的
        out[author] = entries[:V.BASELINE_MAX_ENTRIES]
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="judge_evidence.py",
        description="阶段 2：榜位解析 / 指标退化 / 中位数 / 3 倍阈值 -> judged.json",
        epilog="退出码：0 正常 / 1 未预期错误 / 2 用法或输入结构错误。")
    parser.add_argument("--plan", required=True, help="run-plan.json 路径")
    parser.add_argument("--baselines", help="feed.json 路径（取其中的 baselines 段算中位数）")
    parser.add_argument("--today", help="今天日期 YYYY-MM-DD（与阶段 1 同一个值）")
    parser.add_argument("--out", help="judged.json 输出路径（缺省只写 stdout）")
    parser.add_argument("--json", action="store_true", help="stdout 输出紧凑 JSON")
    args = parser.parse_args(argv)

    try:
        today = V.parse_today(args.today)
        baselines = load_baselines(args.baselines)
        judged = run(args.plan, baselines, today)
        text = V.emit(judged, compact=args.json, out=args.out)
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
    sys.stderr.write("[judge_evidence] items=%d with_baseline=%d\n" % (
        len(judged["items"]),
        len([i for i in judged["items"] if i.get("baseline")]))
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
