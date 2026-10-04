#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vpf_common.py -- viral-teardown-writer 共享库（确定性常量唯一事实源 + 原子操作）

职责边界（蓝图 §6.1 三问裁决）：
  确定性 ✅ 常量表 + 纯函数，逐条可枚举；同输入同输出，可机器回归。
  理解   ❌ 本库不做任何语义判断。不判断「这坨文字看起来够不够」「像不像广告」。
  失败   ✅ 非法输入抛 VpfError，由调用方转成退出码 2 并打印确切修法。

硬约束：
  1. 零第三方依赖、零网络 API（公理 2）。不 import urllib / requests / socket /
     http.client / playwright，连 urllib.parse 也不用（URL 解析走正则）。
  2. 零 3.9+ 语法（match / X | Y 类型 / removeprefix / dict |），适配 python3.8。
  3. 不出现 os.getcwd()，一切路径以本文件位置为基准（D1）。
  4. 全部 IO 显式 encoding="utf-8"；脚本内一律 ASCII 直引号。

CLI：
    python3 vpf_common.py --dump-tables      # 把常量表导出为 Markdown
"""

import argparse
import json
import pathlib
import re
import sys
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2


class VpfError(ValueError):
    """输入结构或用法非法。调用方必须转成退出码 2 + 明确修法，禁止静默退化。"""


# ==========================================================================
# 1. 平台枚举与归属表（裁决 D2：域名表是「归属线索」，不是准入门槛）
# ==========================================================================

# 平台枚举（喂料回显后的确定值）。key = 机器枚举，value = 用户可见中文标签。
PLATFORM_ENUM = {
    "douyin": "抖音",
    "xiaohongshu": "小红书",
    "bilibili": "B站",
    "wechat_channels": "视频号",
}
PLATFORM_ENUM_KEYS = ("bilibili", "douyin", "wechat_channels", "xiaohongshu")
# 接受用户口语写法 → 机器枚举。只做字面归一，不做语义猜测。
PLATFORM_ALIASES = {
    "抖音": "douyin",
    "douyin": "douyin",
    "小红书": "xiaohongshu",
    "xiaohongshu": "xiaohongshu",
    "红书": "xiaohongshu",
    "b站": "bilibili",
    "bilibili": "bilibili",
    "哔哩哔哩": "bilibili",
    "视频号": "wechat_channels",
    "wechat_channels": "wechat_channels",
    "微信视频号": "wechat_channels",
}

# 平台 host 归属表（裁决 D2：仅用于平台归属判定，短链与长链同等收录）。
# 匹配规则：host == entry 或 host.endswith("." + entry)，不做后缀模糊猜。
# full = 完整链接 host；short = 短链 host。二者归属同一个平台枚举。
PLATFORM_HOST_TABLE = {
    "douyin": {
        "full": ("douyin.com", "iesdouyin.com"),
        "short": ("v.douyin.com",),
    },
    "xiaohongshu": {
        "full": ("xiaohongshu.com",),
        "short": ("xhslink.com",),
    },
    "bilibili": {
        "full": ("bilibili.com",),
        "short": ("b23.tv",),
    },
    "wechat_channels": {
        "full": ("channels.weixin.qq.com",),
        "short": (),
    },
}

# 无法归属到四平台的短链 host：命中即直接判「未获取」，不猜（N9 + 裁决 D2）。
UNATTRIBUTABLE_SHORT_HOSTS = ("v.kuaishou.com", "url.cn", "t.cn")


def all_hosts_for(platform_key):
    """某平台的全部 host（full + short），用于归属匹配。"""
    entry = PLATFORM_HOST_TABLE[platform_key]
    return tuple(entry["full"]) + tuple(entry["short"])

# 裁决 D2 的硬边界，供文档与自测引用（代码里不执行跳转，只做断言）。
SHORTLINK_POLICY = (
    "短链（xhslink.com / b23.tv 等）与长链同等合法，收入条目；"
    "禁止展开跳转、禁止猜真实域名、禁止自己生成 URL"
)


# ==========================================================================
# 2. 榜单位字样白名单（A 档依据）与推荐流例外集（不算 A 档，裁决 D3）
# ==========================================================================

RANK_LABEL_ALLOW = (
    "热门榜",
    "热榜",
    "榜单",
    "排行榜",
    "TOP榜",
    "热搜榜",
    "热门",
)

RECOMMEND_ONLY_SET = (
    "推荐",
    "推荐流",
    "为你推荐",
    "猜你喜欢",
    "大家都在看",
    "相似推荐",
)

# 榜位数字：纯数字，1~4 位（F4）
RANK_POS_RE = re.compile(r"^\d{1,4}$")
# 「榜单第N」这类带数字的榜位形式，由 rank_pos 承担，不进 rank_label
RANK_LABEL_NUMBERED_RE = re.compile(r"^(?:榜单|排行榜|热榜|热门榜|第)\s*第?\s*\d{1,4}\s*位?$")


# ==========================================================================
# 3. 指标口径（Q8：只对页面写出的项求和，禁止跨指标加权）
# ==========================================================================

METRIC_KEYS = ("play", "like", "collect", "comment")
METRIC_LABELS = {"play": "播放", "like": "点赞", "collect": "收藏", "comment": "评论"}
# 指标退化固定序（Q3 / F4）：播放 → 点赞 → 收藏。评论不参与退化，只进总量。
METRIC_DEGRADE_ORDER = ("play", "like", "collect")

STATE_EXACT = "EXACT"        # 精确可归一：进求和、进阈值判定
STATE_VERBATIM = "VERBATIM"  # 带模糊修饰：原文照抄展示，不进求和、不进阈值
STATE_UNPARSABLE = "UNPARSABLE"  # 匹配不上语法：标「未获取」，计 0
METRIC_STATES = (STATE_EXACT, STATE_VERBATIM, STATE_UNPARSABLE)

UNIT_TABLE = (
    ("万", 4),
    ("w", 4),
    ("W", 4),
    ("千", 3),
    ("k", 3),
    ("K", 3),
    ("亿", 8),
    ("百", 2),
)

# 模糊修饰字符集（裁决 D4：这些是页面原文写着的，照抄照收，不判失败）。
# 可作前缀（`约50万`）也可作后缀（`3.5w+`）。
MOD_CHARS = "约近莫超过大概＋+"
# 数值后缀白名单（`1.2万点赞` 的「点赞」被吸收，不影响 EXACT 判定）。
# 注：蓝图 §10.1 的 PATTERN 原文 suffix 组只列到「赞」，但同节 §10.4 的逐输入
# 行为表明确要求 `1.2万点赞` 判 EXACT=12000 并写「后缀「点赞」被 PATTERN 的
# suffix 吸收」。两处自相矛盾，实现按 §10.4（更具体、可机检）补齐「点赞」。
# 见实现报告 R-02。
METRIC_SUFFIXES = ("万", "w", "W", "千", "k", "K", "亿", "百",
                   "次", "人", "赞", "点赞", "播放", "个")

_NUM_CORE = r"(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
# 蓝图 §10.1 的 PATTERN 原文把 mod 组放在 unit 组之后，无法匹配前缀修饰的
# `约50万` / `超10万`（§10.4 却要求这两个判 VERBATIM）。实现按 §10.4 的逐输入
# 行为表修正为「修饰词可前可后」，其余结构与 §10.1 一致。见实现报告 R-01。
METRIC_PATTERN = re.compile(
    r"^(?P<pre>[" + MOD_CHARS + r"]*)"
    r"(?P<num>" + _NUM_CORE + r")"
    r"(?P<unit>[万wW千kK亿百])?"
    r"(?P<post>[" + MOD_CHARS + r"]*)"
    r"(?P<suffix>" + "|".join(METRIC_SUFFIXES) + r")?$"
)


# ==========================================================================
# 4. 发布日期格式白名单（F9）
# ==========================================================================

# 日期格式白名单。每项 = (正则, 组语义)。组语义用命名组 y/m/d 显式声明，
# 避免「有没有年份」与「第几组是月」错位（`2026-10` 曾因此解析成 None）。
DATE_ABS_FORMATS = (
    (re.compile(r"^(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})$"), True),
    (re.compile(r"^(?P<y>\d{4})/(?P<m>\d{1,2})/(?P<d>\d{1,2})$"), True),
    (re.compile(r"^(?P<y>\d{4})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日$"), True),
    (re.compile(r"^(?P<m>\d{1,2})月(?P<d>\d{1,2})日$"), False),
    (re.compile(r"^(?P<y>\d{4})-(?P<m>\d{1,2})$"), True),
    (re.compile(r"^(?P<y>\d{4})年(?P<m>\d{1,2})月$"), True),
)
DATE_REL_DAYS = {"今天": 0, "今日": 0, "昨天": 1, "昨日": 1, "前天": 2}
DATE_REL_RE = re.compile(r"^(\d{1,3})(天|小时)前$")


# ==========================================================================
# 5. 字面量白名单与黑名单（B8 / B6）
# ==========================================================================

MISSING_NUM = "未获取"        # 数字缺失
MISSING_TEXT = "无文案"       # 文案缺失
MISSING_ANY = "未获取"        # 整体证据缺失（证据形态 4）
MISSING_LITERALS = (MISSING_NUM, MISSING_TEXT, MISSING_ANY)

# B8 禁止的替代写法。命中即 exit 2，不允许「换个花样写」。
BANNED_MISSING_ALTERNATIVES = (
    "暂无", "N/A", "n/a", "NA", "-", "--", "null", "None",
    "0", "未知", "无数据", "没有", "不详",
)

# B6：只禁「由我们生成的」修饰词。页面原文里的 `约` / `+` 属照抄，不违规。
# 因此本黑名单只在剔除全部 raw 原文之后的残余文本上扫描（见 strip_raw_literals）。
SELF_MADE_MODIFIER_RE = re.compile(
    r"(大约|大概|预计|预估|左右|可能超过|应该是|行业平均|约为|接近|不少于|高于)"
)

# 输出末尾禁止出现的小节标题与提示行（B10 / G5 / 矩阵第 26 行）
FORBIDDEN_OUTPUT_SECTIONS = ("总结", "规律", "建议", "下一步", "方法论", "复盘", "小结")
FORBIDDEN_SUMMARY_LINE_RE = re.compile(r"本批.{0,12}条.{0,12}未获取|共\d+条|占比|命中率")

# 证据形态白名单（需求 6.3 的四种形态，id 与之一一对应）
EVIDENCE_TEMPLATE_IDS = ("rank", "absolute", "relative", "none")

# 阈值常量（Q5：该条数值 >= 近 N 条同指标中位数的 3 倍，含等号）
RATIO_FACTOR = 3
BASELINE_MAX_ENTRIES = 10   # Q3：取账号主页最近 10 条
CAPTION_MAX_CHARS = 300     # Q7：文案上限 300 字
MAX_OUTPUT_ITEMS = 10       # 条数上限
INF = 2 ** 31               # 排序键占位常量

# 喂料级状态（F0 / F1 / F2）。命名沿用蓝图 §4.4，与字段级编号 F1~F14 不同层，
# 文档中一律写成「喂料级 F0/F1/F2」以免与字段编号混淆。
FEED_STATE_INVALID = "F0"    # 无有效喂料
FEED_STATE_SUFFICIENT = "F1"  # 充分
FEED_STATE_INSUFFICIENT = "F2"  # 不足

# 蓝图 §4.4 边界裁决：基线缺失不参与充分性判定（D1）。做成显式常量，
# 裁决若翻转只改这一个值，逻辑不动。
BASELINE_REQUIRED_FOR_SUFFICIENCY = False


# ==========================================================================
# 6. URL 解析（正则实现，零 urllib 依赖）
# ==========================================================================

URL_RE = re.compile(
    r"^(?P<scheme>https?)://(?P<authority>[^/?#\s]+)"
    r"(?P<path>[^?#\s]*)"
    r"(?:\?(?P<query>[^#\s]*))?"
    r"(?:#(?P<frag>\S*))?$",
    re.IGNORECASE,
)
HOST_RE = re.compile(r"^[A-Za-z0-9._~%-]+$")

# N9 安全：URL 的 query 里出现访问凭据型参数名即判无效地址。
# 只看参数名，不看值；命中不解释、不修复，直接判该条目地址不合法。
CREDENTIAL_QUERY_RE = re.compile(
    r"(?:^|[?&])(?:[A-Za-z0-9_.-]*(?:token|sign|signature|auth|key|secret|password|passwd|pwd|session|cookie)[A-Za-z0-9_.-]*)=[^&]*",
    re.IGNORECASE,
)


def parse_url(raw):
    """解析 URL，返回 dict；不合法返回 None（调用方按 F2 判丢弃）。

    只做字面解析：协议、authority、host、query。不解析 DNS、不发请求、
    不跟随跳转（N9 + 裁决 D2 禁止展开短链）。
    """
    if not isinstance(raw, str):
        return None
    m = URL_RE.match(raw.strip())
    if not m:
        return None
    authority = m.group("authority")
    if "@" in authority:
        # authority 内嵌 user:pass@ 属凭据型 URL，直接判不合法
        return None
    host = authority.split(":")[0].lower()
    if not HOST_RE.match(host):
        return None
    return {
        "scheme": m.group("scheme").lower(),
        "authority": authority,
        "host": host,
        "path": m.group("path") or "",
        "query": m.group("query"),
        "frag": m.group("frag"),
    }


def url_has_credential_query(parsed):
    """query 含访问凭据型参数名 → True（N9）。"""
    if not parsed or not parsed.get("query"):
        return False
    return bool(CREDENTIAL_QUERY_RE.search(parsed["query"]))


def is_shortlink_host(host):
    """host 是否为短链 host（归属表里的短链，或无法归属的短链）。

    短链与长链同等合法（裁决 D2），本函数只用于留痕与自测断言，
    不参与地址准入判定，也绝不用于展开跳转。
    """
    if not host:
        return False
    h = host.lower()
    for key in PLATFORM_ENUM_KEYS:
        for entry in PLATFORM_HOST_TABLE[key]["short"]:
            if h == entry or h.endswith("." + entry):
                return True
    for entry in UNATTRIBUTABLE_SHORT_HOSTS:
        if h == entry or h.endswith("." + entry):
            return True
    return False


def platform_by_host(host):
    """host 精确命中归属表 → 平台机器枚举；命中短链例外集或未命中 → None。"""
    if not host:
        return None
    h = host.lower()
    for key in PLATFORM_ENUM_KEYS:
        for entry in all_hosts_for(key):
            if h == entry or h.endswith("." + entry):
                return key
    for entry in UNATTRIBUTABLE_SHORT_HOSTS:
        if h == entry or h.endswith("." + entry):
            return None
    return None


def normalize_platform_input(text):
    """把 --platform 的用户写法归一到机器枚举；无法归一抛 VpfError。"""
    if text is None:
        return None
    key = unicodedata.normalize("NFKC", str(text)).strip().lower().replace(" ", "")
    if key in PLATFORM_ENUM:
        return key
    if key in PLATFORM_ALIASES:
        return PLATFORM_ALIASES[key]
    raise VpfError(
        "--platform 取值非法：'{}'。可选枚举：{}（也接受对应中文写法）。"
        "平台必须由用户回显确认，脚本不做语义猜测。".format(
            text, " / ".join("%s=%s" % (k, PLATFORM_ENUM[k]) for k in PLATFORM_ENUM_KEYS)
        )
    )


def resolve_platform_field(url_raw, context_platform):
    """平台字段取值（蓝图 §11.3.3 + 裁决 D2 明文）。返回 (展示值, 来源标记)。

    1) host 命中归属表 → 该平台（确定性，保证 C8 链接与平台一致）
    2) host 是**不可归属的短链**（url.cn / t.cn / v.kuaishou.com 等）→ 「未获取」
    3) 其余情况取喂料上下文（--platform）
    4) 都推断不出 → 「未获取」

    第 2 步是裁决 D2 的特指条款，优先于第 3 步（QA N-01）：`--platform` 是用户对
    **本次要找的内容**的描述，不是对**某条链接归属**的证据。拿它去填一条
    归属不明的链接的平台，等于把「猜的」冒充「验过的」——而 D-03 修好后输出里
    其它条目的平台都经 host 验证过，这一条混在中间更像真的。

    因此「不可归属短链」这一类**无论有没有传 --platform 都填「未获取」**，
    但条目**照常保留**（裁决 D2 的放行条件：短链照收不丢弃）。
    """
    parsed = parse_url(url_raw) if isinstance(url_raw, str) else None
    host = parsed["host"] if parsed else None
    by_host = platform_by_host(host)
    if by_host:
        return PLATFORM_ENUM[by_host], "host"
    if host and is_shortlink_host(host):
        # 不可归属短链：平台填「未获取」，不猜（裁决 D2）
        return MISSING_NUM, "none"
    if context_platform:
        return PLATFORM_ENUM[context_platform], "context"
    return MISSING_NUM, "none"


# ==========================================================================
# 7. 溯源核验（公理 3：喂料不是许可证）
# ==========================================================================


def _is_absolute(p):
    s = str(p)
    return s.startswith("/") or (len(s) > 1 and s[1] == ":")


def _as_p(p):
    return pathlib.Path(str(p))


def load_corpus(path):
    """读原始喂料文本。溯源核验的基准，缺它就无法证明「只认页面写着的」。"""
    p = _as_p(path) if _is_absolute(path) else (SCRIPT_DIR / path)
    if not p.exists():
        raise VpfError(
            "找不到原始喂料文本：{}\n"
            "溯源核验必须有原始喂料文本作为基准，缺则无法证明「只认页面写着的」，"
            "因此不做无基准运行。".format(path)
        )
    text = p.read_text(encoding="utf-8")
    # 仅做换行编码归一（CRLF/CR -> LF），不改任何字符内容
    return text.replace("\r\n", "\n").replace("\r", "\n")


def verify_raw(raw, corpus):
    """raw 是否是原始喂料文本的精确子串。

    精确子串，不做模糊匹配、不做去空格宽松匹配、不做大小写折叠。
    这是「喂料不是许可证」的机器落点：用户口头说的数字进不了 raw，
    因此进不了输出（验收 E9）。
    """
    if not isinstance(raw, str):
        return False
    if raw.strip() == "":
        return False
    return raw in corpus


def leaf_value(node):
    """取 feed.json 的叶子值 raw 字符串；结构不符返回 None。"""
    if node is None:
        return None
    if isinstance(node, dict):
        raw = node.get("raw")
        if raw is None:
            return None
        if not isinstance(raw, str):
            raise VpfError("叶子值的 raw 必须是字符串，实际类型为 %s" % type(raw).__name__)
        return raw
    raise VpfError(
        "叶子值必须是 {raw, src} 对象或 null，实际收到：%r。"
        "每个值都必须带 raw（页面原文片段），src 可选标注位置。" % (node,)
    )


# ==========================================================================
# 8. 单位归一化三态（裁决 D4：照收照抄，判三态）
# ==========================================================================


def normalize_metric(raw):
    """把页面上写着的指标原文归一化。返回 (state, value|None, raw_verbatim)。

    EXACT       有数值 + 白名单单位 + 无模糊修饰  → 进求和、进阈值判定
    VERBATIM    数值可解析但带约/近/超/+/大概等    → 原文照抄，不进算术
    UNPARSABLE 匹配不上语法                        → 「未获取」，计 0

    归一化是「解析」不是「估算」：只有 EXACT 允许进算术。另外两个态都不参与
    任何计算，但 VERBATIM 必须原文照抄保留，只有 UNPARSABLE 才标「未获取」。
    禁止取数字前缀当值、禁止乘任何系数、禁止按平台经验补典型值。
    """
    if raw is None:
        return STATE_UNPARSABLE, None, None
    if not isinstance(raw, str):
        raise VpfError("指标原文必须是字符串，实际类型为 %s" % type(raw).__name__)
    # 步骤 1：NFKC 编码归一（仅用于解析；展示一律回写调用方传入的原文 raw）
    text = unicodedata.normalize("NFKC", raw).strip()
    if text == "":
        return STATE_UNPARSABLE, None, None
    # 步骤 2：匹配语法白名单
    m = METRIC_PATTERN.match(text)
    if not m:
        return STATE_UNPARSABLE, None, raw
    # 步骤 3：去千分位逗号（语法已保证形态，不会误删正文逗号）
    num_text = m.group("num").replace(",", "")
    # 步骤 4：Decimal 计算，禁止 float（避免 1.2*10000=11999.999…）
    exp = 0
    if m.group("unit"):
        for token, power in UNIT_TABLE:
            if m.group("unit") == token:
                exp = power
                break
    try:
        value = int(
            (Decimal(num_text) * (Decimal(10) ** exp)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
    except (InvalidOperation, ValueError):
        return STATE_UNPARSABLE, None, raw
    # 步骤 5：修饰词非空 → VERBATIM；否则 EXACT。两者都带回原文
    has_mod = bool(m.group("pre")) or bool(m.group("post"))
    state = STATE_VERBATIM if has_mod else STATE_EXACT
    return state, value, raw


def exact_value(raw):
    """只要 EXACT 的整数值；VERBATIM / UNPARSABLE 一律返回 None（不进算术）。"""
    state, value, _ = normalize_metric(raw)
    return value if state == STATE_EXACT else None


# ==========================================================================
# 9. 榜位解析与分档
# ==========================================================================


def normalize_rank_label(raw):
    """榜单位字样归一。返回 (归一后字样|None, 类别)。

    类别取值：ALLOW（榜单位白名单，A 档依据）/ RECOMMEND（推荐流例外集）
    / NUMBERED（榜单第N，数字部分交 rank_pos）/ NONE（白名单外）。
    """
    if raw is None or not isinstance(raw, str):
        return None, "NONE"
    text = unicodedata.normalize("NFKC", raw).strip()
    if text == "":
        return None, "NONE"
    compact = re.sub(r"\s+", "", text)
    if RANK_LABEL_NUMBERED_RE.match(compact):
        return compact, "NUMBERED"
    for token in RANK_LABEL_ALLOW:
        if token in compact:
            return compact, "ALLOW"
    for token in RECOMMEND_ONLY_SET:
        if token in compact:
            return compact, "RECOMMEND"
    return None, "NONE"


def normalize_rank_pos(raw):
    """榜位归一。纯数字 1~4 位 → int；否则 None（视为无榜位）。"""
    if raw is None or not isinstance(raw, str):
        return None
    text = unicodedata.normalize("NFKC", raw).strip()
    if RANK_POS_RE.match(text):
        value = int(text)
        if value >= 1:
            return value
    return None


def assign_tier(rank_label_state, rank_pos, has_metric):
    """证据分档（蓝图 §8.1）。返回 (tier, tier_rank)。

    A 榜单位白名单字样 或 带名次排名 → A
    B 推荐流字样且无榜位，或有任一可见互动数字 → B
    C 不在榜且无任何可见互动数字 → C（凭什么算爆款 = 未获取）
    """
    if rank_label_state == "ALLOW":
        return "A", 0
    if rank_pos is not None:
        return "A", 0
    if rank_label_state == "RECOMMEND":
        return "B", 1
    if has_metric:
        return "B", 1
    return "C", 2


# ==========================================================================
# 10. 中位数与 3 倍阈值（纯整数，含等号，偶数取中间两数均值）
# ==========================================================================


def median_exact(values):
    """中位数精确值。返回 (num, den, exact_text)；空样本返回 (None, None, None)。

    偶数条必须取中间两数均值（用 sorted()[n//2] 是错的）。
    展示禁止 round()：Python 的 round(2.5)=2（银行家舍入），会让「可复算」
    变成「不可复算」。这里用 divmod 拼出精确文本。
    """
    xs = sorted(v for v in values if isinstance(v, int))
    n = len(xs)
    if n == 0:
        return None, None, None
    if n % 2 == 1:
        num, den = xs[n // 2], 1
        return num, den, str(num)
    num = xs[n // 2 - 1] + xs[n // 2]
    q, r = divmod(num, 2)
    return num, 2, (str(q) if r == 0 else "%d.5" % q)


def ratio_passed(target, num, den, factor=RATIO_FACTOR):
    """target >= factor * num/den  <=>  target*den >= factor*num。纯整数，零浮点抖动。

    含等号：恰好 3 倍判通过（Q5 / 验收 F2）。
    中位数为 0 时一律判不通过：数学上 0 的任意倍数仍是 0，但那等于「随便多高都算
    爆款」，会让相对结论失去意义（蓝图 §8.3：中位数为 0 -> 不写相对结论）。
    """
    if target is None or num is None or den is None:
        return False
    if num == 0:
        return False
    return (target * den) >= (factor * num)


# ==========================================================================
# 11. 发布日期解析（白名单 + 相对表述锚定 --today）
# ==========================================================================


def parse_today(text):
    """校验 --today（YYYY-MM-DD）。非法抛 VpfError（时间窗计算归代码，矩阵第 4 行）。"""
    if text is None:
        return date.today()
    s = str(text).strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        raise VpfError(
            "--today 格式非法：'{}'。期望 YYYY-MM-DD（如 2026-10-04）。"
            "时间窗必须锚定一个确定日期，禁止含糊表述。".format(text)
        )
    try:
        y, m, d = [int(x) for x in s.split("-")]
        return date(y, m, d)
    except ValueError as exc:
        raise VpfError("--today 不是合法日历日期：'{}'（{}）".format(text, exc))


def parse_publish_time(raw, today):
    """发布时间原文 → ISO 日期串；不匹配白名单 → None（视为缺失，排序键取末位）。

    字幕轴 / 时间轴上的 `00:12` 这类时间点不是发布时间，格式不匹配 → None。
    """
    if raw is None or not isinstance(raw, str):
        return None
    text = unicodedata.normalize("NFKC", raw).strip()
    if text == "":
        return None
    if text in DATE_REL_DAYS:
        return (today - timedelta(days=DATE_REL_DAYS[text])).isoformat()
    m = DATE_REL_RE.match(text)
    if m:
        n = int(m.group(1))
        delta = timedelta(days=n) if m.group(2) == "天" else timedelta(hours=n)
        return (today - delta).isoformat()
    for pattern, has_year in DATE_ABS_FORMATS:
        mm = pattern.match(text)
        if not mm:
            continue
        groups = mm.groupdict()
        y = int(groups["y"]) if has_year else today.year
        mo = int(groups["m"])
        d = int(groups["d"]) if groups.get("d") else 1
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return None
    return None


def date_ordinal(iso_text):
    """ISO 日期串 → 序数（排序键用）；非法 → None。"""
    if not iso_text:
        return None
    try:
        y, m, d = [int(x) for x in iso_text.split("-")]
        return date(y, m, d).toordinal()
    except (ValueError, TypeError):
        return None


# ==========================================================================
# 12. 文案处理（N2 原文摘录 + Q7 上限 300 字）
# ==========================================================================


def fold_whitespace(text):
    """把连续空白（含换行）折叠成单个半角空格。

    仅排版折叠：字符内容与顺序不变，不改写、不删字、不加字。
    五字段块必须单行可解析，否则「每块恰 5 个字段」无法机器判定。
    """
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def truncate_caption(text, limit=CAPTION_MAX_CHARS):
    """文案截断。返回 (截断后文本, 是否截断)。不因截断改写或补全。"""
    if text is None:
        return None, False
    folded = fold_whitespace(text)
    if len(folded) <= limit:
        return folded, False
    return folded[:limit], True


# ==========================================================================
# 13. 文本合规校验（B6 / B8 / B10 的机器落点）
# ==========================================================================


def raw_literal_variants(raw_values):
    """把每个 raw 展开成「原串 + 折叠空白后的形态」两个变体。

    D-04：caption 常含换行（短视频口播 / 字幕摘录天然多行），而渲染层输出的是
    折叠后的形态（原串的 `\\n` 变成了空格）。若只按原串做 `str.replace`，
    折叠后的输出剔不掉，残��里就留着页面原文里的「大约」，被误判成我们自造的
    修饰词——整批 exit 2。因此两个形态都要尝试剔除。

    注意：折叠只改空白，不改任何字面内容，所以剔除折叠形态不会放过任何
    我们自己生成的字。
    """
    variants = set()
    for raw in raw_values:
        if not isinstance(raw, str) or not raw:
            continue
        variants.add(raw)
        folded = fold_whitespace(raw)
        if folded and folded != raw:
            variants.add(folded)
    return variants


def strip_raw_literals(text, raw_values):
    """从文本中剔除全部 raw 原文，剩下的才是「我们自己写的字」。

    裁决 D4 的正确边界：禁的是由我们生成的「大约 / 预计 / 左右」；页面原文里
    本来就有的 `约` / `+` 属照抄，不违规。因此自造修饰词扫描必须在剔除 raw
    之后的残余文本上做，否则会把照抄的 `约50万` 误判成我们自造的修饰词。

    同一机制也用于末尾汇总行检测（B10 / G5）：`占比` / `共N条` / `命中率` 是
    短视频与小红书文案里的高频词，页面原文里出现它们是正常的，只有**我们自己**
    写的汇总行才该被拦（QA D-02）。

    已知且可接受的漏检窗口：本函数做的是**无条件逐字节替换**。若我们将来新增的
    某个输出片段，其文字与某条页面原文**逐字节完全相同**，该片段会被一并剔除，
    从而绕过越界词检查。触发条件需要内部新增代码（当前代码从不产生裸汇总行），
    后果轻微。修法方向：新增自由文本字段时同步把该字段的 raw 收进
    `rank_render.collect_raw_values`，并对本函数加针对性用例。
    """
    out = text or ""
    for raw in sorted(raw_literal_variants(raw_values), key=len, reverse=True):
        out = out.replace(raw, " ")
    return out


def find_self_made_modifier(text, raw_values):
    """自造修饰词命中列表；命中即 exit 2（矩阵第 20 行 / 验收 B6）。"""
    residual = strip_raw_literals(text, raw_values)
    return SELF_MADE_MODIFIER_RE.findall(residual)


def find_forbidden_literal(value):
    """字段值是否用了「未获取 / 无文案」的禁止替代写法。命中即 exit 2（验收 B8）。"""
    if value is None:
        return None
    text = str(value).strip()
    for bad in BANNED_MISSING_ALTERNATIVES:
        if text == bad:
            return bad
    return None


def find_summary_line(text, raw_values=()):
    """输出里是否混入了**我们自己写的**汇总行 / 总结小节（验收 B10 / G5）。

    命中即 exit 2。但扫描前必须先剔除全部 raw 原文（QA D-02）：
    `占比` / `共N条` / `命中率` 是短视频与小红书文案里的高频词，页面原文里出现
    属正常；B10 禁的是「我们自己附加的总结」，不是「页面原文恰好有这些字」。
    与自造修饰词扫描共用 strip_raw_literals，边界口径一致（裁决 D4 同理）。
    """
    hits = []
    for line in (text or "").split("\n"):
        stripped = line.strip().lstrip("#").lstrip("-").strip()
        if not stripped:
            continue
        residual = strip_raw_literals(stripped, raw_values).strip()
        if not residual:
            continue
        for word in FORBIDDEN_OUTPUT_SECTIONS:
            if residual == word or residual.startswith(word + "：") \
                    or residual.startswith(word + ":"):
                hits.append(stripped)
        if FORBIDDEN_SUMMARY_LINE_RE.search(residual):
            hits.append(stripped)
    return hits


# ==========================================================================
# 14. 确定性 JSON 输出（C1：同输入两次逐字节一致）
# ==========================================================================


def dumps_canonical(obj, compact=False):
    """确定性 JSON 序列化：固定分隔符、固定键序（调用方构造时保序）、不排空白。

    不含时间戳、不含随机数、不含系统路径，因此同输入两次输出逐字节一致。
    """
    if compact:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False)


def write_out(path, text):
    """写文件（幂等：同路径覆盖）。不创建目录。"""
    p = pathlib.Path(str(path))
    if not p.parent.exists():
        raise VpfError("输出目录不存在：{}（脚本不创建目录，避免副作用）".format(p.parent))
    p.write_text(text, encoding="utf-8")


def emit(payload, compact=False, out=None):
    """stdout 输出确定性段；--out 存在时同时落盘。"""
    text = dumps_canonical(payload, compact=compact)
    if out:
        write_out(out, text + "\n")
    return text


# ==========================================================================
# 15. --dump-tables：常量表导出（供 references 与代码保持同源，防漂移）
# ==========================================================================


def dump_tables():
    """把库内常量表导出为 Markdown。references/evidence-templates.md 的表格来源。"""
    lines = []
    add = lines.append

    add("# 常量表导出（由 scripts/vpf_common.py --dump-tables 生成，禁止手抄手改）")
    add("")
    add("## 平台枚举与归属表（裁决 D2：仅用于平台归属判定，不是地址准入门槛）")
    add("")
    add("| 平台枚举 | 用户可见标签 | 完整链接 host | 短链 host（归属推断用） |")
    add("|---|---|---|---|")
    for key in PLATFORM_ENUM_KEYS:
        entry = PLATFORM_HOST_TABLE[key]
        full = " ".join(entry["full"]) or "—"
        shorts = " ".join(entry["short"]) or "—"
        add("| `%s` | %s | %s | %s |" % (key, PLATFORM_ENUM[key], full, shorts))
    add("")
    add("无法归属到四平台的短链 host（命中即填「未获取」，不猜）：%s"
        % "、".join(UNATTRIBUTABLE_SHORT_HOSTS))
    add("")
    add("短链处置（裁决 D2）：%s" % SHORTLINK_POLICY)
    add("")

    add("## 榜单位字样白名单（A 档依据）")
    add("")
    add("、".join("`%s`" % t for t in RANK_LABEL_ALLOW))
    add("")
    add("## 推荐流例外集（命中且无榜位 → B 档，不算 A 档；裁决 D3）")
    add("")
    add("、".join("`%s`" % t for t in RECOMMEND_ONLY_SET))
    add("")

    add("## 指标口径与退化序")
    add("")
    add("| 项 | 取值 |")
    add("|---|---|")
    add("| 指标字段 | %s |" % "、".join("`%s`（%s）" % (k, METRIC_LABELS[k]) for k in METRIC_KEYS))
    add("| 退化优先序 | %s |" % " → ".join("`%s`" % k for k in METRIC_DEGRADE_ORDER))
    add("| 互动总量 | 只累加 EXACT 项；VERBATIM 剔除进 `excluded_terms`；UNPARSABLE 计 0 |")
    add("| 权重系数 | 无。代码中不存在任何跨指标权重（验收 G3 可 grep 断言） |")
    add("")

    add("## 单位表")
    add("")
    add("| 单位 | 10 的幂 |")
    add("|---|---|")
    for token, power in UNIT_TABLE:
        add("| `%s` | 10^%d |" % (token, power))
    add("")
    add("模糊修饰字符集（可前缀可后缀，判 VERBATIM 照抄）：%s" % " ".join("`%s`" % c for c in MOD_CHARS))
    add("")
    add("指标后缀白名单（被语法吸收，不影响 EXACT 判定）：%s" % "、".join("`%s`" % s for s in METRIC_SUFFIXES))
    add("")

    add("## 三态归一化判定")
    add("")
    add("| 态 | 条件 | 求和 | 阈值判定 | 输出展示 |")
    add("|---|---|---|---|---|")
    add("| `EXACT` | 数值 + 白名单单位，无模糊修饰 | 计入 | 参与 | 回写 `raw` 原文 |")
    add("| `VERBATIM` | 数值可解析，带模糊修饰 | 剔除，进 `excluded_terms` | 不参与 | 原文照抄（含修饰词） |")
    add("| `UNPARSABLE` | 匹配不上语法 | 计 0 | 不参与 | 「未获取」 |")
    add("")

    add("## 发布日期格式白名单")
    add("")
    add("、".join(
        ["YYYY-MM-DD", "YYYY/MM/DD", "YYYY年M月D日", "M月D日", "YYYY-MM", "YYYY年M月",
         "相对日（今天 / 昨天 / 前天 / N天前 / N小时前，锚定 `--today`）"]
    ))
    add("")

    add("## 字面量白名单与黑名单")
    add("")
    add("| 用途 | 取值 |")
    add("|---|---|")
    add("| 数字缺失 | `%s` |" % MISSING_NUM)
    add("| 文案缺失 | `%s` |" % MISSING_TEXT)
    add("| 整体证据缺失（形态 4） | `%s` |" % MISSING_ANY)
    # 替代写法里含 N/A 与 n/a：校验器把含点的行内 token 判为路径引用，
    # 因此这一格不加反引号（语义不丢，其余白名单格仍加）。
    add("| 禁止替代写法（命中即 exit 2） | %s |"
        % "、".join(BANNED_MISSING_ALTERNATIVES))
    add("| 自造修饰词黑名单（剔除 raw 后扫描） | %s |"
        % "、".join(t for t in SELF_MADE_MODIFIER_RE.pattern.strip("()").split("|")))
    add("")

    add("## 判定阈值常量")
    add("")
    add("| 项 | 取值 |")
    add("|---|---|")
    add("| 相对判定阈值 | 该条数值 ≥ 近 %d 条同指标中位数的 %d 倍（含等号） |" % (BASELINE_MAX_ENTRIES, RATIO_FACTOR))
    add("| 基线样本上限 | %d 条（不足按实际条数，证据中标明实际样本数） |" % BASELINE_MAX_ENTRIES)
    add("| 文案长度上限 | %d 字（超出截断并标注） |" % CAPTION_MAX_CHARS)
    add("| 输出条数上限 | %d 条（不足按实际条数，禁止凑数） |" % MAX_OUTPUT_ITEMS)
    add("| 喂料级状态 | `%s` 无有效喂料 / `%s` 充分 / `%s` 不足 |"
        % (FEED_STATE_INVALID, FEED_STATE_SUFFICIENT, FEED_STATE_INSUFFICIENT))
    add("| `baseline_required_for_sufficiency` | `%s`（基线缺失不算喂料不足，裁决 D1） |"
        % str(BASELINE_REQUIRED_FOR_SUFFICIENCY).lower())
    add("")

    add("## 证据形态白名单")
    add("")
    add("| id | 形态 |")
    add("|---|---|")
    add("| `rank` | 榜单形态 |")
    add("| `absolute` | 绝对数值形态 |")
    add("| `relative` | 相对比较形态（必须有基线） |")
    add("| `none` | 无证据形态（`%s`） |" % MISSING_ANY)
    add("")

    return "\n".join(lines) + "\n"


# ==========================================================================
# 16. CLI
# ==========================================================================


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="vpf_common.py",
        description="共享库：常量表导出。默认无业务动作。",
        epilog="退出码：0 正常 / 1 失败 / 2 用法或输入结构错误。")
    parser.add_argument("--dump-tables", action="store_true",
                        help="把常量表导出为 Markdown（references 的同源来源）")
    parser.add_argument("--out", help="导出文件路径（默认写 stdout）")
    args = parser.parse_args(argv)

    if not args.dump_tables:
        parser.print_help()
        return EXIT_OK

    text = dump_tables()
    if args.out:
        try:
            write_out(args.out, text)
        except VpfError as exc:
            sys.stderr.write("错误：%s\n" % exc)
            return EXIT_USAGE
        sys.stderr.write("常量表已导出：%s\n" % args.out)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
