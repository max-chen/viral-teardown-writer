#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""draft_check.py -- 写稿段（第二段）确定性校验器

职责：在稿子交给用户**之前**，把能机器判定的硬红线逐条卡死。
只做「形式合规 + 素材溯源 + 洗稿检测」，**不判断内容好坏**。

不可判定、因此本脚本**不予拦截**的部分（由 SKILL.md 的语义纪律约束）：
  - 「内核」是否真的抓准了爆款为什么能停住人
  - 三种平台口气（抖音/小红书/公众号）是否到位
  - 一篇是否只讲一件事
  - 是否读出声顺

设计原则：宁可漏拦，不可错杀。凡是需要理解语义才能判的规则，本脚本一律不写。

退出码：0 = 通过；2 = 违反硬红线（附理由与定位）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量（唯一事实源；改动请同步 references/draft-rules.md）
# ---------------------------------------------------------------------------

# 禁用词：空心形容词与夸张词。用户明确点名「不要用」。
BANNED_WORDS = [
    "高级感", "氛围感", "质感", "绝了", "震惊", "神器", "必看",
]

# 段首禁用：套话开头（用户明确点名）
BANNED_OPENERS = [
    "大家好", "因此", "综上所述",
]

# 输出必须恰好四块，顺序固定
REQUIRED_BLOCKS = ["标题", "正文", "内核", "改口"]

# 每块的中文序号前缀，容忍空白与标点差异
BLOCK_HEADER_RE = re.compile(r"^\s*([1-4])\s*[.、．]\s*(标题|正文|内核|改口)\s*[:：]?\s*$")

# 宽松块头：序号不限1-4、块名任意。仅用于**识别越界块**并报错，
# 不用于认定合法块。防止「5. 正文」这类第 5 块被当成普通正文行混进上一块。
LOOSE_HEADER_RE = re.compile(r"^\s*(\d)\s*[.、．]\s*(\S[^:：]*?)\s*[:：]?\s*$")

# 「改口」每一行的形态：<原句> → <改后>
REVISION_LINE_RE = re.compile(r"^(?P<before>.+?)\s*(?:→|->|➜)\s*(?P<after>.+?)\s*$")

# 涉及第一人称经验断言的动词/短语：素材里没有就不许出现。
# 这是「有素材必须用素材，没有的不许编」的可判定落点。
FIRST_PERSON_CLAIM_RE = re.compile(
    r"(我测过|我试过|我试|我用过|我做过|我经历|我遇到|我发现|我当时|我买过|我吃过|我拍过)"
)

# 洗稿检测：正文与参考原文的最长公共子串阈值（字符）。
# 依据：用户要求「只留内核、禁止复述原句」，而「禁止换词」字面不可判定
# （连「爆款」都不许写？）。故改为能量化的「禁止整句搬运」红线。
MAX_SHARED_SPAN = 12


class Violation:
    """一条硬红线违规。"""

    def __init__(self, rule: str, message: str, block: str = "", line_no: int = 0):
        self.rule = rule
        self.message = message
        self.block = block
        self.line_no = line_no

    def as_dict(self) -> dict:
        return {
            "rule": self.rule,
            "message": self.message,
            "block": self.block,
            "line": self.line_no,
        }

    def render(self) -> str:
        loc = f"[{self.block}]" if self.block else ""
        at = f" 第{self.line_no}行" if self.line_no else ""
        return f"  - {self.rule}{loc}{at}: {self.message}"


def _norm(text: str) -> str:
    """结构用归一化：全角转半角、去零宽字符。**保留换行。**

    换行必须保留——块首解析依赖行边界。压掉换行会把整篇并成一行，
    块结构全部丢失，表现为「必须恰好 4 块，实际 0 块」的误杀。
    """
    text = unicodedata.normalize("NFKC", text)
    # 零宽/软连字符一律剔除。必须用转义写法写码点，
    # 不可把裸字符直接塞进字符类——裸短横会被当成范围运算符，
    # 把 ASCII '-' 一并吃掉，导致 '->' 被拆成空格、箭头失效
    # （曾实际发生：改口块用 '->' 时被判 W-REV-FORMAT）。
    return re.sub("[\\u200b\\u200c\\u200d\\u2060\\ufeff\\u00a0\\u3000]", " ", text)


