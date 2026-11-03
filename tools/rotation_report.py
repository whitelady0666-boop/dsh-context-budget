#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轮转前后的 token 成本对比：读 DSH 会话日志，出图 + 出数。

用法：
    python rotation_report.py                       # DSH_HOME/sessions 下最新 4 个会话
    python rotation_report.py --last 5 --out out/
    python rotation_report.py --session <会话目录> --session <会话目录> --label 长会话,轮转1

输出 rotation-dashboard.png 和 rotation-data.json。
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from selfcheck import Curve, DEFAULT_RATIO  # noqa: E402

SESSION_FILE = "session.v4.jsonl.zstd"

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def default_root():
    home = os.environ.get("DSH_HOME")
    base = Path(home) if home else Path.home() / ".dsh"
    return base / "sessions"


def _key(path):
    name = path.parent.name
    if name.startswith("session-"):
        name = name[len("session-"):]
    return name[:8]


def pick_sessions(args):
    """返回 [(key, path, label)]，按时间从旧到新。"""
    if args.session:
        out = []
        labels = [x.strip() for x in (args.label or "").split(",") if x.strip()]
        for i, raw in enumerate(args.session):
            p = Path(raw).expanduser()
            if p.is_dir():
                p = p / SESSION_FILE
            label = labels[i] if i < len(labels) else _key(p)
            out.append((_key(p), p, label))
        return out

    root = Path(args.root).expanduser() if args.root else default_root()
    if not root.is_dir():
        raise SystemExit("会话目录不存在：%s（用 --root 指过去）" % root)
    found = sorted(root.rglob(SESSION_FILE), key=lambda p: p.stat().st_mtime)
    found = found[-args.last:]
    out = []
    for i, p in enumerate(found):
        if len(found) == 1:
            label = "会话"
        elif i == 0:
            label = "最早（未轮转）"
        else:
            label = "轮转 %d" % i
        out.append((_key(p), p, label))
    return out


def load(spec):
    curves = []
    for key, path, label in spec:
        if not path.exists():
            print("missing", path)
            continue
        curves.append({"key": key, "label": label, "curve": Curve(path, DEFAULT_RATIO)})
    return curves


def fit(points):
    xs = np.array([p[0] for p in points], dtype=float)
    ys = np.array([p[1] for p in points], dtype=float)
    m, b = np.polyfit(xs, ys, 1)
    pred = m * xs + b
    ss_res = float(((ys - pred) ** 2).sum())
    ss_tot = float(((ys - ys.mean()) ** 2).sum()) or 1.0
    return float(m), float(b), 1.0 - ss_res / ss_tot


