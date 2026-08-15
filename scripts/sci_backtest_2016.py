# -*- coding: utf-8 -*-
"""历史模拟（真前验测试）：假装现在是 2016 年，预测 2017-2025 将直连的对

用户挑战（2026-08-14）：
- 用 2020-2025 数据预判 2026 突破——先做历史模拟验证方法
- 设计：2016 时刻，用 2010-2016 的 G 数据（未直连对）预测"将直连"
  → 2017-2025 的实际结果已存在 → 真实命中率（无法作弊）

候选池：2016 年热短语两两配对，过滤 2010-2016 未直连
特征：G 波动性(CV) + G 峰 + G AUC（2010-2016 窗口）
验证：2017-2025 是否出现稳定共现（N>=5 且 >=2 年）
"""
import json, os, sys, time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sci_g_series_fast import g_raw_series_fast

ANN = "data/annotation/normalized"
OUT = "data/sci/discovery/backtest"
os.makedirs(OUT, exist_ok=True)
N_WORKERS = 8
T0 = 2016          # 假装现在是 2016 年
PRED_WINDOW = (2017, 2025)  # 预测期（已有实际结果）
G_WINDOW = (2010, 2016)     # 用 2010-2016 的 G 数据

AI_TERMS = ['machine learning', 'deep learning', 'neural network', 'transformer',
            'large language model', 'artificial intelligence', 'llm', 'reinforcement learning',
            'computer vision', 'representation learning', 'generative', 'attention',
            'natural language processing', 'deep neural network', 'self supervised',
            'fine tuning', 'pre trained', 'pre training', 'zero shot', 'encoder',
            'decoder', 'foundation model', 'downstream task', 'vision language',
            'image generation', 'diffusion model', 'training data', 'prompt']


def n_series(A, B, y0, y1):
    con = duckdb.connect(":memory:")
    rows = con.execute(f"""
        WITH docs AS (
            SELECT a.arxiv_id, EXTRACT(YEAR FROM p.submission_date) AS yr
            FROM read_parquet('{ANN}/year=*/part-0.parquet') a
            JOIN read_parquet('data/parquet/papers/year=*/*.parquet') p ON a.arxiv_id = p.arxiv_id
            WHERE list_contains(a.phrases, ?) AND list_contains(a.phrases, ?)
        )
        SELECT yr, count(*) FROM docs GROUP BY yr ORDER BY yr
    """, [A, B]).fetchall()
    con.close()
    return {int(y): int(n) for y, n in rows}


def build_candidates():
    """2016 热短语配对，过滤 2010-2016 未直连"""
    con = duckdb.connect(":memory:")
    rows = con.execute(f"""
        SELECT ph, count(*) n FROM (
            SELECT unnest(phrases) ph FROM read_parquet('{ANN}/year=2016/part-0.parquet')
        ) GROUP BY ph ORDER BY n DESC LIMIT 6000
    """).fetchall()
    # 用中等频次短语（30-800）：2016 已有一定存在感但未到饱和，更可能 2017+ 新直连
    hot = [(ph, n) for ph, n in rows if 30 <= n <= 800]
    rows16 = con.execute(f"""
        SELECT arxiv_id, unnest(phrases) AS ph FROM read_parquet('{ANN}/year=2016/part-0.parquet')
    """).fetchall()
    con.close()
    inv = {}
    for aid, ph in rows16:
        inv.setdefault(ph, set()).add(aid)

    # 短语→文档(2010-2016)：一次全量 unnest 建倒排，避免类型推断问题
    con = duckdb.connect(":memory:")
    rows = con.execute(f"""
        SELECT arxiv_id, unnest(phrases) AS ph FROM read_parquet('{ANN}/year=*/part-0.parquet')
        WHERE year BETWEEN 2010 AND 2016
    """).fetchall()
    con.close()
    inv1610 = {}
    for aid, ph in rows:
        inv1610.setdefault(ph, set()).add(aid)

    cands = []
    phrases = [p for p, _ in hot]
    for i in range(len(phrases)):
        for j in range(i + 1, len(phrases)):
            A, B = phrases[i], phrases[j]
            # 变体/套话过滤
            ta, tb = set(A.lower().split()), set(B.lower().split())
            if ta & tb and len(ta & tb) / max(len(ta), len(tb), 1) > 0.4:
                continue
            # 2010-2016 是否直连（t_p<=2016 则排除）
            if inv1610.get(A) and inv1610.get(B) and (inv1610[A] & inv1610[B]):
                continue
            # 两个短语 2016 都需有存在感（可能首连在 2017+）
            if not inv.get(A) or not inv.get(B):
                continue
            is_ai = any(ai in A.lower() or ai in B.lower() for ai in AI_TERMS)
            cands.append({"A": A, "B": B, "n2016": 0, "is_ai": is_ai})
    print(f"2016 未直连候选: {len(cands)}")
    return cands


