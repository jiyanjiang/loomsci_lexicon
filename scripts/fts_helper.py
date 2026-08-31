#!/usr/bin/env python3
"""FTS(BM25) 查询助手（本项目自用版，2026-08-13 从 sci365 compare_fts_vs_raw.py 复制）。

对齐用户原则「不调用外部目录程序，复制一份过来」：
  - 查询库 = config.FTS_DB（data/fts.duckdb，由 scripts/build_fts_from_parquet.py 构建）
  - 线程本地连接池（DuckDB 连接非线程安全，绝不跨线程共享）

接口（与 sci365 版一致，便于 predict_verify_refs / fetch_real_papers 无缝切换）：
  fts_search(query_text, top_n=10, offset=0, with_abstract=True, since=None) -> list[dict]
  fts_count(query_text, since=None) -> int
"""
from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

FTS_DB = config.FTS_DB
DEFAULT_TOP = 10

_fts_local = threading.local()


def _get_fts_con():
    con = getattr(_fts_local, "fts_con", None)
    if con is None:
        import duckdb
        con = duckdb.connect(FTS_DB, read_only=True)
        _fts_local.fts_con = con
    return con


def fts_search(query_text: str, top_n: int = DEFAULT_TOP, offset: int = 0,
               with_abstract: bool = True, since: str | None = None) -> list[dict]:
    con = _get_fts_con()
    date_clause = "AND CAST(submission_date AS DATE) >= ?" if since else ""
    if with_abstract:
        sql = f"""SELECT paper_id, title, authors, arxiv_id, submission_date, abstract,
                        fts_main_papers.match_bm25(paper_id, ?) AS score
                 FROM papers
                 WHERE fts_main_papers.match_bm25(paper_id, ?) IS NOT NULL {date_clause}
                 ORDER BY score DESC
                 LIMIT ? OFFSET ?"""
    else:
        sql = f"""SELECT paper_id, title, authors, arxiv_id, submission_date,
                        fts_main_papers.match_bm25(paper_id, ?) AS score
                 FROM papers
                 WHERE fts_main_papers.match_bm25(paper_id, ?) IS NOT NULL {date_clause}
                 ORDER BY score DESC
                 LIMIT ? OFFSET ?"""
    params = [query_text, query_text]
    if since:
        params.append(since)
    params += [top_n, offset]
    rows = con.execute(sql, params).fetchall()
    out = []
    for r in rows:
        if with_abstract:
            out.append({
                "paper_id": r[0], "title": r[1], "authors": r[2], "arxiv_id": r[3],
                "date": str(r[4]) if r[4] is not None else "", "abstract": r[5],
                "score": round(r[6], 4),
            })
        else:
            out.append({
                "paper_id": r[0], "title": r[1], "authors": r[2], "arxiv_id": r[3],
                "date": str(r[4]) if r[4] is not None else "",
                "score": round(r[5], 4),
            })
    return out


def fts_count(query_text: str, since: str | None = None) -> int:
    """FTS 命中总数（只读，无副作用）。"""
    con = _get_fts_con()
    date_clause = "AND CAST(submission_date AS DATE) >= ?" if since else ""
    params = [query_text]
    if since:
        params.append(since)
    return con.execute(
        f"SELECT count(*) FROM papers "
        f"WHERE fts_main_papers.match_bm25(paper_id, ?) IS NOT NULL {date_clause}",
        params,
    ).fetchone()[0]
