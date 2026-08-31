# -*- coding: utf-8 -*-
"""算法 v2：两跳共同邻居路径采样未直连候选对（用户 2026-08-14 设计 v2）

核心：从核心起点 seed 出发，走两步（seed → 中间词 → 候选 v），
要求 v 与 seed 未直连，且中间词是"真桥"（排除套话/停用词）。
候选按间接路径强度 I = min(边权) 排序，取 top 600 算 G(AB)。

验证：2015 时刻采样 → 检查 2016-2025 是否直连且增长（目标 5%）。
命中标准：2016-2025 任一年 N>=5 且后段(2021-25)峰值 > 前段(2016-20)峰值。
"""
import json, os, sys, time, random
from collections import defaultdict, Counter
import numpy as np
import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sci_g_series_fast import g_raw_series_fast

ANN = "data/annotation/normalized"
OUT = "data/sci/discovery/randomwalk"
os.makedirs(OUT, exist_ok=True)
N_WORKERS = 8

# 套话/停用中间词（桥接词过滤，防垃圾对 + 防 G 虚高）
BRIDGE_STOP = {'non zero', 'category', 'many application', 'various', 'multiple', 'different',
               'certain', 'single', 'specific', 'important', 'recent', 'general', 'particular',
               'related', 'due to', 'result(s)', 'shown', 'presented', 'obtained', 'studied',
               'considered', 'including', 'such as', 'order to', 'terms of', 'well known',
               'well studied', 'state of the art', 'real world', 'real time', 'high quality',
               'large scale', 'practical application', 'important role', 'significant',
               'experimental obs', 'numerical result', 'theoretical prediction', 'new method',
               'novel method', 'proposed method', 'deep learning based', 'machine learning based'}

def load_seeds():
    """从 balanced seed 文件加载（用户 2026-08-15：按 arXiv 大类系统覆盖 200 节点）"""
    seeds = json.load(open("data/sci/discovery/randomwalk/seeds_balanced.json"))
    return [s["phrase"] for s in seeds]


def build_network(y0, y1):
    """概念网络：短语 → {邻居: 共现数}（窗口 y0-y1）"""
    con = duckdb.connect(":memory:")
    rows = con.execute(f"""
        SELECT arxiv_id, unnest(phrases) AS ph FROM read_parquet('{ANN}/year=*/part-0.parquet')
        WHERE year BETWEEN {y0} AND {y1}
    """).fetchall()
    con.close()
    inv = defaultdict(set)
    for aid, ph in rows:
        inv[ph].add(aid)
    freq = {ph: len(docs) for ph, docs in inv.items()}
    hot = {ph for ph, n in freq.items() if n >= 20}
    doc_ph = defaultdict(set)
    for aid, ph in rows:
        if ph in hot:
            doc_ph[aid].add(ph)
    neigh = defaultdict(Counter)
    for aid, phs in doc_ph.items():
        phs = list(phs)
        for i in range(len(phs)):
            for j in range(i + 1, len(phs)):
                a, b = phs[i], phs[j]
                neigh[a][b] += 1
                neigh[b][a] += 1
    return inv, freq, neigh


def two_hop_candidates(seed, neigh, inv, max_n=12):
    """seed → 中间词 w（真桥）→ 候选 v（与 seed 未直连）。
    返回 [(seed, v, w, cw, cv, I)]，I = min(cw, cv) 两跳强度。
    """
    nb = neigh.get(seed, {})
    if not nb:
        return []
    cands = []
    for w, cw in nb.items():
        if w in BRIDGE_STOP:
            continue
        wnb = neigh.get(w, {})
        for v, cv in wnb.items():
            if v == seed or v in nb:
                continue
            if v in BRIDGE_STOP:
                continue
            if inv.get(seed) and inv.get(v) and (inv[seed] & inv[v]):
                continue  # 已直连
            I = min(cw, cv)
            cands.append((seed, v, w, cw, cv, I))
    # 按两跳强度降序，取 top
    cands.sort(key=lambda x: -x[5])
    return cands[:max_n]


def build_candidates():
    """所有种子两跳采样 → 去重 → 按 I 排序"""
    t0 = time.time()
    inv15, freq15, neigh15 = build_network(2015, 2015)
    print(f"2015 网络: 短语 {len(freq15)} 带邻居 {len(neigh15)} ({time.time()-t0:.0f}s)", flush=True)

    con = duckdb.connect(":memory:")
    rows14 = con.execute(f"""
        SELECT arxiv_id, unnest(phrases) AS ph FROM read_parquet('{ANN}/year=*/part-0.parquet')
        WHERE year BETWEEN 2010 AND 2014
    """).fetchall()
    con.close()
    inv14 = defaultdict(set)
    for aid, ph in rows14:
        inv14[ph].add(aid)

    SEEDS = load_seeds()
    all_c = []
    for seed in SEEDS:
        if seed not in neigh15:
            continue
        got = two_hop_candidates(seed, neigh15, inv15)
        for seed2, v, w, cw, cv, I in got:
            # 双重检查 2010-14 未直连
            if inv14.get(seed2) and inv14.get(v) and (inv14[seed2] & inv14[v]):
                continue
            ta, tb = set(seed2.lower().split()), set(v.lower().split())
            if ta & tb and len(ta & tb) / max(len(ta), len(tb), 1) > 0.4:
                continue
            all_c.append({"A": seed2, "B": v, "seed": seed2, "via": w,
                          "I": I, "freqA": freq15.get(seed2, 0), "freqB": freq15.get(v, 0)})
    seen = set(); uniq = []
    for c in all_c:
        k = (min(c["A"], c["B"]), max(c["A"], c["B"]))
        if k not in seen:
            seen.add(k); uniq.append(c)
    uniq.sort(key=lambda x: -x["I"])
    print(f"采样: {len(uniq)} 唯一候选 (两跳共同邻居) ({time.time()-t0:.0f}s)", flush=True)
    return uniq


