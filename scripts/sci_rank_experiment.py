# -*- coding: utf-8 -*-
"""G 排序法则实验（2015 → 2016-2025，2000 案例）2026-08-15

用户要求：
1. 用 G(2015) 当年值（而非历史峰值）降序排名
2. 扩到 2000 案例
3. 多种排序法则 + 形状定量化（振荡周期/单调增长拟合/峰型）

排序法则（每个都对命中率做降序分桶单调性检验）：
  R1: G2015 当年值
  R2: G 均值(2010-2015)
  R3: G 峰值
  R4: G 尾部斜率（后2年均值-前2年均值）/跨度
  R5: 波动性 CV
  R6: 两跳强度 I
形状特征（供后续拟合分析）：
  S1: 振荡频率（FFT 主频）
  S2: 单调增长拟合度（与线性增长的 R²）
  S3: 峰位置（argmax 相对位置）
  S4: 归一化曲线（存原始序列）

分批：--batch 0/1（每批 1000 对，断点续跑，输出 rank_batch{N}.json）
"""
import json, os, sys, time, math
from collections import defaultdict
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sci_g_series_fast import g_n_raw_series_fast

ANN = "data/annotation/normalized"
OUT = "data/sci/discovery/randomwalk"
N_WORKERS = 8
YEARS = list(range(2010, 2016))   # G 窗口
PRED_YEARS = list(range(2016, 2026))  # 命中窗口


def check_hit(N):
    """命中：直连且增长（后段峰值>=5 且后段>前段）——与 rw_sampler 一致"""
    if not N:
        return False
    p1 = max([N.get(y, 0) for y in range(2016, 2021)] or [0])
    p2 = max([N.get(y, 0) for y in range(2021, 2026)] or [0])
    return p2 >= 5 and p2 > p1


def shape_features(vals):
    """形状定量化。vals = 2010-2015 G 序列（可能缺年，补 0）"""
    full = [vals.get(y, 0.0) for y in YEARS]
    mx = max(full)
    if mx <= 0:
        return {"f_peak": 0.0, "f_pos": 0.0, "f_lin_r2": 0.0, "f_fft_freq": 0.0, "f_cv": 0.0}
    norm = [v / mx for v in full]
    # 峰位置（相对位置 0-1）
    peak_pos = np.argmax(norm) / (len(norm) - 1)
    # 单调增长拟合度：与线性增长 y=x 的相关（Spearman 用 Pearson on ranks 简化）
    x = np.arange(len(norm), dtype=float)
    lin_r2 = np.corrcoef(x, norm)[0, 1] ** 2 if np.std(norm) > 0 else 0.0
    # 振荡频率：FFT 主频（>0 表示有振荡）
    det = norm - np.mean(norm)
    fft = np.abs(np.fft.rfft(det))
    if len(fft) > 2:
        freqs = np.fft.rfftfreq(len(det), d=1.0)
        idx = np.argmax(fft[1:]) + 1
        fft_freq = freqs[idx] if len(freqs) > idx else 0.0
    else:
        fft_freq = 0.0
    cv = np.std(full) / max(np.mean(full), 1e-9)
    return {"f_peak": mx, "f_pos": float(peak_pos), "f_lin_r2": float(lin_r2),
            "f_fft_freq": float(fft_freq), "f_cv": float(cv)}


def worker(args):
    A, B, I, score = args
    try:
        G, N = g_n_raw_series_fast(A, B, 2010, 2025)
        hit = check_hit(N)
        g2015 = float(G.get(2015, 0.0))
        vals = {y: float(G.get(y, 0.0)) for y in YEARS}
        sh = shape_features(vals)
        # 尾部斜率
        v = [vals[y] for y in YEARS]
        head = np.mean(v[:2]); tail = np.mean(v[-2:])
        slope = (tail - head) / max(np.mean(v), 1e-9)
        return {"A": A, "B": B, "I": float(I), "score": float(score), "hit": hit,
                "g2015": g2015, "g_mean": float(np.mean(list(vals.values()))),
                "g_peak": float(max(vals.values())), "slope": float(slope),
                "n_years": sum(1 for y in PRED_YEARS if N.get(y, 0) > 0),
                "max_n": max(N.values()) if N else 0,
                "shape": sh, "N": N, "G": vals}
    except Exception as e:
        return {"A": A, "B": B, "error": str(e)[:60]}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=0)
    ap.add_argument("--cands", default=f"{OUT}/cands_noai_5000.json")
    ap.add_argument("--out-prefix", default="rank_batch", help="输出文件名前缀")
    args = ap.parse_args()
    batch = args.batch
    cands = json.load(open(args.cands))
    # 分批：batch 0-4，每批 1000
    lo = batch * 1000
    hi = min(lo + 1000, len(cands))
    batch_cands = cands[lo:hi]
    print(f"批次 {batch}: 候选 {len(batch_cands)}（{lo}-{hi}）from {args.cands}", flush=True)

    from concurrent.futures import ProcessPoolExecutor, as_completed
    t0 = time.time()
    jobs = [(c["A"], c["B"], c.get("I", c.get("score", 0.0)), c.get("score", 0.0)) for c in batch_cands]
    results = []
    with ProcessPoolExecutor(max_workers=N_WORKERS) as ex:
        futs = [ex.submit(worker, j) for j in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            results.append(f.result())
            if i % 100 == 0:
                print(f"  {i}/{len(jobs)} ({time.time()-t0:.0f}s)", flush=True)
    out = f"{OUT}/{args.out_prefix}{batch}.json"
    json.dump(results, open(out, "w"), ensure_ascii=False)
    ok = [r for r in results if "error" not in r]
    hits = sum(1 for r in ok if r["hit"])
    print(f"批次 {batch}: {len(ok)} 有效, 命中 {hits} = {hits/max(len(ok),1)*100:.1f}% → {out}", flush=True)


if __name__ == "__main__":
    main()