def main(argv=None):
    ap = argparse.ArgumentParser(description="轮转前后 token 成本对比")
    ap.add_argument("--root", default="", help="会话根目录（默认 $DSH_HOME/sessions）")
    ap.add_argument("--last", type=int, default=4, help="取最新 N 个会话（默认 4）")
    ap.add_argument("--session", action="append", default=[], help="指定会话目录或 .zstd，可重复")
    ap.add_argument("--label", default="", help="配合 --session 的逗号分隔标签")
    ap.add_argument("--out", default=".", help="输出目录（默认当前目录）")
    args = ap.parse_args(argv)

    spec = pick_sessions(args)
    curves = load(spec)
    if not curves:
        raise SystemExit("没有可读的会话日志")

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    stats = []
    for item in curves:
        c = item["curve"]
        eq = c.equiv or 1.0
        stats.append({
            "key": item["key"],
            "label": item["label"],
            "steps": c.n,
            "turns": c.turns,
            "first_ctx": c.first_ctx,
            "last_ctx": c.last_ctx,
            "peak_ctx": max((r["ctx"] for r in c.rows), default=0),
            "avg_ctx": c.avg_ctx,
            "equiv": c.equiv,
            "per_step": c.equiv_per_step,
            "first_step": c.rows[0]["equiv"] if c.rows else 0,
            "hit": c.hit_rate,
            "cache_share": c.cache * DEFAULT_RATIO / eq,
            "miss_share": c.miss / eq,
            "out_share": c.out / eq,
            "ratio_obs": c.ratio_observed,
        })
        s = stats[-1]
        print(f'{s["key"]:<9} {s["label"]:<14} steps={s["steps"]:>4}  '
              f'ctx {s["first_ctx"]:>7,} -> {s["last_ctx"]:>8,}  avg={s["avg_ctx"]:>8,.0f}  '
              f'equiv={s["equiv"]:>12,.0f}  per_step={s["per_step"]:>8,.0f}  '
              f'hit={s["hit"]:.3f}  out%={s["out_share"]*100:.0f}')

    all_rows = [r for item in curves for r in item["curve"].rows]
    m_all, b_all, r2_all = fit([(r["ctx"], r["equiv"]) for r in all_rows])
    m_long, b_long, r2_long = fit([(r["ctx"], r["equiv"]) for r in curves[0]["curve"].rows])
    short_rows = [r for item in curves[1:] for r in item["curve"].rows]
    m_short, b_short, r2_short = fit([(r["ctx"], r["equiv"]) for r in short_rows])
    print(f"\nfit all   : equiv = {m_all:.5f} * ctx + {b_all:,.0f}   R2={r2_all:.3f}")
    print(f"fit 首个会话: equiv = {m_long:.5f} * ctx + {b_long:,.0f}   R2={r2_long:.3f}")
    if short_rows:
        print(f"fit 轮转后 : equiv = {m_short:.5f} * ctx + {b_short:,.0f}   R2={r2_short:.3f}")

    base = curves[0]["curve"]
    start_ctx = base.last_ctx
    growth = (base.last_ctx - base.first_ctx) / (base.n - 1) if base.n > 1 else 0
    post_steps = sum(item["curve"].n for item in curves[1:])
    post_actual = sum(item["curve"].equiv for item in curves[1:])
    post_ctx = [r["ctx"] for item in curves[1:] for r in item["curve"].rows]
    post_avg_ctx = float(np.mean(post_ctx)) if post_ctx else 0.0

    cf_total = 0.0
    cf_cum = []
    if post_steps:
        for i in range(post_steps):
            cf_total += m_all * (start_ctx + growth * (i + 1)) + b_all
            cf_cum.append(cf_total)
        saved = cf_total - post_actual
        print(f"\n轮转后 {post_steps} 步：实际 {post_actual:,.0f}"
              f"（每步 {post_actual / post_steps:,.0f}，平均上下文 {post_avg_ctx:,.0f}）")
        print(f"不轮转反事实：从 {start_ctx:,} 起每步 +{growth:,.0f} 上下文 → {cf_total:,.0f}")
        print(f"节省 {saved:,.0f}（{saved / cf_total * 100 if cf_total else 0:.1f}%）")

    colors = ["#d62728", "#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf"]
    fig, axes = plt.subplots(3, 1, figsize=(12, 14.5), gridspec_kw={"height_ratios": [1, 1.15, 1.1]})

    ax = axes[0]
    offset = 0
    for i, item in enumerate(curves):
        rows = item["curve"].rows
        ax.plot(np.arange(len(rows)) + offset, [r["ctx"] / 1000 for r in rows],
                color=colors[i % len(colors)], lw=1.4, label=item["label"])
        if i:
            ax.axvline(offset, color="#888", ls="--", lw=1)
        offset += len(rows)
    ax.set_ylabel("上下文 (K tokens)")
    ax.set_xlabel("步（会话按时间串联）")
    ax.set_title("A. 上下文锯齿：轮转把上下文打回起点")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=0.3)

    ax = axes[1]
    for i, item in enumerate(curves):
        rows = item["curve"].rows
        ax.scatter([r["ctx"] / 1000 for r in rows], [r["equiv"] for r in rows],
                   s=10, alpha=0.5, color=colors[i % len(colors)], label=item["label"])
    xs = np.linspace(0, max((r["ctx"] for r in all_rows), default=1) / 1000, 100)
    ax.plot(xs, m_all * xs * 1000 + b_all, color="black", lw=2,
            label=f"拟合 {m_all:.4f}×ctx + {b_all:,.0f}")
    ax.set_ylim(0, max(90000, np.percentile([r["equiv"] for r in all_rows], 99.5) * 1.1))
    ax.set_xlabel("上下文 (K tokens)")
    ax.set_ylabel("单步等价 token")
    ax.set_title("B. 单步成本 ≈ %.3f × 上下文 + 固定开销" % m_all)
    ax.legend(fontsize=9, loc="upper left")
    ax.grid(alpha=0.3)

    ax = axes[2]
    if post_steps:
        actual_cum = np.cumsum([r["equiv"] for item in curves[1:] for r in item["curve"].rows])
        ax.plot(np.arange(1, post_steps + 1), actual_cum / 1e6, color="#1f77b4", lw=2,
                label="实际：轮转后 %d 步" % post_steps)
        ax.plot(np.arange(1, post_steps + 1), np.array(cf_cum) / 1e6, color="#d62728", lw=2,
                ls="--", label="反事实：不轮转")
        ax.set_title("C. 同样 %d 步：轮转 %.1fM，不轮转 %.1fM" % (
            post_steps, post_actual / 1e6, cf_total / 1e6))
        ax.set_xlabel("轮转后的步数")
        ax.set_ylabel("累计等价 token（百万）")
        ax.legend(fontsize=9)
        ax.grid(alpha=0.3)

    fig.tight_layout()
    chart = out_dir / "rotation-dashboard.png"
    fig.savefig(chart, dpi=130)
    print("wrote %s" % chart)

    payload = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "ratio": DEFAULT_RATIO,
        "sessions": stats,
        "fit": {"all": [m_all, b_all, r2_all], "first": [m_long, b_long, r2_long],
                "rotated": [m_short, b_short, r2_short]},
        "counterfactual": {
            "post_steps": post_steps,
            "post_actual": post_actual,
            "post_avg_ctx": post_avg_ctx,
            "start_ctx": start_ctx,
            "growth": growth,
            "cf_total": cf_total,
            "saved": cf_total - post_actual,
        },
    }
    data = out_dir / "rotation-data.json"
    data.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote %s" % data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
