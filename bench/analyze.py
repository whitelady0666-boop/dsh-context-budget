# -*- coding: utf-8 -*-
"""把 data/ 下的 CSV 汇总成 README 里那张表，顺便验证数字对不对。

用法：
    python analyze.py
"""
import csv
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def summarize(name):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        print("跳过 %s（没有这个文件）" % name)
        return
    with open(path, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    arms = {}
    for r in rows:
        arms.setdefault(r["arm"], []).append(r)
    print("===== %s —— %d 次运行" % (name, len(rows)))
    print("%-10s %4s %8s %7s %10s %8s" % ("组", "n", "等价成本", "步数", "每步上下文", "完成"))
    for arm, rs in arms.items():
        eq = [float(r["equiv"]) for r in rs]
        steps = [float(r["steps"]) for r in rs]
        ctx = [int(r["miss"]) + int(r["cache"]) for r in rs]
        per_step = statistics.mean([c / s if s else 0 for c, s in zip(ctx, steps)])
        correct = sum(int(r["correct"]) for r in rs)
        print("%-10s %4d %8.0f %7.2f %10.0f %7d/%d"
              % (arm, len(rs), statistics.mean(eq), statistics.mean(steps), per_step,
                 correct, len(rs)))
    print()


if __name__ == "__main__":
    for f in ("benchmark.csv", "benchmark-long.csv", "benchmark-long2.csv"):
        summarize(f)
