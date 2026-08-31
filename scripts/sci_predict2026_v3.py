# -*- coding: utf-8 -*-
"""2026 预判 v3：内存构建 + seed 聚焦邻居（快，<5 分钟）

关键优化：不建全网络邻接表，只对 seed 聚焦——
1. 内存构建 短语→文档(inv) 和 文档→短语(doc_ph, 仅hot)
2. 对每个 seed：从 doc_ph 找含 seed 的文档 → 统计邻居短语（共现数）
3. 对每个邻居 w：再找含 w 的文档 → 两跳邻居 v（与 seed 未直连）
4. G 波动性 CV 筛选 0.3-1.0 → Top 100
"""
import json, os, sys, time
from collections import defaultdict, Counter
import numpy as np
import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sci_g_series_fast import g_raw_series_fast

ANN = "data/annotation/normalized"
OUT = "data/sci/discovery/predict2026"
os.makedirs(OUT, exist_ok=True)
N_WORKERS = 8
MAX_N1 = 25    # 每个 seed 取 top 邻居数
MAX_N2 = 12    # 每个 w 取两跳数

BRIDGE_STOP = {'non zero', 'category', 'many application', 'various', 'multiple', 'different',
               'certain', 'single', 'specific', 'important', 'recent', 'general', 'particular',
               'related', 'due to', 'result(s)', 'shown', 'presented', 'obtained', 'studied',
               'considered', 'including', 'such as', 'order to', 'terms of', 'well known',
               'well studied', 'state of the art', 'real world', 'real time', 'high quality',
               'large scale', 'practical application', 'important role', 'significant',
               'experimental obs', 'numerical result', 'theoretical prediction', 'new method',
               'novel method', 'proposed method', 'deep learning based', 'machine learning based',
               'scheme', 'flux', 'invariant', 'positive integer', 'upper bound', 'lower bound',
               'sufficient condition', 'numerical simulation', 'experimental data',
               'time dependent', 'functional', 'fitness', 'steady state'}


def load_data():
    """内存构建 inv(短语→文档), doc_ph(文档→hot短语), freq"""
    con = duckdb.connect(":memory:")
    data = con.execute(f"""
        SELECT arxiv_id, unnest(phrases) AS ph FROM read_parquet('{ANN}/year=2025/part-0.parquet')
    """).fetchall()
    con.close()
    inv = defaultdict(set)
    for aid, ph in data:
        inv[ph].add(aid)
    freq = {ph: len(d) for ph, d in inv.items()}
    hot = {ph for ph, n in freq.items() if n >= 20}
    doc_ph = defaultdict(set)
    for aid, ph in data:
        if ph in hot:
            doc_ph[aid].add(ph)
    return inv, doc_ph, freq


def main():
    t0 = time.time()
    inv, doc_ph, freq = load_data()
    print(f"数据加载: {time.time()-t0:.0f}s (inv {len(inv)}, 文档 {len(doc_ph)})", flush=True)

    seeds = json.load(open("data/sci/discovery/randomwalk/seeds_balanced.json"))
    seed_set = set(x["phrase"] for x in seeds)

    # 预计算：每个短语 → 含它的文档列表（用 inv 直接拿）
    # 一跳：seed 的邻居
    all_c = []
    for x in seeds:
        ph = x["phrase"]
        docs = inv.get(ph)
        if not docs:
            continue
        # seed 的邻居计数：含 seed 的文档里的其他 hot 短语
        neigh_c = Counter()
        for aid in docs:
            for w in doc_ph.get(aid, ()):
                if w != ph and w not in BRIDGE_STOP:
                    neigh_c[w] += 1
        got_seed = 0
        for w, cw in neigh_c.most_common(MAX_N1 * 2):
            if cw < 3:   # 邻居权重下限：弱桥过滤
                continue
            # 两跳：w 的邻居
            wdocs = inv.get(w)
            if not wdocs:
                continue
            neigh2 = Counter()
            for aid in wdocs:
                for v in doc_ph.get(aid, ()):
                    if v != w and v != ph and v not in BRIDGE_STOP:
                        neigh2[v] += 1
            for v, cv in neigh2.most_common(MAX_N2):
                if cv < 3:   # 两跳权重下限
                    continue
                # 2025 未直连
                if inv.get(ph) and inv.get(v) and (inv[ph] & inv[v]):
                    continue
                # 变体过滤
                ta, tb = set(ph.lower().split()), set(v.lower().split())
                if ta & tb and len(ta & tb) / max(len(ta), len(tb), 1) > 0.4:
                    continue
                all_c.append({"A": ph, "B": v, "field": x["field"],
                              "via": w, "I": min(cw, cv)})
                got_seed += 1
                if got_seed >= 7:   # 每 seed 上限 7，学科均匀
                    break
            if got_seed >= 7:
                break
        if len(all_c) % 200 == 0:
            print(f"  累计候选 {len(all_c)} ({time.time()-t0:.0f}s)", flush=True)

    seen = set(); uniq = []
    for c in all_c:
        k = (min(c["A"], c["B"]), max(c["A"], c["B"]))
        if k not in seen:
            seen.add(k); uniq.append(c)
    print(f"候选(初筛): {len(uniq)} ({time.time()-t0:.0f}s)", flush=True)

    # 双重检查 2010-24 未直连（SQL 批量）
    con = duckdb.connect(":memory:")
    linked = set()
    sel = [c for c in uniq if 5 <= c["I"] <= 40]
    for c in sel:
        rows = con.execute(f"""
            SELECT 1 FROM read_parquet('{ANN}/year=*/part-0.parquet')
            WHERE year BETWEEN 2010 AND 2024
              AND list_contains(phrases, ?) AND list_contains(phrases, ?) LIMIT 1
        """, [c["A"], c["B"]]).fetchall()
        if rows:
            linked.add((min(c["A"], c["B"]), max(c["A"], c["B"])))
    con.close()
    final = [c for c in sel if (min(c["A"], c["B"]), max(c["A"], c["B"])) not in linked]
    print(f"I 5-40 + 未直连: {len(final)} ({time.time()-t0:.0f}s)", flush=True)

    # G 波动性（2010-2025）
    from concurrent.futures import ProcessPoolExecutor, as_completed
    results = []
    with ProcessPoolExecutor(max_workers=N_WORKERS) as ex:
        futs = {ex.submit(g_raw_series_fast, c["A"], c["B"], 2010, 2025): c for c in final}
        for i, f in enumerate(as_completed(futs), 1):
            c = futs[f]
            try:
                G = f.result()
                vals = [G[y] for y in range(2010, 2026) if G.get(y) is not None and G[y] > 0]
                if len(vals) < 4:
                    continue
                cv = float(np.std(vals) / max(np.mean(vals), 1e-9))
                if 0.3 <= cv <= 1.0:
                    results.append({**c, "cv": cv, "g_peak": float(max(vals))})
            except Exception:
                pass
            if i % 50 == 0:
                print(f"  G 计算 {i}/{len(final)} ({time.time()-t0:.0f}s)", flush=True)

    results.sort(key=lambda x: -x["cv"])
    print(f"CV 0.3-1.0 通过: {len(results)}", flush=True)
    top100 = results[:100]
    json.dump(top100, open(f"{OUT}/predict2026_top100.json", "w"), ensure_ascii=False, indent=1)

    fc = Counter(r["field"] for r in top100)
    print(f"\n=== 2026 预判 Top {len(top100)} 学科分布 ===")
    for f, c in fc.most_common(20):
        print(f"  {f}: {c}")
    print(f"\n→ {OUT}/predict2026_top100.json ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
