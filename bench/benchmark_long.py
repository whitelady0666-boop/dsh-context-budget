# -*- coding: utf-8 -*-
"""长任务实验：记忆的“精度”是否影响步数？

任务：目录里已有 step1.txt=1、step2.txt=2，让模型新建 step3.txt = 3。
判分直接读磁盘上的 step3.txt，不看它怎么说。

用法：
    set DEEPSEEK_API_KEY=sk-...
    python benchmark_long.py
"""
import argparse
import os
import shutil

import common
from common import DATA, OUT, run_task, write_csv

SEEDS = {
    "A无记忆": None,
    # 旧措辞：陈述事实 + “请记住这个进度”。模型读完常会自己去核实一遍。
    "B精确记忆": "你已经在这个目录里完成了前两步：step1.txt 的内容是 1，step2.txt 的内容是 2。请记住这个进度。",
    "C模糊记忆": "你已经在这个目录里做了几步操作，但当时没有记录具体数值，只记得进度已经推进了两步。请记住这一点。",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=6)
    ap.add_argument("--workdir", default=os.path.join(OUT, "long"))
    ap.add_argument("--out", default=os.path.join(DATA, "benchmark-long.csv"))
    args = ap.parse_args()

    common.require_api_key()
    rows = []
    for arm, seed in SEEDS.items():
        for rep in range(1, args.reps + 1):
            d = os.path.join(args.workdir, "%s-r%d" % (arm, rep))
            shutil.rmtree(d, ignore_errors=True)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "step1.txt"), "w", encoding="utf-8") as fh:
                fh.write("1")
            with open(os.path.join(d, "step2.txt"), "w", encoding="utf-8") as fh:
                fh.write("2")
            task = ("在这个目录里继续完成第三步：%s\n"
                    "新建 step3.txt，内容必须等于前两步内容之和。完成后只回答那个数字。") % d
            if seed:
                s = run_task(["--profile", "headless"], seed)
                r = run_task(["--profile", "headless", "--session-id", s["sid"]], task)
            else:
                r = run_task(["--profile", "headless"], task)

            p3 = os.path.join(d, "step3.txt")
            if os.path.exists(p3):
                with open(p3, encoding="utf-8", errors="replace") as fh:
                    content = fh.read().strip()
            else:
                content = "(未创建)"
            ok = 1 if content == "3" else 0
            rows.append({"arm": arm, "rep": rep, "steps": r["steps"], "tools": r["tools"],
                         "miss": r["miss"], "cache": r["cache"], "out": r["out"],
                         "equiv": round(r["equiv"]), "step3": content, "correct": ok,
                         "final": r["final"][:60]})
            print("%-8s rep%d: steps=%d tools=%d equiv=%d step3=%s ok=%d"
                  % (arm, rep, r["steps"], r["tools"], r["equiv"], content, ok), flush=True)

    write_csv(args.out, rows)


if __name__ == "__main__":
    main()
