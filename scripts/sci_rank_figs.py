# -*- coding: utf-8 -*-
"""Rank experiment figures (ALL ENGLISH, matplotlib has no CJK font) 2026-08-15
Input: rank_2000.json (2000 cases: G series + shape features + hit)
Output: figs/*.png + rank_report.html
"""
import json, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = "data/sci/discovery/randomwalk/rankfigs"
os.makedirs(f"{OUT}/figs", exist_ok=True)

_raw = json.load(open("data/sci/discovery/randomwalk/rank_2000.json"))
# 2026-08-31 v21（Windows 复现报告收编）：过滤失败条目。
# 采集失败的条目只有 error 字段、没有 hit/G，直接索引会 KeyError，
# 且会把 baseline 与分桶命中率算歪。
ok = [r for r in _raw
      if isinstance(r, dict) and not r.get("error")
      and r.get("hit") is not None and r.get("G")]
if not ok:
    raise SystemExit("rank_2000.json 无有效条目（全部 error 或字段缺失）")
if len(ok) != len(_raw):
    print(f"[warn] 过滤 {len(_raw) - len(ok)} 条无效条目（error/缺字段）")
hits = [r for r in ok if r["hit"]]
nonhits = [r for r in ok if not r["hit"]]
N = len(ok)
BASE = sum(1 for r in ok if r["hit"]) / N * 100  # overall baseline hit rate

# ---------- Fig 1: multiple rank rules vs bucket hit rate ----------
fig, ax = plt.subplots(figsize=(12, 6.5))
rules = [
    ("g2015", "G(2015)", "#1f77b4"),
    ("g_mean", "G mean", "#ff7f0e"),
    ("g_peak", "G peak", "#2ca02c"),
    ("slope", "tail slope", "#d62728"),
    ("I", "2-hop strength I", "#9467bd"),
]
buckets = [(0, 100), (100, 200), (200, 500), (500, 1000), (1000, 2000)]
bnames = ["1-100", "101-200", "201-500", "501-1000", "1001-2000"]
x = np.arange(len(buckets))
width = 0.15
for i, (key, name, color) in enumerate(rules):
    ok_sorted = sorted(ok, key=lambda r: -r[key])
    rates = []
    for lo, hi in buckets:
        sub = ok_sorted[lo:hi]
        # v21：空桶除零保护（样本不足 2000 时尾部桶可能为空）
        rates.append(sum(1 for r in sub if r["hit"]) / len(sub) * 100 if sub else 0.0)
    ax.bar(x + (i - 2) * width, rates, width, label=name, color=color, alpha=0.85)
ax.axhline(BASE, color="#888", ls="--", lw=1, label=f"baseline {BASE:.1f}%")
ax.set_xticks(x)
ax.set_xticklabels(bnames)
ax.set_xlabel("Rank bucket (descending)", fontsize=12)
ax.set_ylabel("Hit rate (%)", fontsize=12)
ax.set_title("2015 snapshot: G-rank vs 2016-2025 hit rate (linked & growing, N=2000)", fontsize=13)
ax.legend(fontsize=10, ncol=3)
ax.grid(alpha=0.3, axis="y")
plt.tight_layout()
plt.savefig(f"{OUT}/figs/rules_buckets.png", dpi=140)
plt.close()

# ---------- Fig 2: fine gradient every 100 ranks ----------
fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
for ax_, key, title in [
    (axes[0], "g2015", "G(2015) — hit rate per 100 ranks"),
    (axes[1], "g_peak", "G peak — hit rate per 100 ranks"),
]:
    ok_sorted = sorted(ok, key=lambda r: -r[key])
    rates = []
    for lo in range(0, 2000, 100):
        sub = ok_sorted[lo:lo + 100]
        # v21：空桶除零保护（x 轴固定 20 桶，样本不足时尾部补 0）
        rates.append(sum(1 for r in sub if r["hit"]) / len(sub) * 100 if sub else 0.0)
    ax_.plot(range(1, 21), rates, "o-", color="#1f77b4", lw=2)
    ax_.axhline(BASE, color="#888", ls="--", lw=1)
    ax_.set_xticks(range(1, 21))
    ax_.set_xlabel("Rank (per 100)", fontsize=11)
    ax_.set_ylabel("Hit rate (%)", fontsize=11)
    ax_.set_title(title, fontsize=12)
    ax_.grid(alpha=0.3)
