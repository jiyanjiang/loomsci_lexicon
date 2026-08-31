#!/usr/bin/env python3
"""构建 cs/非cs 分组的逐年 G_max 参考（二分归一基准）。

方法：取 100 候选概念对的并集概念，随机配对 N 对 → 每对算逐年 G_raw
→ 按策展分组（cs/non-cs，联合命中率）→ 各年份取组内 max → 存 JSON。

输出：data/binary_gmax_20260812.json {years, G_max: {cs: {year: max}, non-cs: {year: max}}}
供 web /distance 二分归一：G_norm = G_raw / G_max[group][year]
"""
import json
import os
import random
import sys
import duckdb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from g_ab_calc import compute_g_series  # noqa: E402

ANN = os.path.join(ROOT, "data", "annotation", "normalized")
CAT_DB = os.path.join(ROOT, "data", "category_map.duckdb")
YEARS = list(range(1991, 2026))
OUT = os.path.join(ROOT, "data", "binary_gmax_20260812.json")
N_PAIRS = 200
SEED = 42


def _pair_binary_group(A, B):
    """策展：联合命中率选组（与 web/explore.py 一致）。返回 group 或 None。"""
    try:
        cat = duckdb.connect(CAT_DB, read_only=True)
        ann25 = os.path.join(ANN, "year=2025", "part-0.parquet")
        va = vb = None
        for c in (A, B):
            for v in [c, c + "(s)", c + "es"]:
                n = cat.execute(
                    f"SELECT count(*) FROM read_parquet('{ann25}') WHERE list_contains(phrases, ?)",
                    [v]).fetchone()[0]
                if n > 0:
                    if c == A:
                        va = v
                    else:
                        vb = v
                    break
        if not va or not vb:
            cat.close()
            return None
        cs_hits = cat.execute(f"""
            SELECT count(*) FROM read_parquet('{ann25}') a
            JOIN paper_category pc USING (arxiv_id)
            WHERE list_contains(a.phrases, ?) AND list_contains(a.phrases, ?)
              AND (pc.domain = 'cs' OR pc.domain = 'eess')
        """, [va, vb]).fetchone()[0]
        non_hits = cat.execute(f"""
            SELECT count(*) FROM read_parquet('{ann25}') a
            JOIN paper_category pc USING (arxiv_id)
            WHERE list_contains(a.phrases, ?) AND list_contains(a.phrases, ?)
              AND pc.domain != 'cs' AND pc.domain != 'eess'
        """, [va, vb]).fetchone()[0]
        cat.close()
        cs_hits, non_hits = int(cs_hits), int(non_hits)
        if cs_hits == 0 and non_hits == 0:
            return None
        return "cs" if cs_hits >= non_hits else "non-cs"
    except Exception:
        return None


def _worker(args4):
    A, B, g, yrs = args4
    try:
        _, gs = compute_g_series(A, B, yrs)
        return A, B, g, gs
    except Exception:
        return A, B, g, [None] * len(yrs)


def main():
    random.seed(SEED)
    with open(os.path.join(ROOT, "data", "distance_candidate_pairs.json"), encoding="utf-8") as f:
        pairs = json.load(f)["pairs"]
    pool = sorted(set(p["A"] for p in pairs) | set(p["B"] for p in pairs))
    all_pairs = list(random.sample(
        [(pool[i], pool[j]) for i in range(len(pool)) for j in range(i + 1, len(pool))],
        N_PAIRS))

    # 分组（策展：联合命中率）
    groups = {}
    for A, B in all_pairs:
        g = _pair_binary_group(A, B)
        if g:
            groups[(A, B)] = g
    print(f"可分组的对 {len(groups)}/{len(all_pairs)}")

    # 逐年 G_raw（全 35 年，8 进程并行）
    from concurrent.futures import ProcessPoolExecutor

    tasks = [(A, B, g, YEARS) for (A, B), g in groups.items()]
    print(f"并行计算 {len(tasks)} 对 × {len(YEARS)} 年 ...", flush=True)
    nproc = min(os.cpu_count() or 4, 8)
    with ProcessPoolExecutor(max_workers=nproc) as ex:
        results = list(ex.map(_worker, tasks))

    # 按组取逐年 max
    gmax = {"cs": {}, "non-cs": {}}
    for yi, y in enumerate(YEARS):
        for grp in ("cs", "non-cs"):
            vals = [gs[yi] for _, _, g, gs in results
                    if g == grp and gs[yi] is not None and gs[yi] > 0]
            gmax[grp][y] = round(max(vals), 4) if vals else None

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"years": YEARS, "G_max": gmax}, f, ensure_ascii=False, indent=1)
    print(f"\n已写入 {OUT}")
    print("cs 逐年 G_max 样例:", {y: gmax["cs"][y] for y in (2015, 2017, 2020, 2025)})
    print("non-cs 逐年 G_max 样例:", {y: gmax["non-cs"][y] for y in (2015, 2017, 2020, 2025)})


if __name__ == "__main__":
    main()
