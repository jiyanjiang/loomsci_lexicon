#!/usr/bin/env python3
"""批量计算 100 个候选 AB 对的 G(AB,t)，结果归一（G_norm = G_raw/G_max(t)）。

归一（用户定稿）：
  G_raw = 1/有效电阻（max 边电导归一）
  G_max(t) = 当年所有概念对 G_raw 的最大值
  G_norm(A,B,t) = G_raw(A,B,t) / G_max(t)   →  当年最强对=1，跨年可比

输出：
  data/distance_plots/*.png                     逐年 G_norm 曲线（Y 轴 0-1，含阈值线）
  data/distance_batch_summary_20260812.csv      归一汇总
  data/distance_year_max_20260812.json          {year: G_max(t)} 供 /distance 单对查询归一
"""
import argparse
import csv
import json
import os
import sys
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from g_ab_calc import compute_g_series  # noqa: E402

PLOT_DIR = os.path.join(ROOT, "data", "distance_plots")
SUMMARY = os.path.join(ROOT, "data", "distance_batch_summary_20260812.csv")
YEAR_MAX_JSON = os.path.join(ROOT, "data", "distance_year_max_20260812.json")
YEARS = list(range(1991, 2026))


def safe_name(s):
    return "".join(c if c.isalnum() else "_" for c in s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(ROOT, "data", "distance_candidate_pairs.json"))
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 对（调试）")
    args = ap.parse_args()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with open(args.json, encoding="utf-8") as f:
        pairs = json.load(f)["pairs"]
    if args.limit:
        pairs = pairs[: args.limit]
    os.makedirs(PLOT_DIR, exist_ok=True)

    # ---- 第一遍：算全部 G_raw ----
    raw_by_pair = []          # [{meta..., G_raw: [逐年]}, ...]
    for i, p in enumerate(pairs):
        A, B, dom, burst = p["A"], p["B"], p["domain"], p.get("burst_year")
        print(f"[{i+1}/{len(pairs)}] {dom}: {A} × {B}", flush=True)
        _, g = compute_g_series(A, B, YEARS)
        raw_by_pair.append({"domain": dom, "A": A, "B": B,
                            "burst_year": burst or "", "G_raw": g})

    # ---- 结果归一：每年 G_max ----
    year_max = {}
    for yi, y in enumerate(YEARS):
        vals = [rp["G_raw"][yi] for rp in raw_by_pair
                if rp["G_raw"][yi] is not None and rp["G_raw"][yi] > 0]
        year_max[y] = round(max(vals), 4) if vals else None
    with open(YEAR_MAX_JSON, "w", encoding="utf-8") as f:
        json.dump({"years": YEARS, "G_max": year_max}, f, ensure_ascii=False, indent=1)
    print(f"\n年度 G_max(t) 参考 → {YEAR_MAX_JSON}")
    print("  " + " ".join(f"{y}:{year_max[y]}" for y in YEARS if year_max[y]))

    # ---- 归一化 + PNG + 汇总 ----
    rows = []
    for i, rp in enumerate(raw_by_pair):
        A, B, dom, burst = rp["A"], rp["B"], rp["domain"], rp["burst_year"]
        g_norm = []
        for yi, v in enumerate(rp["G_raw"]):
            if v is None or v <= 0 or not year_max[YEARS[yi]]:
                g_norm.append(None)
            else:
                g_norm.append(round(v / year_max[YEARS[yi]], 4))
        # 爆发前形态指标
        if burst and burst - 1 >= 1991:
            pre = [v for y, v in zip(YEARS, g_norm) if v is not None and burst - 5 <= y < burst]
            early = [v for y, v in zip(YEARS, g_norm) if v is not None and y < burst - 5]
            pre_avg = np.mean(pre) if pre else None
            early_avg = np.mean(early) if early else None
            pre_rise = (pre_avg - early_avg) / early_avg if early_avg else None
        else:
            pre_avg = early_avg = pre_rise = None
        recent = [v for v in g_norm if v is not None][-3:]
        recent_rise = (recent[-1] - recent[0]) / recent[0] if len(recent) == 3 and recent[0] else None

        # PNG（Y 轴 0-1）
        fig, ax = plt.subplots(figsize=(9, 4))
        xs = [y for y, v in zip(YEARS, g_norm) if v is not None]
        ys = [v for v in g_norm if v is not None]
        ax.plot(xs, ys, "-o", ms=3, color="#1565c0")
        if burst:
            ax.axvline(burst, color="#e65100", ls="--", lw=1.2, label=f"burst {burst}")
        ax.axhline(1.0, color="#888", ls=":", lw=1, label="G=1 (当年最强)")
        ax.set_ylim(0, 1.05)
        ax.set_title(f"G_norm({A} x {B}) | {dom} | burst={burst}", fontsize=11)
        ax.set_xlabel("year"); ax.set_ylabel("G_norm (0-1)")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fn = os.path.join(PLOT_DIR, f"{dom}_{i+1:03d}_{safe_name(A)}_{safe_name(B)}.png")
        fig.savefig(fn, dpi=110)
        plt.close(fig)

        rows.append({
            "domain": dom, "A": A, "B": B, "burst_year": burst,
            "pre_avg": "" if pre_avg is None else round(pre_avg, 4),
            "early_avg": "" if early_avg is None else round(early_avg, 4),
            "pre_rise": "" if pre_rise is None else round(pre_rise, 2),
            "recent_rise": "" if recent_rise is None else round(recent_rise, 2),
            "G_last": "" if g_norm[-1] is None else round(g_norm[-1], 4),
            "png": os.path.relpath(fn, ROOT),
        })

    with open(SUMMARY, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n完成 {len(rows)} 对 → PNG 在 {PLOT_DIR}，归一汇总 {SUMMARY}")


if __name__ == "__main__":
    main()
