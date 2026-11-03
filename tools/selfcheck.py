#!/usr/bin/env python3
"""selfcheck.py — turn your own DSH session log into a per-step cost curve.

Why: running a 12-minute benchmark tells you what a *synthetic* workload costs.
Reading your own session tells you what *you* actually pay, step by step, and
shows which lever moves the bill.

Cost model (same as the plugin):
    context_i  = inputTokens + cacheReadTokens          (tokens re-sent per step)
    equiv_i    = inputTokens + cacheReadTokens * ratio + outputTokens
    ratio      = cache hit price / miss price (default 0.1 for DeepSeek)

Usage:
    python selfcheck.py                      # newest session under the DSH home
    python selfcheck.py --last 5             # one summary line for the 5 newest
    python selfcheck.py <dir-or-file>        # a session dir or session.v4.jsonl.zstd
    python selfcheck.py a b c                # compare several sessions
    python selfcheck.py --csv curve.csv      # also dump the curve

Options:
    --ratio R      cache-read price ratio (default 0.1)
    --root DIR     sessions root (default: $DSH_HOME/sessions or ~/.dsh/sessions)
    --every N      print every Nth step of the curve (default: auto)
    --csv PATH     write per-step rows to a CSV

Reading the log needs `pip install zstandard`, or the `zstd` CLI on PATH.
No network, no API key, ~1 second.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SESSION_FILE = "session.v4.jsonl.zstd"
DEFAULT_RATIO = 0.1


# ---------------------------------------------------------------- log reading

def _read_zstd(path: Path) -> bytes:
    """Read every zstd *frame* in the file.

    DSH appends one frame per flush, so a plain `ZstdDecompressor().decompress()`
    only returns the first frame (usually just the session header). A stream
    reader walks all of them.
    """
    try:
        import zstandard  # type: ignore
    except ImportError:
        zstandard = None

    if zstandard is not None:
        with open(path, "rb") as fh:
            return zstandard.ZstdDecompressor().stream_reader(fh).read()

    if shutil.which("zstd"):
        out = subprocess.run(["zstd", "-dc", str(path)], capture_output=True)
        if out.returncode == 0:
            return out.stdout
        raise SystemExit(f"zstd failed on {path}: {out.stderr.decode(errors='replace')}")

    raise SystemExit(
        "Cannot read .zstd logs. Install one of:\n"
        "    pip install zstandard\n"
        "    (or put the `zstd` CLI on PATH)"
    )


def read_events(path: Path) -> list[dict]:
    data = _read_zstd(path) if path.suffix == ".zstd" else path.read_bytes()
    events = []
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def resolve(path_arg: str) -> Path:
    p = Path(path_arg).expanduser()
    if p.is_dir():
        candidate = p / SESSION_FILE
        if not candidate.exists():
            raise SystemExit(f"{p} has no {SESSION_FILE}")
        return candidate
    if not p.exists():
        raise SystemExit(f"no such session: {p}")
    return p


def default_root() -> Path:
    home = os.environ.get("DSH_HOME")
    base = Path(home) if home else Path.home() / ".dsh"
    return base / "sessions"


def newest_sessions(root: Path, count: int) -> list[Path]:
    if not root.is_dir():
        raise SystemExit(f"sessions root not found: {root}\n(point --root at your DSH sessions dir)")
    found = [p for p in root.rglob(SESSION_FILE)]
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return found[:count]


# ---------------------------------------------------------------- accounting

class Curve:
    """Per-step usage for one session."""

    def __init__(self, path: Path, ratio: float = DEFAULT_RATIO):
        self.path = path
        self.ratio = ratio
        self.rows: list[dict] = []
        self.session_id = path.parent.name
        self.events = 0
        for ev in read_events(path):
            self.events += 1
            if ev.get("type") == "session":
                self.session_id = ev.get("id", self.session_id)
                continue
            if ev.get("type") != "assistant/message":
                continue
            usage = (ev.get("data") or {}).get("usage")
            if not usage:
                continue
            miss = int(usage.get("inputTokens") or 0)
            cache = int(usage.get("cacheReadTokens") or 0)
            out = int(usage.get("outputTokens") or 0)
            ctx = miss + cache
            self.rows.append({
                "turn": (ev.get("data") or {}).get("turn"),
                "step": (ev.get("data") or {}).get("step"),
                "ctx": ctx,
                "miss": miss,
                "cache": cache,
                "out": out,
                "equiv": miss + cache * ratio + out,
            })
        cum = 0.0
        for row in self.rows:
            cum += row["equiv"]
            row["cum"] = cum

    # -- aggregates
    @property
    def n(self) -> int:
        return len(self.rows)

    @property
    def miss(self) -> int:
        return sum(r["miss"] for r in self.rows)

    @property
    def cache(self) -> int:
        return sum(r["cache"] for r in self.rows)

    @property
    def out(self) -> int:
        return sum(r["out"] for r in self.rows)

    @property
    def ctx_sum(self) -> int:
        return sum(r["ctx"] for r in self.rows)

    @property
    def equiv(self) -> float:
        return sum(r["equiv"] for r in self.rows)

    @property
    def raw(self) -> int:
        return self.miss + self.cache + self.out

    @property
    def hit_rate(self) -> float:
        denom = self.miss + self.cache
        return self.cache / denom if denom else 0.0

    @property
    def turns(self) -> int:
        return len({r["turn"] for r in self.rows if r["turn"] is not None})

    @property
    def avg_ctx(self) -> float:
        return self.ctx_sum / self.n if self.n else 0.0

    @property
    def equiv_per_step(self) -> float:
        return self.equiv / self.n if self.n else 0.0

    @property
    def ratio_observed(self) -> float:
        """Effective cost per token of context, per step.

        equiv_i ~= m * ctx_i, so m = sum(equiv) / sum(ctx). With a 0.1 cache
        ratio and ~99% hit rate you should land near 0.11.
        """
        return self.equiv / self.ctx_sum if self.ctx_sum else 0.0

    @property
    def last_ctx(self) -> int:
        return self.rows[-1]["ctx"] if self.rows else 0

    @property
    def first_ctx(self) -> int:
        return self.rows[0]["ctx"] if self.rows else 0

    def shares(self) -> dict[str, float]:
        total = self.equiv or 1.0
        return {
            "miss": self.miss / total,
            "cache": self.cache * self.ratio / total,
            "out": self.out / total,
        }


# ---------------------------------------------------------------- printing

def fmt(n: float) -> str:
    return f"{n:,.0f}"


def short(n: float) -> str:
    if n >= 1e6:
        return f"{n/1e6:.2f}M"
    if n >= 1e3:
        return f"{n/1e3:.1f}K"
    return f"{n:.0f}"


def print_summary(c: Curve, ratio: float) -> None:
    print(f"session   {c.session_id}")
    print(f"          {c.path}")
    if not c.n:
        print("          no assistant/message with usage — nothing to measure")
        return
    sh = c.shares()
    print(f"steps     {c.n}  ({c.turns} turns)")
    print(f"raw       {short(c.raw)} tokens   (miss {short(c.miss)} / cache {short(c.cache)} / out {short(c.out)})")
    print(f"hit rate  {c.hit_rate*100:.2f}%")
    print(f"equiv     {fmt(c.equiv)}       @ cache ratio {ratio:g}")
    print("          where it goes: "
          f"cache {sh['cache']*100:.0f}%  output {sh['out']*100:.0f}%  miss {sh['miss']*100:.0f}%")
    print(f"context   {short(c.first_ctx)} -> {short(c.last_ctx)}  (avg {short(c.avg_ctx)})")
    print(f"per step  {fmt(c.equiv_per_step)} equiv   = {c.ratio_observed:.4f} x context")
    if c.turns:
        print(f"per turn  {fmt(c.equiv / c.turns)} equiv")

    print("\ncost of one step at a given context (the lever):")
    print(f"    {'context':>9}  {'per step':>10}  {'3-step ask':>12}")
    for ctx in (10_000, 20_000, 40_000, 80_000, 160_000, 240_000):
        one = c.ratio_observed * ctx
        print(f"    {short(ctx):>9}  {fmt(one):>10}  {fmt(one*3):>12}")


def print_curve(c: Curve, every: int | None) -> None:
    if not c.n:
        return
    step = every or max(1, -(-c.n // 40))  # ceil(n/40)
    tail = set(range(max(0, c.n - 5), c.n))
    print(f"\nper-step curve (every {step} of {c.n}, last 5 always shown)")
    print(f"    {'#':>4} {'turn':>4} {'step':>4} {'context':>9} {'miss':>8} {'cache':>9} {'out':>6} {'equiv':>9} {'cum':>9}")
    prev_ctx = None
    for i, r in enumerate(c.rows):
        if i % step and i not in tail:
            continue
        jump = "" if prev_ctx is None else f"  (+{short(r['ctx']-prev_ctx)})"
        prev_ctx = r["ctx"]
        print(f"    {i+1:>4} {str(r['turn']):>4} {str(r['step']):>4} {short(r['ctx']):>9} "
              f"{short(r['miss']):>8} {short(r['cache']):>9} {short(r['out']):>6} "
              f"{fmt(r['equiv']):>9} {fmt(r['cum']):>9}{jump}")


def write_csv(c: Curve, path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["i", "turn", "step", "context", "miss", "cache", "out", "equiv", "cum_equiv"])
        for i, r in enumerate(c.rows, 1):
            w.writerow([i, r["turn"], r["step"], r["ctx"], r["miss"], r["cache"],
                        r["out"], round(r["equiv"], 1), round(r["cum"], 1)])
    print(f"\nwrote {path}")


def compare(curves: list[Curve]) -> None:
    head = f"{'session':<22}{'steps':>6}{'turns':>6}{'avg ctx':>9}{'last ctx':>10}{'equiv':>11}{'per step':>10}{'per turn':>10}"
    print(head)
    print("-" * len(head))
    for c in curves:
        if not c.n:
            print(f"{c.session_id:<22}{'no usage':>8}")
            continue
        per_turn = c.equiv / c.turns if c.turns else 0
        print(f"{c.session_id:<22}{c.n:>6}{c.turns:>6}{short(c.avg_ctx):>9}{short(c.last_ctx):>10}"
              f"{short(c.equiv):>11}{fmt(c.equiv_per_step):>10}{fmt(per_turn):>10}")


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Per-step context cost curve for a DSH session.")
    ap.add_argument("sessions", nargs="*", help="session dir(s) or session.v4.jsonl.zstd file(s)")
    ap.add_argument("--last", type=int, default=0, help="use the N newest sessions under --root")
    ap.add_argument("--root", default="", help="sessions root (default: $DSH_HOME/sessions or ~/.dsh/sessions)")
    ap.add_argument("--ratio", type=float, default=DEFAULT_RATIO, help="cache read price ratio (default 0.1)")
    ap.add_argument("--every", type=int, default=0, help="print every Nth step (default: auto)")
    ap.add_argument("--csv", default="", help="write per-step rows to this CSV")
    args = ap.parse_args(argv)

    if args.sessions:
        paths = [resolve(s) for s in args.sessions]
    else:
        root = Path(args.root).expanduser() if args.root else default_root()
        count = args.last or 1
        paths = newest_sessions(root, count)
        if not paths:
            print(f"no session logs under {root}", file=sys.stderr)
            return 1

    curves = [Curve(p, args.ratio) for p in paths]

    if len(curves) > 1:
        compare(curves)
        return 0

    c = curves[0]
    print_summary(c, args.ratio)
    print_curve(c, args.every or None)
    if args.csv:
        write_csv(c, Path(args.csv).expanduser())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
