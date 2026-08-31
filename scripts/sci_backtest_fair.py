# -*- coding: utf-8 -*-
"""对等回测：实验组 vs 对照组（同过滤、同命中定义）2026-08-15

用户质疑：5.8% vs 3% 的对比不对等——实验组(两跳采样+过滤+topN) vs
对照组(随机配对未过滤)。修复：两臂完全相同的前处理。

对照组构建：从 2015 网络随机抽"未直连且有间接路径"的对（与两跳采样
同等的存在性条件），同样过 BRIDGE_STOP/变体过滤，同样算"直连且增长"。
"""
import json, os, sys, time, random
from collections import defaultdict, Counter
import numpy as np
import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sci_g_series_fast import g_raw_series_fast
from sci_rw_sampler import BRIDGE_STOP, build_network, check_hit, load_seeds

ANN = "data/annotation/normalized"
OUT = "data/sci/discovery/randomwalk"
os.makedirs(OUT, exist_ok=True)
N_WORKERS = 8
random.seed(42)


def build_random_control(neigh, inv, freq, n_target=600):
    """随机对照组：与两跳采样同等的存在性条件，但配对方式是随机。

    条件（与实验组对齐）：
    - 两词都是 2015 热短语（freq>=20，同 build_network 的 hot 条件）
    - 2010-14 未直连
    - 两词不在同一篇 2015 文章（2015 也未直连，即 inter=0）
    - 过 BRIDGE_STOP / 变体过滤
    配对：从热短语池随机两两配对（不要求有共同邻居路径）。
    """
    t0 = time.time()
    # 2010-14 未直连检查
    con = duckdb.connect(":memory:")
    rows14 = con.execute(f"""
        SELECT arxiv_id, unnest(phrases) AS ph FROM read_parquet('{ANN}/year=*/part-0.parquet')
        WHERE year BETWEEN 2010 AND 2014
    """).fetchall()
    con.close()
    inv14 = defaultdict(set)
    for aid, ph in rows14:
        inv14[ph].add(aid)

    hot = [ph for ph, n in freq.items() if n >= 20]
    print(f"随机对照组: 热短语池 {len(hot)} 个", flush=True)

    cands = []
    attempts = 0
    while len(cands) < n_target and attempts < n_target * 200:
        attempts += 1
        A, B = random.sample(hot, 2)
        if A == B:
            continue
        # 2010-14 未直连
        if inv14.get(A) and inv14.get(B) and (inv14[A] & inv14[B]):
            continue
        # 2015 未直连（间接路径存在性：至少 A 有邻居且 B 有邻居）
        if inv.get(A) and inv.get(B) and (inv[A] & inv[B]):
            continue
        # 桥词/变体过滤（与实验组 same）
        if A in BRIDGE_STOP or B in BRIDGE_STOP:
            continue
        ta, tb = set(A.lower().split()), set(B.lower().split())
        if ta & tb and len(ta & tb) / max(len(ta), len(tb), 1) > 0.4:
            continue
        cands.append({"A": A, "B": B, "seed": None, "via": None, "I": None,
                      "freqA": freq.get(A, 0), "freqB": freq.get(B, 0)})
    print(f"随机对照组: {len(cands)} 对 ({time.time()-t0:.0f}s)", flush=True)
    return cands


def worker(args):
    A, B, tag = args
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
        hit = check_hit(N)
        return {"A": A, "B": B, "tag": tag, "cv": cv, "hit": hit,
                "n_years": sum(1 for y in range(2016, 2026) if N.get(y, 0) > 0),
                "max_n": max(N.values()) if N else 0}
    except Exception as e:
        return {"A": A, "B": B, "tag": tag, "error": str(e)[:60]}


def main():
    t0 = time.time()
    # 实验组：复用 rw_candidates_2015.json（两跳采样结果，已有 hit 字段）
    exp = json.load(open(f"{OUT}/rw_candidates_2015.json"))
    exp_ok = [r for r in exp if "error" not in r]
    exp_hit = sum(1 for r in exp_ok if r["hit"])
    exp_rate = exp_hit / max(len(exp_ok), 1) * 100

    # 对照组：随机配对 + 同等过滤
    inv15, freq15, neigh15 = build_network(2015, 2015)
    ctrl = build_random_control(neigh15, inv15, freq15, n_target=len(exp_ok))
    print(f"对照组: {len(ctrl)} 对（与实验组 {len(exp_ok)} 对等量）", flush=True)

    from concurrent.futures import ProcessPoolExecutor, as_completed
    jobs = [(c["A"], c["B"], "ctrl") for c in ctrl]
    results = []
    with ProcessPoolExecutor(max_workers=N_WORKERS) as ex:
        futs = [ex.submit(worker, j) for j in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            results.append(f.result())
            if i % 100 == 0:
                print(f"  {i}/{len(jobs)} ({time.time()-t0:.0f}s)", flush=True)
    ok = [r for r in results if "error" not in r]
    hit = sum(1 for r in ok if r["hit"])
    ctrl_rate = hit / max(len(ok), 1) * 100

    print(f"\n=== 对等对比（2015 → 2016-2025，命中=直连且增长）===")
    print(f"实验组(两跳采样+I排序): n={len(exp_ok)} 命中 {exp_hit} = {exp_rate:.1f}%")
    print(f"对照组(随机配对+同过滤): n={len(ok)} 命中 {hit} = {ctrl_rate:.1f}%")
    print(f"增益: {exp_rate - ctrl_rate:.1f} 个百分点（{exp_rate/max(ctrl_rate,1e-9):.1f} 倍）")
    print(f"\n→ {OUT}/backtest_fair_2015.json")

    json.dump({"exp": exp_ok, "ctrl": ok,
               "exp_rate": exp_rate, "ctrl_rate": ctrl_rate,
               "gain_pp": exp_rate - ctrl_rate,
               "n_exp": len(exp_ok), "n_ctrl": len(ok),
               "note": "对等对比：两臂同过滤(BRIDGE_STOP/变体)、同命中定义(直连且增长)"},
              open(f"{OUT}/backtest_fair_2015.json", "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
