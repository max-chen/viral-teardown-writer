#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rank_render.py -- 阶段 3：去重合并、四级排序、截断、五字段渲染

职责：读 judged.json → 同链接去重合并（择一保留证据最强）→ 四级排序键排序 →
      截断 ≤10 条 → 渲染固定五字段块 -> final.md + stdout 机器摘要

三问裁决（蓝图 §6.4）：
  确定性 ✅ 排序键全来自页面可见字段；合并与截断是集合运算；渲染是模板拼装。
  理解   ❌ 不做「哪个更火」的主观比较。
  失败   ✅ 截断、丢弃、渲染缺字段均可预测。

CLI:
    python3 rank_render.py --judged judged.json [--out final.md] [--json]

退出码：0 正常（含 0 条，0 条是合法输出不是失败）/ 1 输出后置断言失败 / 2 结构错误。
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import vpf_common as V  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2

FIELD_NAMES = ("标题", "地址", "平台", "凭什么算爆款", "文案")


def load_judged(path):
    p = pathlib.Path(str(path))
    if not p.exists():
        raise V.VpfError(
            "找不到 judged.json：{}\n"
            "请先运行 scripts/judge_evidence.py 生成。".format(path)
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise V.VpfError("judged.json 不是合法 JSON：{}\n{}".format(path, exc))
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise V.VpfError(
            "judged.json 结构破损（{}）：期望根节点含 items 数组。\n"
            "它应由 scripts/judge_evidence.py 生成。".format(path)
        )
    return data


# ------------------------------------------------------------ 去重与合并 --


def evidence_strength(item):
    """证据强度序（蓝图 §8.4）。逐项比较，取最大者保留。

    (tier_rank, rank_pos is None, engagement_total, publish_ord, caption 长度)
    """
    return (
        item.get("tier_rank", 2),
        0 if item.get("rank_pos") is not None else 1,
        item.get("engagement_total", 0),
        V.date_ordinal(item.get("publish_iso")) or -V.INF,
        len(item.get("caption_raw") or ""),
    )


def dedup(items):
    """同链接合并，逐字节相等（--dedup-key=raw 默认），不做任何规范化。

    理由：N9 禁止改写 URL。漏合并的代价是同内容占两席；错合并的代价是改写链接
    并丢掉真实内容——后者不可接受。

    只择一保留，严禁字段拼接（caption 拼接即改写，踩 N2）。
    裁决 D2：短链不做同源归并（那需要展开跳转），因此短链与其对应的长链
    按 raw 键算两条，允许同内容占 2 席。这是「不猜链接」的必然代价。
    """
    best = {}
    order = []
    merged_away = []
    for it in items:
        key = it.get("url_raw")
        if key is None:
            key = "__no_url__" + str(it.get("index"))
        if key not in best:
            best[key] = it
            order.append(key)
            continue
        incumbent = best[key]
        if evidence_strength(it) > evidence_strength(incumbent):
            merged_away.append({
                "url_raw": key,
                "kept_item_id": it["item_id"],
                "dropped_item_id": incumbent["item_id"],
                "reason": "same_url_merged_keep_stronger_evidence",
            })
            best[key] = it
        else:
            merged_away.append({
                "url_raw": key,
                "kept_item_id": incumbent["item_id"],
                "dropped_item_id": it["item_id"],
                "reason": "same_url_merged_keep_stronger_evidence",
            })
    return [best[k] for k in order], merged_away


# -------------------------------------------------------------- 排序键 --


def sort_key(item):
    """四级排序键（蓝图 §8.2）。全 int，禁止混类型比较。

    1) tier_rank（A=0 B=1 C=2）
    2) 榜位小者在前；A 档无榜位排在有榜位之后
    3) 可见互动总量大者在前
    4) 发布时间新的在前；缺失排并列之后
    5) 标题字典序升序（原串比较，不做任何归一化）
    """
    tier = item.get("tier")
    rank_pos = item.get("rank_pos")
    if tier == "A" and rank_pos is not None:
        pos_key = rank_pos
    else:
        pos_key = V.INF
    total = item.get("engagement_total", 0)
    ordinal = V.date_ordinal(item.get("publish_iso"))
    time_key = -ordinal if ordinal is not None else -V.INF
    return (
        item.get("tier_rank", 2),
        pos_key,
        -total,
        time_key,
        item.get("title_raw") or "",
    )


# -------------------------------------------------------------- 渲染 --


def render_item(item, ordinal):
    """渲染单条五字段块。字段名与顺序固定，缺字段的条目已在渲染前丢弃。

    块首行只放序号，不重复标题——否则标题会出现在块里两次，
    被读成第 6 个字段（违反「每条恰 5 个字段、无多余字段」）。
    """
    title = V.fold_whitespace(item.get("title_raw") or "")
    url = item.get("url_raw") or ""
    platform = item.get("platform_field") or V.MISSING_NUM
    evidence = item.get("evidence_text") or V.MISSING_ANY

    caption_text, truncated = V.truncate_caption(item.get("caption_raw"))
    if not caption_text:
        caption = V.MISSING_TEXT
    else:
        caption = caption_text + ("（已截断，原文超 {} 字）".format(V.CAPTION_MAX_CHARS)
                                  if truncated else "")

    lines = ["第 {} 条".format(ordinal)]
    for name, value in zip(FIELD_NAMES, (title, url, platform, evidence, caption)):
        lines.append("{}：{}".format(name, value))
    return "\n".join(lines)


def render_blockers(blockers, branch):
    """0 条时的原因说明（需求 5.2 序 3/5、E3、E6）。

    只说「缺什么」与「需要喂什么」，不教方法（N5）、不替用户决策（N10）。
    """
    if not blockers:
        return "本次没有可输出的条目：喂料未提供可判定爆款的页面证据，且当前环境无网页读取能力。"
    lines = ["本次没有可输出的条目。"]
    lines.append("")
    lines.append("缺什么：")
    for f in blockers.get("missing_fields", []):
        count = f.get("count", 0)
        prefix = "{} 条缺".format(count) if count else "需要"
        lines.append("  - {}（{}）".format(f.get("field"), prefix))
    lines.append("")
    lines.append("需要你喂什么（以下任一即可）：")
    for a in blockers.get("accepted_fields", []):
        lines.append("  - {}".format(a))
    lines.append("")
    lines.append(blockers.get("need", ""))
    return "\n".join(lines)


def collect_raw_values(items):
    """收集本次输出涉及的全部 raw 原文，供自造修饰词扫描时剔除。

    裁决 D4：禁的是「由我们生成的」修饰词；页面原文里本来存在的 `约` / `+`
    属照抄，不违规。因此必须先把 raw 原文从文本里剔掉，剩下的残余才是
    我们自己写的字。
    """
    values = []
    for it in items:
        for key in ("title_raw", "url_raw", "evidence_text", "caption_raw"):
            if it.get(key):
                values.append(it[key])
        for t in (it.get("sum_terms") or []) + (it.get("excluded_terms") or []):
            if t.get("raw"):
                values.append(t["raw"])
    return values


def post_assertions(text, raw_values):
    """输出后置断言（矩阵第 20 / 24 / 26 行）。任一命中 → 抛 VpfError。

    这是「渲染层禁止自造修饰词」「未获取字面量不被替换」「末尾无总结建议」
    三条硬约束的机器落点。
    """
    # 1) 自造修饰词：剔除 raw 原文后仍命中即违规
    for line in text.split("\n"):
        hits = V.find_self_made_modifier(line, raw_values)
        if hits:
            raise V.VpfError(
                "渲染层自造修饰词（验收 B6 / 裁决 D4 边界）：命中 {}。\n"
                "禁的是由我们生成的「大约 / 预计 / 左右」；页面原文里本来存在的"
                " `约` / `+` 属照抄，不违规。\n命中行：{}".format(hits, line.strip()))

    # 2) 未获取字面量不得被替换成别的写法
    for line in text.split("\n"):
        if "：" not in line:
            continue
        name, _, value = line.strip().partition("：")
        name = name.strip()
        if name not in FIELD_NAMES:
            continue
        bad = V.find_forbidden_literal(value)
        if bad is not None:
            raise V.VpfError(
                "「{}」字段用了禁止的替代写法 '{}'（验收 B8）。\n"
                "未获取的数字必须原样写「{}」，文案缺失写「{}」，"
                "禁止写成 {} 之类。".format(name, bad, V.MISSING_NUM, V.MISSING_TEXT,
                                          " / ".join(V.BANNED_MISSING_ALTERNATIVES)))

    # 3) 末尾禁止总结 / 建议 / 占比统计行
    #    传 raw_values 进去：页面原文里出现「占比 / 共N条 / 命中率」是合法的，
    # 只有我们自己写的汇总行才该被拦（QA D-02）。
    hits = V.find_summary_line(text, raw_values)
    if hits:
        raise V.VpfError(
            "输出末尾出现总结 / 建议 / 占比统计行（验收 B10 / G5）：{}\n"
            "输出末尾禁止附任何总结、规律、建议、下一步提示或占比统计。".format(hits))


def build_final(judged, branch, out_path):
    """主流程：分支闸门 → 去重 → 排序 → 截断 → 渲染 → 后置断言。

    返回 (final_text, summary)。

    分支闸门（蓝图 §5 序 3 / 序 5，验收 E3 / E6 / C4）：
      branch ∈ {3, 5} 表示「没有可用通路」，此时**必须输出 0 条**并说明缺什么。
      哪怕上游传进来若干条目，也一律不输出——否则「喂料不足且无工具」会被
      渲染成看起来有结果的清单，等于凭空补齐（踩 N8 / E3）。
    """
    items = judged.get("items", [])

    if branch in (3, 5):
        body = render_blockers(judged.get("blockers"), branch)
        post_assertions(body, [])
        summary = {
            "item_count": 0,
            "blocked_by_branch": branch,
            "suppressed_items": len(items),
            "dropped_over_limit": 0,
            "merged_away": [],
            "discarded_at_render": [],
            "items": [],
        }
        return body, summary

    merged, merged_away = dedup(items)

    # 渲染前兜底执行入场券（§5.5）：缺标题或缺地址的条目绝不输出半条
    renderable = []
    discarded = []
    for it in merged:
        if not it.get("title_raw") or not it.get("url_raw"):
            discarded.append({"item_id": it.get("item_id"),
                              "reason": "missing_title_or_url_at_render"})
            continue
        renderable.append(it)

    ordered = sorted(renderable, key=sort_key)
    truncated_items = ordered[V.MAX_OUTPUT_ITEMS:]
    kept = ordered[:V.MAX_OUTPUT_ITEMS]

    summary = {
        "item_count": len(kept),
        "dropped_over_limit": len(truncated_items),
        "merged_away": merged_away,
        "discarded_at_render": discarded,
        "items": [],
    }

    if not kept:
        body = render_blockers(judged.get("blockers"), branch)
        post_assertions(body, [])
        summary["blocked_reason"] = "no_judgeable_items"
        return body, summary

    blocks = [render_item(it, i + 1) for i, it in enumerate(kept)]
    body = "\n\n".join(blocks)

    post_assertions(body, collect_raw_values(kept))

    for i, it in enumerate(kept):
        summary["items"].append({
            "rank": i + 1,
            "url_host": it.get("url_host"),
            "url_is_shortlink": it.get("url_is_shortlink", False),
            "tier": it.get("tier"),
            "evidence_template_id": it.get("evidence_template_id"),
            "engagement_total": it.get("engagement_total"),
            "platform_field": it.get("platform_field"),
        })
    return body, summary


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="rank_render.py",
        description="阶段 3：去重 / 四级排序 / 截断 / 五字段渲染 -> final.md",
        epilog="退出码：0 正常（含 0 条）/ 1 输出后置断言失败 / 2 用法或输入结构错误。")
    parser.add_argument("--judged", required=True, help="judged.json 路径")
    parser.add_argument("--out", help="final.md 输出路径（缺省写 stdout）")
    parser.add_argument("--json", action="store_true", help="stdout 输出机器摘要 JSON")
    args = parser.parse_args(argv)

    try:
        judged = load_judged(args.judged)
        text, summary = build_final(judged, judged.get("branch"), args.out)
    except V.VpfError as exc:
        sys.stderr.write("错误：%s\n" % exc)
        return EXIT_USAGE

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    if args.out:
        try:
            V.write_out(args.out, text + "\n")
        except V.VpfError as exc:
            sys.stderr.write("错误：%s\n" % exc)
            return EXIT_USAGE
        sys.stdout.write(V.dumps_canonical(summary, compact=args.json) + "\n")
    elif args.json:
        sys.stdout.write(V.dumps_canonical(summary, compact=True) + "\n")
    else:
        sys.stdout.write(text + "\n")
        sys.stderr.write(V.dumps_canonical(summary, compact=True) + "\n")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
