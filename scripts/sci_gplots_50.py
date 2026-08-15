# -*- coding: utf-8 -*-
"""50 个候选 G(AB,t) 图 + 形态分类 + 看好档数值规律（用户 2026-08-15）"""
import json, os, re
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from collections import Counter

OUT = "data/sci/discovery/predict2026"
FIGD = f"{OUT}/gfigs"
os.makedirs(FIGD, exist_ok=True)

sel = json.load(open(f"{OUT}/selected50.json"))
# 读 verdicts
verdicts = json.load(open(f"{OUT}/verdicts.json"))
verdicts = {int(k): v for k, v in verdicts.items()}

YEARS = list(range(2015, 2026))


def classify_shape(series):
    """形态分类（英文返回值，供图内显示；HTML 层再映射中文）"""
    vals = [series.get(str(y)) for y in YEARS]
    vals = [v for v in vals if v is not None]
    if len(vals) < 5:
        return "insufficient"
    # 归一化
    mx = max(vals)
    if mx <= 0:
        return "all-zero"
    norm = [v / mx for v in vals]
    first, last = norm[0], norm[-1]
    # 峰检测（局部最大，>0.85mx 且两侧下降）
    peaks = []
    for i in range(1, len(norm) - 1):
        if norm[i] >= norm[i - 1] and norm[i] > norm[i + 1] and norm[i] >= 0.85:
            peaks.append(i)
    # 尾部斜率（后 3 年）
    tail = norm[-3:]
    tail_slope = tail[-1] - tail[0]
    if peaks and peaks[-1] <= len(norm) - 2:
        # 峰后下降明显？
        after_peak = norm[peaks[-1]:]
        drop = after_peak[0] - after_peak[-1]
        if drop > 0.2:
            return "rise-then-fall"
    if last > first * 1.5 and tail_slope > 0.05:
        return "mono-rise"
    if last > first * 1.2 and abs(tail_slope) <= 0.05:
        return "rise-plateau"
    if abs(last - first) <= 0.15 and max(norm) - min(norm) <= 0.3:
        return "flat"
    return "volatile"


# 形态英文 → 中文（HTML 展示用）
SHAPE_CN = {"insufficient": "数据不足", "all-zero": "全零",
            "rise-then-fall": "先升后降(峰)", "mono-rise": "单调上升",
            "rise-plateau": "上升后平台", "flat": "平/低波动", "volatile": "波动"}


# 主统计
shape_counts = Counter()
fav_stats = {"看好": [], "中立": [], "不看好": []}

cards = ""
for i, c in enumerate(sel, 1):
    v = verdicts.get(i, {"verdict": "?", "reason": ""})
    verdict = v.get("verdict", "?")
    reason = v.get("reason", "")
    series = c["series"]
    vals = [series.get(str(y)) for y in YEARS]
    shape = classify_shape(series)
    shape_counts[shape] += 1

    # 数值特征
    vv = [x for x in vals if x is not None]
    g_last = vals[-1] if vals[-1] is not None else 0
    g_first = next((x for x in vals if x is not None), 0)
    g_mean = float(np.mean(vv)) if vv else 0
    g_cv = float(np.std(vv) / max(g_mean, 1e-9)) if g_mean > 0 else 0
    rise = (g_last - g_first) / max(g_first, 1e-9)
    # G 峰值位置（相对 2025）
    peak_y = None
    if vv:
        pi = int(np.argmax(vv))
        peak_y = 2015 + pi
    fav_stats[verdict].append({
        "g2025": g_last, "g_cv": g_cv, "rise": rise, "peak_y": peak_y,
        "I": c["I"], "cv": c["cv"]})

    # 画图
    fig, ax = plt.subplots(1, 1, figsize=(9, 4.5))
    yrs = [y for y in YEARS if series.get(str(y)) is not None]
    gvs = [series.get(str(y)) for y in yrs]
    ax.plot(yrs, gvs, "o-", color="#2ca02c", lw=1.8, ms=4)
    ax.set_title(f"#{i} {c['A']} × {c['B']}  [{c['field']}]  G2025={g_last:.0f}  {shape}", fontsize=9)
    ax.set_xlabel("Year"); ax.set_ylabel("G(AB,t)")
    ax.grid(alpha=0.3)
    plt.tight_layout()
    fn = f"{FIGD}/g{i:02d}.png"
    plt.savefig(fn, dpi=110, bbox_inches="tight")
    plt.close()

    color = {"看好": "#2e7d32", "中立": "#f9a825", "不看好": "#c62828"}.get(verdict, "#666")
    shape_cn = SHAPE_CN.get(shape, shape)
    cards += f"""<div class="card" style="border-left:5px solid {color}">
<img src="gfigs/g{i:02d}.png">
<div class="head"><b>#{i}</b> {c['A']} × {c['B']} <span class="verdict" style="background:{color}">{verdict}</span> <span class="shape">{shape_cn}</span></div>
<div class="meta">via={c['via']} · I={c['I']} · cv={c['cv']:.2f} · G2025={g_last:.0f} · peak-year={peak_y}</div>
<div class="reason">理由: {reason}</div></div>"""

