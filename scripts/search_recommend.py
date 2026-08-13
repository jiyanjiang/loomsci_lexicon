#!/usr/bin/env python3
"""焦点推荐：概念组合（AB 对 / AC-CB 桥接）独立程序（2026-08-13 定稿）。

与 RBO 脱钩（用户定稿）：
  - RBO/FTS 只做论文检索（纯列表 + 得分）
  - 焦点推荐只出概念组合（AB 对 + AC-CB 桥接），不输出论文列表
  - 概念组合聚合不依赖 RBO 排序，只依赖「包含查询短语的论文」的 normalized 标注

流程：
  1. LLM 编排 ≤5 有序短语（orchestrate_query，skip 判断 + 缓存）
  2. raw 标注 SQL 预筛：含任一查询短语的论文（轻量，LIMIT cap）
  3. normalized 标注反查这些论文的短语序列
  4. 聚合 AB 对（A=查询短语 × B=邻近概念）+ AC-CB 桥接（A=查询短语 — C — B）
  5. 只输出概念组合（pairs + bridges），附 A 侧约束校验

用法：
  python scripts/search_recommend.py --query "nickel-based superconductivity"
  python scripts/search_recommend.py --query "transmon qubit, surface code"
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from orchestrate_query import orchestrate_query, MAX_PHRASES
from search_rbo import (_all_annotation_years, _load_norm_phrases,
                        _expand_query_a, _a_is_query)

import duckdb

CAP_PAPERS = 2000   # 预筛论文上限（含查询短语的论文数量控制）


def _find_candidate_ids(query_phrases: list[str], years: list[int] | None = None,
                        cap: int = CAP_PAPERS) -> list[tuple[str, int]]:
    """raw 标注 SQL 预筛：含任一查询短语的论文 → [(arxiv_id, year)]。"""
    if years is None:
        years = _all_annotation_years()
    cond = " OR ".join(["list_contains(phrases, ?)"] * len(query_phrases))
    out: list[tuple[str, int]] = []
    for y in years:
        fp = os.path.join(config.ANN_RAW, f"year={y}", "part-0.parquet")
        if not os.path.exists(fp):
            continue
        con = duckdb.connect(":memory:")
        try:
            rows = con.execute(
                f"SELECT arxiv_id FROM read_parquet('{fp}') "
                f"WHERE {cond} LIMIT {cap}", query_phrases).fetchall()
        finally:
            con.close()
        for (aid,) in rows:
            out.append((str(aid), y))
    return out


def recommend(query_phrases: list[str], years: list[int] | None = None,
              top_n: int = 50) -> dict:
    """焦点推荐：只出概念组合（AB 对 + AC-CB 桥接）。"""
    if years is None:
        years = _all_annotation_years()
    # 1) raw 预筛：含查询短语的论文
    cand_ids = _find_candidate_ids(query_phrases, years)
    if not cand_ids:
        return {"pairs": [], "bridges": [], "n_papers": 0}
    # 2) normalized 反查这些论文的短语
    norm = _load_norm_phrases(cand_ids)
    paper_phrases = [ph for ph in norm.values() if ph]
    if not paper_phrases:
        return {"pairs": [], "bridges": [], "n_papers": len(cand_ids)}
    # 3) 聚合（复用 search_rbo 的纯函数，A 侧约束）
    from search_rbo import _aggregate_from_papers
    agg = _aggregate_from_papers(paper_phrases, query_phrases)
    agg["n_papers"] = len(paper_phrases)
    agg["pairs"] = agg["pairs"][:top_n]
    agg["bridges"] = agg["bridges"][:top_n]
    return agg


def main():
    ap = argparse.ArgumentParser(description="焦点推荐：概念组合（AB/桥接）")
    ap.add_argument("--query", required=True, help="自然语言或逗号分隔短语（≤5）")
    ap.add_argument("--years", default=None, help="限定年份（默认全部）")
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    years = None
    if args.years:
        from search_rbo import _parse_years
        years = _parse_years(args.years)

    t0 = time.time()
    phrases, mode = orchestrate_query(args.query)
    if not phrases:
        raise SystemExit(f"编排失败: {mode}")
    phrases = phrases[:MAX_PHRASES]
    out = recommend(phrases, years=years, top_n=args.top)
    elapsed = time.time() - t0

    print(json.dumps({
        "query": args.query, "phrases": phrases, "mode": mode,
        "n_papers": out["n_papers"],
        "pairs": out["pairs"], "bridges": out["bridges"],
        "elapsed_s": round(elapsed, 2),
    }, ensure_ascii=False, indent=2 if args.pretty else None))
    print(f"\n[done] {len(out['pairs'])} pairs, {len(out['bridges'])} bridges, "
          f"{out['n_papers']} 篇, {elapsed:.2f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