def worker(args):
    A, B, n2016, is_ai = args
    try:
        # G 序列（2010-2016）
        G = g_raw_series_fast(A, B, 2010, 2016)
        vals = [G[y] for y in range(2010, 2017) if G.get(y) is not None and G[y] > 0]
        if len(vals) < 3:
            return {"A": A, "B": B, "error": "sparse"}
        cv = float(np.std(vals) / max(np.mean(vals), 1e-9))
        peak = float(max(vals))
        auc = float(np.trapezoid(vals, range(2010, 2010 + len(vals))))
        slope = float(np.polyfit(range(len(vals)), np.log(vals), 1)[0]) if len(vals) >= 3 else 0.0
        # 实际结果（2017-2025 是否直连）
        N = n_series(A, B, 2017, 2025)
        nn = {y: N.get(y, 0) for y in range(2017, 2026)}
        n_links = sum(1 for y in range(2017, 2026) if nn[y] > 0)
        n_years_ge5 = sum(1 for y in range(2017, 2026) if nn[y] >= 5)
        max_n = max(nn.values()) if nn else 0
        first_link = min((y for y in range(2017, 2026) if nn[y] > 0), default=None)
        return {"A": A, "B": B, "n2016": n2016, "is_ai": is_ai,
                "cv": cv, "peak": peak, "auc": auc, "slope": slope,
                "n_links": n_links, "n_years_ge5": n_years_ge5, "max_n": max_n,
                "first_link": first_link, "N": nn}
    except Exception as e:
        return {"A": A, "B": B, "error": str(e)[:60]}


def main():
    t0 = time.time()
    cands = build_candidates()
    # 用"双方 2016 频次乘积"排序（大节点对优先），取 top 400
    # 注：频次积 = 两概念各自 2016 出现文档数之积，proxy 其"强对"地位
    freq = {}
    for c in cands:
        freq[(c["A"], c["B"])] = c["n2016"]
    # 重新读频次
    con = duckdb.connect(":memory:")
    fr = con.execute(f"""
        SELECT ph, count(*) n FROM (
            SELECT unnest(phrases) ph FROM read_parquet('{ANN}/year=2016/part-0.parquet')
        ) GROUP BY ph
    """).fetchall()
    con.close()
    fmap = {ph: n for ph, n in fr}
    scored = []
    for c in cands:
        prod = fmap.get(c["A"], 0) * fmap.get(c["B"], 0)
        scored.append({**c, "freq_prod": prod})
    scored.sort(key=lambda x: -x["freq_prod"])
    top = scored[:400]
    print(f"算 G: {len(top)} 对 (按2016频次积排序)", flush=True)
    jobs = [(c["A"], c["B"], c["n2016"], c["is_ai"]) for c in top]
    results = []
    with ProcessPoolExecutor(max_workers=N_WORKERS) as ex:
        futs = [ex.submit(worker, j) for j in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            results.append(f.result())
            if i % 50 == 0:
                print(f"  {i}/{len(jobs)} ({time.time()-t0:.0f}s)", flush=True)

    ok = [r for r in results if "error" not in r]
    json.dump(results, open(f"{OUT}/backtest_2016.json", "w"), ensure_ascii=False, indent=1)

    # 命中定义：2017-2025 出现 >=1 年共现（宽松）或 >=5 次共现（严格）
    print(f"\n=== 历史模拟：2016 时刻预测 2017-2025 直连（{len(ok)} 有效对）===")
    for def_name, cond in [("宽松:2017-25任一>0", lambda r: r["n_links"] > 0),
                           ("严格:任一年>=5", lambda r: r["n_years_ge5"] > 0),
                           ("严格:max>=5", lambda r: r["max_n"] >= 5)]:
        hit = sum(1 for r in ok if cond(r))
        print(f"  {def_name}: 命中 {hit}/{len(ok)} = {hit/len(ok)*100:.1f}%")

    # 按 CV 分层（我们的预测信号是 CV 高）
    print(f"\n=== 按 G 波动性(CV)分层：预测命中率 ===")
    for lo, hi, tag in [(0, 0.3, "CV<0.3(低波动)"), (0.3, 0.6, "CV 0.3-0.6"),
                        (0.6, 1.0, "CV 0.6-1.0(高波动)"), (1.0, 99, "CV>1.0(极高)")]:
        sub = [r for r in ok if lo <= r.get("cv", 0) < hi]
        if len(sub) < 10:
            continue
        for def_name, cond in [("宽松:>0", lambda r: r["n_links"] > 0),
                               ("严格:>=5", lambda r: r["max_n"] >= 5)]:
            hit = sum(1 for r in sub if cond(r))
            print(f"  {tag:18s} n={len(sub):3d} | {def_name}: {hit} = {hit/len(sub)*100:.0f}%")

    # 基线：随机抽样命中率（用整体命中率近似）
    # 对照：CV top 100 vs 全部
    sub_top = sorted(ok, key=lambda r: -r.get("cv", 0))[:100]
    for def_name, cond in [("宽松:>0", lambda r: r["n_links"] > 0),
                           ("严格:>=5", lambda r: r["max_n"] >= 5)]:
        hit = sum(1 for r in sub_top if cond(r))
        print(f"\nCV top100 预测集: {def_name} 命中 {hit}/100 = {hit}%")
    # AI vs 非AI
    print("\n=== AI vs 非AI ===")
    for tag, sel in [("AI对", [r for r in ok if r["is_ai"]]),
                     ("非AI对", [r for r in ok if not r["is_ai"]])]:
        hit = sum(1 for r in sel if r["max_n"] >= 5)
        print(f"  {tag}: n={len(sel)} 严格命中 {hit} = {hit/len(sel)*100:.0f}%")
    print(f"\n→ {OUT}/backtest_2016.json ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
