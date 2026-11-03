#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用真实会话日志，模拟“上下文预算”对 token 成本的影响。

用法：
    python simulate.py <session.v4.jsonl.zstd> [--cache-price 0.1] [--csv out.csv]

原理：
    每次模型调用都会把当前上下文从缓存里重读一遍。
    设某轮有 S 步、每步上下文 C，则
        成本 ≈ C × S × 缓存折扣 + 未命中全价 + 输出
    如果把上下文压在预算 B 以内，就该用 min(C, B) 来算。
"""
import argparse
import collections
import csv
import json
import statistics
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import zstandard as zstd
except ImportError:
    print("需要 zstandard：pip install zstandard", file=sys.stderr)
    raise SystemExit(1)


def load_turns(path):
    raw = zstd.ZstdDecompressor().stream_reader(open(path, "rb")).read().decode("utf-8", "replace")
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    events.sort(key=lambda e: e.get("seq", 0))
    turns = collections.defaultdict(lambda: {"miss": 0, "cache": 0, "out": 0, "steps": 0})
    for ev in events:
        if ev.get("type") != "assistant/message":
            continue
        d = ev.get("data", {})
        t, u = d.get("turn"), d.get("usage") or {}
        if t is None or not u:
            continue
        turns[t]["miss"] += u.get("inputTokens", 0)
        turns[t]["cache"] += u.get("cacheReadTokens", 0)
        turns[t]["out"] += u.get("outputTokens", 0)
        turns[t]["steps"] += 1
    return [(t, v) for t, v in sorted(turns.items()) if v["steps"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--cache-price", type=float, default=0.1, help="缓存命中价 / 未命中价，默认 0.1")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--budgets", default="10000,20000,30000,46000,80000,150000,230000")
    args = ap.parse_args()

    turns = load_turns(args.session)
    rows = []
    for t, v in turns:
        ctx = v["cache"] / v["steps"]
        rows.append({
            "turn": t, "steps": v["steps"], "ctx": int(ctx),
            "miss": v["miss"], "cache": v["cache"], "out": v["out"],
            "equiv": v["miss"] + v["cache"] * args.cache_price + v["out"],
        })

    actual = sum(r["equiv"] for r in rows)
    print("会话轮次: %d" % len(rows))
    print("总步数  : %d（平均 %.1f 步/轮）"
          % (sum(r["steps"] for r in rows), sum(r["steps"] for r in rows) / len(rows)))
    print("平均上下文: %s" % format(int(statistics.mean(r["ctx"] for r in rows)), ","))
    print("实测总等价成本: %s" % format(int(actual), ","))
    print()

    print("%-10s %14s %10s %12s" % ("预算", "模拟总成本", "节省", "每轮均值"))
    print("-" * 52)
    for b in [int(x) for x in args.budgets.split(",")]:
        sim = sum(min(r["ctx"], b) * r["steps"] * args.cache_price
                  + r["out"] + min(r["miss"], 3000) for r in rows)
        print("%-10s %14s %9.1f%% %12s"
              % (format(b, ","), format(int(sim), ","),
                 (1 - sim / actual) * 100, format(int(sim / len(rows)), ",")))

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print("\n已写出 %s（%d 行）" % (args.csv, len(rows)))


if __name__ == "__main__":
    main()