# 看好档数值规律
print("=== 形态分类 ===")
for s, n in shape_counts.most_common():
    print(f"  {s}: {n}")

print("\n=== 三挡数值特征（mean）===")
for vd in ["看好", "中立", "不看好"]:
    st = fav_stats[vd]
    if not st:
        continue
    print(f"  {vd} (n={len(st)}):")
    for key, label in [("g2025", "G2025"), ("g_cv", "G序列CV"), ("rise", "增幅"), ("I", "两跳I"), ("cv", "G波动CV")]:
        vals = [s[key] for s in st if s[key] is not None]
        if vals:
            print(f"    {label:8s}: mean={np.mean(vals):.2f} med={np.median(vals):.2f}")
    peaks = [s["peak_y"] for s in st if s.get("peak_y")]
    if peaks:
        print(f"    G峰年中位: {int(np.median(peaks))}")

json.dump({"shape_counts": dict(shape_counts),
           "fav_stats": {k: v for k, v in fav_stats.items()}},
          open(f"{OUT}/gfigs_stats.json", "w"), ensure_ascii=False, indent=1)

html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>2026 预判 · 50 个 G(AB,t) 图 + 形态分类</title>
<style>
body {{ font-family:-apple-system,"PingFang SC",sans-serif; max-width:1300px; margin:0 auto; padding:20px; background:#fafafa; }}
h1 {{ border-bottom:3px solid #1f77b4; }}
.panel {{ background:#fff; border:1px solid #e0e0e0; border-radius:10px; padding:16px; margin:16px 0; }}
.analysis {{ background:#fff8e1; border-left:4px solid #f9a825; padding:12px 16px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(460px,1fr)); gap:12px; }}
.card {{ background:#fff; border:1px solid #ddd; border-radius:8px; padding:8px; }}
.card img {{ width:100%; height:auto; border-radius:4px; }}
.head {{ font-size:12px; margin:4px 0; }}
.verdict {{ color:#fff; padding:1px 6px; border-radius:10px; font-size:10px; }}
.shape {{ color:#1f77b4; font-size:10px; margin-left:6px; }}
.meta {{ font-size:10px; color:#888; }}
.reason {{ font-size:11px; color:#333; margin-top:4px; }}
</style></head><body>
<h1>2026 预判 · 50 个候选 G(AB,t) 图（t∈2015-2025）</h1>
<div class="panel"><div class="analysis">
<b>形态分类</b>：{json.dumps(shape_counts, ensure_ascii=False)}<br>
<b>三挡</b>：看好 20 · 中立 24 · 不看好 4（绿/黄/红卡片）
</div></div>
<div class="grid">{cards}</div>
</body></html>"""
with open(f"{OUT}/gfigs_50.html", "w") as f:
    f.write(html)
print(f"\n→ {OUT}/gfigs_50.html (50 图)")