def _squeeze(text: str) -> str:
    """内容比对用归一化：在 _norm 之上**再压掉所有空白**。

    只给洗稿检测用。原稿的换行若参与比对，改写后不换行就会白送几十字，
    造成误杀；且原文段与段之间的换行会把两段无关内容连成一段公共片段，
    抬高判红概率。

    **不要在结构解析路径上用这个函数**——它会毁掉行边界。
    """
    return re.sub(r"\s+", "", _norm(text))


def parse_blocks(raw: str):
    """把稿子解析成 [(序号, 块名, [正文行...])]。

    块首必须形如「1. 标题」或「标题：」，顺序固定，恰好四块。
    返回 (blocks, violations)。
    """
    violations: list[Violation] = []
    lines = _norm(raw).splitlines()
    blocks = []
    cur = None

    for idx, line in enumerate(lines, start=1):
        m = BLOCK_HEADER_RE.match(line)
        if m:
            seq = int(m.group(1))
            name = m.group(2)
            if cur:
                blocks.append(cur)
            cur = (seq, name, [], idx)
        else:
            # **越界块头必须单独识别**。BLOCK_HEADER_RE 的块名只收四白名单，
            # 所以形如「5. 正文」的第 5 块会被当成普通正文行——于是模型多输出
            # 一段方法教学时，校验器只数到 4 块、零违规通过（曾实际发生）。
            # 这里用宽松模式单独兜住「<序号>. <任意块名>」，确保多块必被拦。
            m2 = LOOSE_HEADER_RE.match(line)
            if m2 and cur is not None:
                blocks.append(cur)
                cur = (int(m2.group(1)), m2.group(2), [], idx)
            elif cur is not None:
                cur[2].append(line)
    if cur:
        blocks.append(cur)

    # 恰好四块
    if len(blocks) != len(REQUIRED_BLOCKS):
        violations.append(Violation(
            "W-BLOCK-COUNT",
            f"必须恰好 {len(REQUIRED_BLOCKS)} 块，实际 {len(blocks)} 块。"
            f"只输出：{' / '.join(REQUIRED_BLOCKS)}，不要讲方法。",
        ))

    # 块名与顺序。只在块数吻合时逐位比对——
    # 块数不足时REQUIRED_BLOCKS[i] 会越界崩溃（曾实际发生），
    # 结构不合规必须报违规，不许让校验器自己炸掉。
    if len(blocks) == len(REQUIRED_BLOCKS):
        for i, (seq, name, _body, line_no) in enumerate(blocks):
            if name != REQUIRED_BLOCKS[i]:
                violations.append(Violation(
                    "W-BLOCK-ORDER",
                    f"第 {i + 1} 块应为「{REQUIRED_BLOCKS[i]}」，实际是「{name}」。四块顺序不可调换。",
                    block=name, line_no=line_no,
                ))
            if seq != i + 1:
                violations.append(Violation(
                    "W-BLOCK-SEQ",
                    f"块序号应为 {i + 1}，实际写了 {seq}。",
                    block=name, line_no=line_no,
                ))
    else:
        # 块数不对时，只报能安全定位的那部分
        for seq, name, _body, line_no in blocks:
            if name not in REQUIRED_BLOCKS:
                violations.append(Violation(
                    "W-BLOCK-NAME",
                    f"块名「{name}」不在允许列表内。只允许：{' / '.join(REQUIRED_BLOCKS)}。",
                    block=name, line_no=line_no,
                ))

    return blocks, violations


def _body_of(blocks, name: str) -> str:
    for _seq, blk_name, body, _ln in blocks:
        if blk_name == name:
            return "\n".join(body).strip()
    return ""


