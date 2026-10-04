#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_d2.py -- 辅助：校验并规范化 d2_probe.json 的形状与 attempts==1

职责（蓝图 §6.5）：校验 D2 探测结果文件的形状、attempts==1、status 归一，
      输出规范化的 D2 可用性结论。**不联网、不重试、不伪造。**

为什么单独成脚本（蓝图 §6.5）：验收 E8「探测失败不重试、不伪造」需要一条
可被独立 grep 与单测覆盖的硬约束；混进 normalize_feed 会被淹没。

三问裁决：确定性 ✅ 纯字段校验｜理解 ❌｜失败 ✅ 每个非法形状都有确定报错。

CLI:
    python3 probe_d2.py --probe d2_probe.json [--json]

退出码：
  0  形状合法（available 可能为 false——探测失败按无工具处理，不是报错）
  2  形状非法 / attempts != 1（显式报错，不默认 false 继续跑）
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import vpf_common as V  # noqa: E402

EXIT_OK, EXIT_USAGE = 0, 2

# 探测结论的合法 status 取值
STATUS_OK = "ok"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"
ALLOWED_STATUS = (STATUS_OK, STATUS_TIMEOUT, STATUS_ERROR)
# 一律归一为「无工具」的 status（E8：探测失败按无 D2 工具处理）
DEGRADED_STATUS = (STATUS_TIMEOUT, STATUS_ERROR)

EXPECTED_SHAPE = '{"available": true, "tool": "agent-browser", "attempts": 1, "status": "ok"}'


def validate(data):
    """校验探测结果形状，返回规范化结论。失败抛 VpfError。

    规则：
      - 缺字段 / 类型不对 -> 显式报错（不默认 false 继续跑）
      - attempts != 1       -> 显式报错（探测禁止重试）
      - status ∈ {timeout, error} -> 归一为 available=false，attempts 仍须为 1
    """
    if not isinstance(data, dict):
        raise V.VpfError(
            "d2_probe.json 根节点必须是对象，实际是 {}。\n期望形状：{}".format(
                type(data).__name__, EXPECTED_SHAPE))

    if "available" not in data:
        raise V.VpfError(
            "d2_probe.json 缺少必填字段 'available'。\n期望形状：{}\n"
            "缺字段不默认 false 继续跑——静默退化会让「探测失败」与「未探测」不可区分。".format(
                EXPECTED_SHAPE))
    if not isinstance(data["available"], bool):
        raise V.VpfError(
            "d2_probe.json 的 'available' 必须是布尔值，实际是 {}。\n期望形状：{}".format(
                type(data["available"]).__name__, EXPECTED_SHAPE))

    for key, types in (("tool", (str,)), ("status", (str,)), ("attempts", (int,))):
        if key not in data:
            raise V.VpfError(
                "d2_probe.json 缺少必填字段 '{}'。\n期望形状：{}".format(key, EXPECTED_SHAPE))
        if key == "attempts" and isinstance(data[key], bool):
            raise V.VpfError(
                "d2_probe.json 的 'attempts' 必须是整数（实际拿到布尔值）。\n期望形状：{}".format(
                    EXPECTED_SHAPE))
        if not isinstance(data[key], types):
            raise V.VpfError(
                "d2_probe.json 的 '{}' 类型非法：期望 {}，实际 {}。\n期望形状：{}".format(
                    key,
                    "/".join(t.__name__ for t in types),
                    type(data[key]).__name__,
                    EXPECTED_SHAPE))

    status = data["status"]
    if status not in ALLOWED_STATUS:
        raise V.VpfError(
            "d2_probe.json 的 'status' 取值非法：'{}'。\n"
            "只接受 {}。\n期望形状：{}".format(
                status, " / ".join(ALLOWED_STATUS), EXPECTED_SHAPE))

    if data["attempts"] != 1:
        raise V.VpfError(
            "d2_probe.json 的 'attempts' = {}，必须等于 1。\n"
            "探测禁止重试：一次失败即按无工具处理，不得反复重试或伪造读取结果"
            "（验收 E8）。\n期望形状：{}".format(data["attempts"], EXPECTED_SHAPE))

    available = data["available"]
    normalized_status = status
    if status in DEGRADED_STATUS:
        # 探测超时/失败 -> 一律按无工具处理。这是「按无工具处理」，不是报错。
        available = False
        normalized_status = status

    return {
        "available": available,
        "tool": data["tool"],
        "attempts": data["attempts"],
        "status": normalized_status,
        "note": ("探测未通过（status={}），按无 D2 工具处理；禁止重试。"
                 .format(status) if status in DEGRADED_STATUS else ""),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="probe_d2.py",
        description="校验并规范化 d2_probe.json（不联网、不重试、不伪造）",
        epilog="退出码：0 形状合法（available 可能为 false）/ 2 形状非法或 attempts!=1。")
    parser.add_argument("--probe", required=True, help="d2_probe.json 路径")
    parser.add_argument("--json", action="store_true", help="stdout 输出紧凑 JSON")
    args = parser.parse_args(argv)

    p = pathlib.Path(str(args.probe))
    if not p.exists():
        sys.stderr.write(
            "错误：找不到 D2 探测结果文件：{}\n"
            "探测由模型用运行环境已有的网页读取能力做一次，把结论写入该文件；"
            "本脚本只校验形状，绝不代替探测、绝不重试。\n期望形状：{}\n"
            "若确定没有网页读取能力，直接不传 --probe 即可（按无工具处理）。\n".format(
                args.probe, EXPECTED_SHAPE))
        return EXIT_USAGE

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.stderr.write("错误：d2_probe.json 不是合法 JSON：{}\n{}\n期望形状：{}\n".format(
            args.probe, exc, EXPECTED_SHAPE))
        return EXIT_USAGE

    try:
        result = validate(data)
    except V.VpfError as exc:
        sys.stderr.write("错误：{}\n".format(exc))
        return EXIT_USAGE

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.stdout.write(V.dumps_canonical(result, compact=args.json) + "\n")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
