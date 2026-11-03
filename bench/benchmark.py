# -*- coding: utf-8 -*-
"""基准测试：同一批问题，三种上下文配置，测 步数 / 上下文 / 等价成本 / 正确率。

用法：
    set DEEPSEEK_API_KEY=sk-...            # PowerShell: $env:DEEPSEEK_API_KEY="sk-..."
    python benchmark.py --memory C:/path/to/MEMORY.md

    --memory 是你自己的记忆文件。不给就只跑 A 组（无记忆）。
    8 道题是示例，按自己机器改 QS 里的问题和正则。
"""
import argparse
import os
import re
import time

import common
from common import DATA, run_task, write_csv

# “数 .md 文件”那题要数的目录，改成你自己的
ARTICLES = r"E:\DSH-work\article"

# (问题, 判分正则) —— 通不过正则该题记 0 分
QS = [
    ("AstrBot 的 Web 面板跑在哪个端口？只回答端口号。", r"6185"),
    ("NapCat 的裸通道（本机脚本直连的那个）端口是多少？只回答端口号。", r"3001"),
    ("桥接组件的工作区路径是什么？只回答路径。", r"DSH-work"),
    ("AstrBot 的数据库文件名是什么？只回答文件名。", r"data_v4\.db"),
    ("这台机器的 Windows 控制台默认编码是什么，害得我们踩过坑？只回答编码名。", r"gbk|936|gb2312"),
    ("%s 目录下有几个 .md 文件？只回答数字。" % ARTICLES, r"^\s*3\s*$"),
    ("现在这一刻，本机 3001 端口有没有进程在监听？只回答有或没有。", r"有|是|监听"),
    ("NapCat 重启之后，通常还需要人做什么才能恢复？只回答一个动作。", r"扫码|二维码|扫"),
]


def build_arms(memory_path):
    """A 无记忆 / B 只有记忆 / C 记忆 + 一大段背景。"""
    arms = [("A无记忆", None)]
    if not memory_path:
        print("没给 --memory，只跑 A 组。")
        return arms
    memory = open(memory_path, encoding="utf-8").read()
    seed_b = "以下是你的长期记忆，请记住，之后回答问题时依据它。\n\n" + memory
    seed_c = seed_b + "\n\n补充背景（过往讨论摘要）：\n" + "\n".join(
        "第%d次讨论涉及端口配置、沙箱权限、编码处理、插件挂载、缓存命中率与上下文预算等内容。" % i
        for i in range(600))
    arms += [("B有记忆", seed_b), ("C长背景", seed_c)]
    return arms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory", default=None, help="记忆文件路径（例如你自己的 MEMORY.md）")
    ap.add_argument("--reps", type=int, default=2, help="每种配置重复几次（默认 2）")
    ap.add_argument("--out", default=os.path.join(DATA, "benchmark.csv"))
    args = ap.parse_args()

    common.require_api_key()
    arms = build_arms(args.memory)
    rows = []
    t0 = time.time()
    for arm, seed in arms:
        for rep in range(1, args.reps + 1):
            for qi, (q, pat) in enumerate(QS, 1):
                try:
                    if seed:
                        # 先用一轮空会话把记忆压进前缀，再在同一会话里问题
                        s = run_task(["--profile", "headless"], seed)
                        r = run_task(["--profile", "headless", "--session-id", s["sid"]], q)
                    else:
                        r = run_task(["--profile", "headless"], q)
                except Exception as exc:  # 单次失败不打断整轮
                    print("  ! 第 %d 题失败：%s" % (qi, exc), flush=True)
                    r = {"steps": 0, "miss": 0, "cache": 0, "out": 0,
                         "tools": 0, "final": "", "equiv": 0}
                ok = 1 if re.search(pat, r["final"], re.I) else 0
                rows.append({"arm": arm, "rep": rep, "q": qi, "steps": r["steps"],
                             "tools": r["tools"], "miss": r["miss"], "cache": r["cache"],
                             "out": r["out"], "equiv": round(r["equiv"]),
                             "correct": ok, "final": r["final"][:80]})
                print("%s rep%d q%d: steps=%d tools=%d equiv=%d ok=%d"
                      % (arm, rep, qi, r["steps"], r["tools"], r["equiv"], ok), flush=True)

    write_csv(args.out, rows)
    print("共 %d 次运行，耗时 %.0fs" % (len(rows), time.time() - t0))


if __name__ == "__main__":
    main()