plt.tight_layout()
plt.savefig(f"{OUT}/figs/fine_gradient.png", dpi=140)
plt.close()

# ---------- Fig 3: feature AUC ----------
fig, ax = plt.subplots(figsize=(10, 5))
feats = [
    ("G volatility CV", 0.612),
    ("G peak value", 0.548),
    ("G peak height", 0.548),
    ("linear-growth R2", 0.533),
    ("G(2015)", 0.519),
    ("tail slope", 0.513),
    ("G mean", 0.510),
    ("2-hop strength I", 0.399),
]
feats.sort(key=lambda x: x[1])
names = [f[0] for f in feats]
aucs = [f[1] for f in feats]
colors = ["#2e7d32" if a >= 0.55 else "#c62828" if a <= 0.45 else "#888" for a in aucs]
ax.barh(names, aucs, color=colors, alpha=0.85)
ax.axvline(0.5, color="#888", ls="--", lw=1)
ax.set_xlabel("AUC (hit vs miss; 0.5 = no discrimination)", fontsize=12)
ax.set_title("Feature discrimination (2015 snapshot -> 2016-2025 link & growth)", fontsize=13)
for i, (n, a) in enumerate(zip(names, aucs)):
    ax.text(a + 0.008, i, f"{a:.3f}", va="center", fontsize=10)
plt.tight_layout()
plt.savefig(f"{OUT}/figs/feat_auc.png", dpi=140)
plt.close()

# ---------- Fig 4: CV distribution ----------
fig, ax = plt.subplots(figsize=(9, 5))
cv_hit = [r["shape"]["f_cv"] for r in hits if r.get("shape")]
cv_miss = [r["shape"]["f_cv"] for r in nonhits if r.get("shape")]
ax.hist(cv_hit, bins=25, alpha=0.6, color="#2e7d32", label=f"hit (n={len(hits)})")
ax.hist(cv_miss, bins=25, alpha=0.45, color="#c62828", label=f"miss (n={len(nonhits)})")
ax.set_xlabel("G volatility CV", fontsize=12)
ax.set_ylabel("Count", fontsize=12)
ax.set_title("G volatility CV: hit vs miss (only significant feature, p=0.0002)", fontsize=13)
ax.legend(fontsize=11)
ax.grid(alpha=0.3)
plt.tight_layout()
plt.savefig(f"{OUT}/figs/cv_dist.png", dpi=140)
plt.close()

# ---------- Fig 5: hit case G series examples ----------
fig, axes = plt.subplots(2, 3, figsize=(15, 7))
for ax_, r in zip(axes.flat, sorted(hits, key=lambda x: -x["max_n"])[:6]):
    G = r["G"]
    yrs = sorted(int(y) for y in G)
    vals = [G[str(y)] for y in yrs]
    ax_.plot(yrs, vals, "o-", color="#2e7d32", lw=2)
    ax_.set_title(f"{r['A'][:16]} x {r['B'][:16]}\nmaxN={r['max_n']} G2015={r['g2015']:.0f}", fontsize=9)
    ax_.grid(alpha=0.3)
plt.suptitle("Hit cases: G(AB,t) 2010-2015 series (linked & growing in 2016-2025)", fontsize=13)
plt.tight_layout()
plt.savefig(f"{OUT}/figs/hit_examples.png", dpi=140)
plt.close()

print("Figures regenerated (all-English):", len(os.listdir(f"{OUT}/figs")), "pngs")
