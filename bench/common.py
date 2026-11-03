# -*- coding: utf-8 -*-
"""三个基准脚本共用的部分：调 dsh、解析事件流、写 CSV。

环境变量：
    DSH_BIN              dsh 可执行文件（默认从 PATH 找 `dsh`）
    DEEPSEEK_API_KEY     模型 key（headless 会话要用）
"""
import csv
import json
import os
import subprocess
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "out")


def require_api_key():
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("缺 DEEPSEEK_API_KEY：先设置环境变量再跑（headless 会话要用它）。")


def dsh_command(args):
    """拼出 dsh 的完整命令行。Windows 上 .cmd 需要套一层 cmd /c。"""
    dsh = os.environ.get("DSH_BIN") or "dsh"
    tail = ["--json", "-"]
    if os.name == "nt" and dsh.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", dsh] + list(args) + tail
    return [dsh] + list(args) + tail


def run_task(args, task, timeout=300, cache_price=0.1):
    """跑一次 headless 会话，返回步数 / token / 工具调用数 / 最终回答 / 等价成本。

    等价成本 = 未命中 + 命中 × cache_price + 输出。
    """
    p = subprocess.run(dsh_command(args), input=task.encode("utf-8"),
                       capture_output=True, env=dict(os.environ), timeout=timeout)
    out = p.stdout.decode("utf-8", "replace")
    sid = None
    steps = 0
    miss = 0
    cache = 0
    produced = 0
    tools = 0
    final = ""
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        t = ev.get("type")
        if t == "session":
            sid = ev.get("sessionId")
        elif t == "tool_call":
            tools += 1
        elif t == "status" and ev.get("phase") == "step_end":
            u = ev.get("usage") or {}
            steps += 1
            miss += u.get("inputTokens", 0)
            cache += u.get("cacheReadTokens", 0)
            produced += u.get("outputTokens", 0)
        elif t == "final":
            final = (ev.get("text") or "").strip()
    if steps == 0:
        err = p.stderr.decode("utf-8", "replace").strip()[:200]
        print("  ! 这一跑没有拿到 usage（dsh 退出码 %s）%s" % (p.returncode, ("：" + err) if err else ""),
              file=sys.stderr)
    return {"sid": sid, "steps": steps, "tools": tools, "miss": miss, "cache": cache,
            "out": produced, "final": final, "equiv": miss + cache * cache_price + produced}


def write_csv(path, rows):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("DONE %d 行 -> %s" % (len(rows), path))
