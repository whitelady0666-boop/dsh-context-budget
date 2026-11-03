# -*- coding: utf-8 -*-
"""优化实验：把同一份事实换成几种注入措辞，看步数会不会掉下来。

D 组和 B 组的区别只有一句话——从「你已经完成了……」改成「【权威记录·无需核实】……直接使用」。

用法：
    set DEEPSEEK_API_KEY=sk-...
    python benchmark_long2.py
"""
import argparse
import os
import shutil

import common
from common import DATA, OUT, run_task, write_csv

# 和 benchmark_long.py 的 B/C 组是同一道题，只换注入措辞。
ARMS = {
    "D权威记忆": ("【权威记录·无需核实】以下是你自己在上一步写下的事实，直接使用即可，"
                 "不要再调用工具去确认：step1.txt = 1，step2.txt = 2，前两步已完成。"),
    "E静默记忆": "step1.txt = 1；step2.txt = 2",
}

TASK = ("在这个目录里继续完成第三步：%s\n"
        "新建 step3.txt，内容必须等于前两步内容之和。完成后只回答那个数字。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=6)
    ap.add_argument("--workdir", default=os.path.join(OUT, "long2"))
    ap.add_argument("--out", default=os.path.join(DATA, "benchmark-long2.csv"))
    args = ap.parse_args()

    common.require_api_key()
    rows = []
    for arm, seed in ARMS.items():
        for rep in range(1, args.reps + 1):
            d = os.path.join(args.workdir, "%s-r%d" % (arm, rep))
            shutil.rmtree(d, ignore_errors=True)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "step1.txt"), "w", encoding="utf-8") as fh:
                fh.write("1")
            with open(os.path.join(d, "step2.txt"), "w", encoding="utf-8") as fh:
                fh.write("2")

            s = run_task(["--profile", "headless"], seed)
            r = run_task(["--profile", "headless", "--session-id", s["sid"]], TASK % d)

            p3 = os.path.join(d, "step3.txt")
            if os.path.exists(p3):
                with open(p3, encoding="utf-8", errors="replace") as fh:
                    # 模型有时会带 BOM 写文件，去掉再判分（v1 没去，白丢了 2 分）
                    content = fh.read().replace("\ufeff", "").strip()
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