def check_banned_words(blocks) -> list[Violation]:
    """禁用词扫描。只扫「标题」与「正文」两块——它们面向读者。

    「内核」「改口」不扫：改口块本来就该写出被删掉的原句，
    里面出现「质感」是必要的（要展示改了什么）。
    """
    out: list[Violation] = []
    for target in ("标题", "正文"):
        text = _body_of(blocks, target)
        if not text:
            continue
        for w in BANNED_WORDS:
            for line_no, line in enumerate(text.splitlines(), start=1):
                if w in line:
                    out.append(Violation(
                        "W-BANNED-WORD",
                        f"出现禁用词「{w}」。用身体能感到的词替代"
                        f"（紧、凉、停住、不敢按）。",
                        block=target, line_no=line_no,
                    ))
    return out


def check_openers(blocks) -> list[Violation]:
    """段首套话检查。只看正文**第一句**——正文中间出现不算。"""
    out: list[Violation] = []
    body = _body_of(blocks, "正文")
    if not body:
        return out
    first = body.splitlines()[0].strip()
    for phrase in BANNED_OPENERS:
        if first.startswith(phrase):
            out.append(Violation(
                "W-OPENER",
                f"正文第一句以「{phrase}」开头。删掉，直接进第一句冲突或细节。",
                block="正文", line_no=1,
            ))
    return out


def check_no_advice_tail(blocks) -> list[Violation]:
    """禁止在「内核」块之后夹带方法教学 / 下一步建议。

    用户原话：「只输出这四块，不要讲方法」「不要总结规律，不要教我怎么拍」。
    判定方式：正文块内不得出现总结性收尾标记，且内核块必须只有一句话。
    """
    out: list[Violation] = []
    core = _body_of(blocks, "内核")
    if not core:
        return out
    lines = [ln for ln in core.splitlines() if ln.strip()]
    if len(lines) > 1:
        out.append(Violation(
            "W-CORE-ONELINE",
            f"「内核」必须一句话，实际 {len(lines)} 行。内核是这篇戳中什么，不是方法论。",
            block="内核", line_no=1,
        ))
    return out


def check_revisions(blocks) -> list[Violation]:
    """「改口」必须恰好 3 处，每处「原句 → 改后」。"""
    out: list[Violation] = []
    raw = _body_of(blocks, "改口")
    if not raw:
        out.append(Violation("W-REV-MISSING", "「改口」块为空。必须写出你改掉的 3 处生硬句子。",
                             block="改口"))
        return out
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if len(lines) != 3:
        out.append(Violation(
            "W-REV-COUNT",
            f"「改口」必须恰好 3 处，实际 {len(lines)} 处。",
            block="改口", line_no=1,
        ))
    for i, ln in enumerate(lines, start=1):
        if not REVISION_LINE_RE.match(ln):
            out.append(Violation(
                "W-REV-FORMAT",
                f"第 {i} 处格式不对：应为「原句 → 改后」，一處一行。",
                block="改口", line_no=i,
            ))
    return out


def check_material_grounding(blocks, material: str) -> list[Violation]:
    """素材溯源：素材为空却出现第一人称经验断言 -> 违规。

    依据用户原话：「有素材必须用素材。没有的经历、数字、对话，禁止编。
    素材里没有的，就不要写成「我测过」。」

    素材非空时**不做拦截**：素材里写了「我测过」，稿子里用「我测过」是如实转述，
    不是编造。本脚本无法判定「素材里的我测过」与「编出来的我测过」之别，
    因此只拦「素材为空」这一种可判定情形。
    """
    out: list[Violation] = []
    if material and material.strip():
        return out
    for target in ("标题", "正文"):
        text = _body_of(blocks, target)
        if not text:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            m = FIRST_PERSON_CLAIM_RE.search(line)
            if m:
                out.append(Violation(
                    "W-MATERIAL",
                    f"素材为空，却出现第一人称经验断言「{m.group(1)}」。"
                    f"没素材就不要写成自己经历过的样子。",
                    block=target, line_no=line_no,
                ))
    return out