def check_hit(N):
    """命中：直连且增长（后段峰值>前段峰值 且 >=5）"""
    if not N:
        return False
    p1 = max([N.get(y, 0) for y in range(2016, 2021)] or [0])
    p2 = max([N.get(y, 0) for y in range(2021, 2026)] or [0])
    return p2 >= 5 and p2 > p1


def worker(args):
    A, B, I, seed = args
    try:
        G = g_raw_series_fast(A, B, 2010, 2015)
        con = duckdb.connect(":memory:")
        rows = con.execute(f"""
            WITH docs AS (
                SELECT a.arxiv_id, EXTRACT(YEAR FROM p.submission_date) AS yr
                FROM read_parquet('{ANN}/year=*/part-0.parquet') a
                JOIN read_parquet('data/parquet/papers/year=*/*.parquet') p ON a.arxiv_id = p.arxiv_id
                WHERE list_contains(a.phrases, ?) AND list_contains(a.phrases, ?)
                  AND EXTRACT(YEAR FROM p.submission_date) BETWEEN 2016 AND 2025
            )
            SELECT yr, count(*) FROM docs GROUP BY yr
        """, [A, B]).fetchall()
        con.close()
        N = {int(y): int(n) for y, n in rows}
        vals = [G[y] for y in range(2010, 2016) if G.get(y) is not None and G[y] > 0]
        cv = float(np.std(vals) / max(np.mean(vals), 1e-9)) if len(vals) >= 3 else 0.0
        peak = float(max(vals)) if vals else 0.0
        hit = check_hit(N)
        n_years = sum(1 for y in range(2016, 2026) if N.get(y, 0) > 0)
        max_n = max(N.values()) if N else 0
        return {"A": A, "B": B, "seed": seed, "I": I,
                "cv": cv, "g_peak": peak,
                "hit": hit, "n_years": n_years, "max_n": max_n, "N": N}
    except Exception as e:
        return {"A": A, "B": B, "error": str(e)[:60]}


def main():
    t0 = time.time()
    cands = build_candidates()
    top = cands[:600]
    print(f"算 G: {len(top)} 对 (I 排序 top 600)", flush=True)

    from concurrent.futures import ProcessPoolExecutor, as_completed
    jobs = [(c["A"], c["B"], c["I"], c["seed"]) for c in top]
    results = []
    with ProcessPoolExecutor(max_workers=N_WORKERS) as ex:
        futs = [ex.submit(worker, j) for j in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            results.append(f.result())
            if i % 100 == 0:
                print(f"  {i}/{len(jobs)} ({time.time()-t0:.0f}s)", flush=True)
    ok = [r for r in results if "error" not in r]
    json.dump(results, open(f"{OUT}/rw_candidates_2015.json", "w"), ensure_ascii=False, indent=1)

    hits = [r for r in ok if r["hit"]]
    print(f"\n=== 随机行走采样回测（2015 → 2016-2025，命中=直连且增长）===")
    print(f"候选 {len(ok)}，命中 {len(hits)} = {len(hits)/max(len(ok),1)*100:.1f}%")

    # 按 G 波动性分层
    print(f"\n=== 按 G 波动性(CV)分层 ===")
    for lo, hi, tag in [(0, 0.3, "CV<0.3"), (0.3, 0.6, "0.3-0.6"), (0.6, 1.0, "0.6-1.0"), (1.0, 99, "CV>1.0")]:
        sub = [r for r in ok if lo <= r.get("cv", 0) < hi]
        if len(sub) < 10:
            continue
        h = sum(1 for r in sub if r["hit"])
        print(f"  {tag:8s} n={len(sub):3d} 命中 {h} = {h/len(sub)*100:.0f}%")

    # 按两跳强度 I 分层
    print(f"\n=== 按两跳强度 I 分层 ===")
    for lo, hi, tag in [(0, 5, "I<5"), (5, 15, "I 5-15"), (15, 40, "I 15-40"), (40, 1e9, "I>40")]:
        sub = [r for r in ok if lo <= r.get("I", 0) < hi]
        if len(sub) < 10:
            continue
        h = sum(1 for r in sub if r["hit"])
        print(f"  {tag:8s} n={len(sub):3d} 命中 {h} = {h/len(sub)*100:.0f}%")

    # 命中案例
    print(f"\n=== 命中案例抽样（Top 15）===")
    for r in sorted(hits, key=lambda x: -x["max_n"])[:15]:
        print(f"  {r['A'][:20]:20}x{r['B'][:20]:20} I={r['I']} cv={r['cv']:.2f} maxN={r['max_n']}")

    print(f"\n→ {OUT}/rw_candidates_2015.json ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
