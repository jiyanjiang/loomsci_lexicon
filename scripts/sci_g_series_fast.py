# -*- coding: utf-8 -*-
"""G(AB,t) 快速版：一次查询取全部含 A/B 文章（带年份），Python 内按年分组。

相对 g_raw_series（每年 1 次全表扫描 × 16 次/对），本版每对只扫 1 次全表，
子网络构建逻辑与 g_raw_year 完全一致（TOP_K 邻居 + 原始边权 + 拉普拉斯伪逆）。
"""
import os
from collections import Counter
import numpy as np
import duckdb

ANN = "data/annotation/normalized"
TOP_K = 20


def g_raw_series_fast(A, B, y0, y1):
    """一次查询取出含 A/B 的全部文章 (year, phrases)，按年构建子网络算 G。"""
    con = duckdb.connect(":memory:")
    rows = con.execute(f"""
        SELECT a.year, a.phrases
        FROM read_parquet('{ANN}/year=*/part-0.parquet') a
        WHERE (list_contains(a.phrases, ?) OR list_contains(a.phrases, ?))
    """, [A, B]).fetchall()
    con.close()

    by_year = {}
    for yr, pl in rows:
        by_year.setdefault(yr, []).append(pl)

    out = {}
    for yr in range(y0, y1 + 1):
        pls = by_year.get(yr)
        if not pls:
            continue
        neigh_a, neigh_b, ab = Counter(), Counter(), 0
        for pl in pls:
            ps = set(pl)
            has_a, has_b = A in ps, B in ps
            if has_a and has_b:
                ab += 1
            if has_a:
                for w in ps:
                    if w != A and w != B:
                        neigh_a[w] += 1
            if has_b:
                for w in ps:
                    if w != A and w != B:
                        neigh_b[w] += 1
        na = neigh_a.most_common(TOP_K)
        nb = neigh_b.most_common(TOP_K)
        nodes = [A, B] + [w for w, _ in na if w not in (A, B)] \
                        + [w for w, _ in nb if w not in (A, B)]
        idx = {n: i for i, n in enumerate(nodes)}
        n = len(nodes)
        mat = np.zeros((n, n))
        mat[0, 1] = mat[1, 0] = ab
        for w, c in na:
            if w in idx:
                mat[0, idx[w]] = mat[idx[w], 0] = c
        for w, c in nb:
            if w in idx:
                mat[1, idx[w]] = mat[idx[w], 1] = c
        if mat.max() <= 0:
            out[yr] = 0.0
            continue
        deg = mat.sum(axis=1)
        L = np.diag(deg) - mat
        try:
            Lpinv = np.linalg.pinv(L)
            r = Lpinv[0, 0] + Lpinv[1, 1] - 2 * Lpinv[0, 1]
            out[yr] = 1.0 / r if r > 1e-12 else 0.0
        except Exception:
            continue
    return out


def g_n_raw_series_fast(A, B, y0, y1):
    """一次扫描全表：同时返回 G 序列与每年共现数 N（含A且含B的文章数）。

    G 与 N 共用同一次全表扫描（g_raw_series_fast 每对 1 次全表），
    命中判定（2016-2025 直连且增长）直接用 N，无需二次扫描。
    """
    con = duckdb.connect(":memory:")
    rows = con.execute(f"""
        SELECT a.year, a.phrases
        FROM read_parquet('{ANN}/year=*/part-0.parquet') a
        WHERE (list_contains(a.phrases, ?) OR list_contains(a.phrases, ?))
    """, [A, B]).fetchall()
    con.close()

    by_year = {}
    for yr, pl in rows:
        by_year.setdefault(yr, []).append(pl)

    G, N = {}, {}
    for yr in range(y0, y1 + 1):
        pls = by_year.get(yr)
        if not pls:
            G[yr] = 0.0
            N[yr] = 0
            continue
        neigh_a, neigh_b, ab = Counter(), Counter(), 0
        for pl in pls:
            ps = set(pl)
            has_a, has_b = A in ps, B in ps
            if has_a and has_b:
                ab += 1
            if has_a:
                for w in ps:
                    if w != A and w != B:
                        neigh_a[w] += 1
            if has_b:
                for w in ps:
                    if w != A and w != B:
                        neigh_b[w] += 1
        N[yr] = ab
        na = neigh_a.most_common(TOP_K)
        nb = neigh_b.most_common(TOP_K)
        nodes = [A, B] + [w for w, _ in na if w not in (A, B)] \
                        + [w for w, _ in nb if w not in (A, B)]
        idx = {n: i for i, n in enumerate(nodes)}
        n = len(nodes)
        mat = np.zeros((n, n))
        mat[0, 1] = mat[1, 0] = ab
        for w, c in na:
            if w in idx:
                mat[0, idx[w]] = mat[idx[w], 0] = c
        for w, c in nb:
            if w in idx:
                mat[1, idx[w]] = mat[idx[w], 1] = c
        if mat.max() <= 0:
            G[yr] = 0.0
            continue
        deg = mat.sum(axis=1)
        L = np.diag(deg) - mat
        try:
            Lpinv = np.linalg.pinv(L)
            r = Lpinv[0, 0] + Lpinv[1, 1] - 2 * Lpinv[0, 1]
            G[yr] = 1.0 / r if r > 1e-12 else 0.0
        except Exception:
            G[yr] = 0.0
    return G, N


if __name__ == "__main__":
    import time
    t0 = time.time()
    G = g_raw_series_fast("attention mechanism(s)", "transformer", 2010, 2025)
    print(f"attention×transformer 2010-2025: {time.time()-t0:.1f}s")
    print("G 序列:", {y: round(G[y], 1) for y in sorted(G)})