def longest_shared_span(a: str, b: str) -> str:
    """a 与 b 的最长公共子串（用于洗稿检测）。

    实现为 O(len(a)*len(b)) 动态规划，稿子与原文都是几百字量级，
    无需更复杂的算法（不要为不存在的问题增加复杂度）。
    """
    if not a or not b:
        return ""
    prev = [0] * (len(b) + 1)
    best = ""
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > len(best):
                    best = a[i - 1 - cur[j] + 1: i]
        prev = cur
    return best


def check_plagiarism(blocks, source: str) -> list[Violation]:
    """洗稿检测：正文与参考原文的最长公共片段不得超过阈值。

    依据裁决：用户「禁止换词」字面不可判定，改为量化红线
    「禁止整句搬运」——保留内核、换掉句子，符合；原句照搬，不符合。
    """
    out: list[Violation] = []
    if not source or not source.strip():
        return out
    body = _body_of(blocks, "正文")
    if not body:
        return out
    span = longest_shared_span(_squeeze(body), _squeeze(source))
    if len(span) >= MAX_SHARED_SPAN:
        out.append(Violation(
            "W-PLAGIARISM",
            f"与参考原文有 {len(span)} 字连续相同片段（{span!r}），达到搬运阈值"
            f"（{MAX_SHARED_SPAN} 字）。只留它为什么能停住人的内核，"
            f"换立场/时间/人称重写，不要复述原句。",
            block="正文", line_no=1,
        ))
    return out


def check_draft(raw: str, material: str = "", source: str = "") -> dict:
    """全量校验。返回 {ok, violations, blocks}。"""
    blocks, violations = parse_blocks(raw)

    known = {name for _s, name, _b, _l in blocks}
    if "标题" not in known or "正文" not in known:
        # 结构已经坏了，后面的检查会误报，直接返回
        return {"ok": False, "violations": [v.as_dict() for v in violations],
                "blocks": [b[1] for b in blocks]}

    violations += check_banned_words(blocks)
    violations += check_openers(blocks)
    violations += check_no_advice_tail(blocks)
    violations += check_revisions(blocks)
    violations += check_material_grounding(blocks, material)
    violations += check_plagiarism(blocks, source)

    return {
        "ok": not violations,
        "violations": [v.as_dict() for v in violations],
        "blocks": [b[1] for b in blocks],
    }


def render_report(result: dict, draft_path: str) -> str:
    lines = [f"写稿校验：{draft_path}"]
    if result["ok"]:
        lines.append(f"  ✅ 通过（{len(result['blocks'])} 块结构合规，无硬红线违规）")
        lines.append("  注：内容质量（内核是否抓准、平台口气是否到位）不在本脚本判定范围")
        return "\n".join(lines)
    lines.append(f"  ❌ 违规 {len(result['violations'])} 处：")
    for v in result["violations"]:
        loc = f"[{v['block']}]" if v["block"] else ""
        at = f" 第{v['line']}行" if v["line"] else ""
        lines.append(f"  - {v['rule']}{loc}{at}: {v['message']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="写稿段确定性校验器（只拦形式红线，不判内容好坏）")
    ap.add_argument("--draft", required=True, help="待校验稿子路径（md 或 txt）")
    ap.add_argument("--material", default="", help="素材文件路径；留空表示用户未提供素材")
    ap.add_argument("--source", default="", help="参考爆款原文路径；留空表示无参考原文")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    draft_p = Path(args.draft)
    if not draft_p.is_file():
        print(f"[draft_check] 找不到稿子：{draft_p}", file=sys.stderr)
        return 2
    raw = draft_p.read_text(encoding="utf-8")

    material = ""
    if args.material:
        m_p = Path(args.material)
        if not m_p.is_file():
            print(f"[draft_check] 找不到素材文件：{m_p}", file=sys.stderr)
            return 2
        material = m_p.read_text(encoding="utf-8")

    source = ""
    if args.source:
        s_p = Path(args.source)
        if not s_p.is_file():
            print(f"[draft_check] 找不到参考原文：{s_p}", file=sys.stderr)
            return 2
        source = s_p.read_text(encoding="utf-8")

    result = check_draft(raw, material=material, source=source)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_report(result, str(draft_p)))

    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())