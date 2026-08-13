#!/usr/bin/env python3
"""RBO 语义检索核心 —— 基于 raw 标注（文章短语命中顺序）。

设计（对齐 sci365 search_conversational.py 的「倒排预筛 + RBO 精排」双段）：
  1. 倒排预筛：raw 标注按年分片，命中查询短语的文章进候选（跳过无关文章）
  2. RBO 精排：候选文章短语序列(命中顺序) × 查询短语序列(重要性降序) → RBO p=0.9
  3. 归一化：rbo / max_rbo(len_query, len_article) → 0-1 直观分
  4. 排序：rbo 降序（同级日期降序），返回 top-N

数据源：data/annotation/raw/year={Y}/part-0.parquet
  schema: (arxiv_id, phrases[], n_phrases, year)
  phrases[] 顺序 = 标题+摘要命中顺序（标题先于摘要，先说的比后说的重要）

与 FTS(BM25) 互补：FTS 找字面匹配，RBO 找语义邻近概念。

用法：
  python scripts/search_rbo.py --query "transmon qubit, surface code" --top 10
  python scripts/search_rbo.py --query "..." --years 1991-1995
  python scripts/search_rbo.py --query "..." --years 1991-1995 --pretty
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
from rbo import rbo, max_rbo
from phrase_forms import strip_plural as _strip_plural

import duckdb

P = 0.9          # RBO 持久化参数（头部敏感）
CAP_CANDIDATES = 5000   # 倒排预筛候选上限（避免常见词拉爆内存）

# ---------------- 短语命中状态判定（B2）----------------
# 缓存 scan 词典（lexicon_2025.csv：rank,term,n_tokens,...）→ set of terms
_LEXICON_TERMS: set[str] | None = None


def _load_lexicon_terms() -> set[str]:
    """加载学术短语词典（data/cumulative/lexicon_*.csv 最新），返回短语集合。"""
    global _LEXICON_TERMS
    if _LEXICON_TERMS is not None:
        return _LEXICON_TERMS
    terms: set[str] = set()
    lex_dir = config.LEXICON_DIR
    if os.path.isdir(lex_dir):
        files = sorted(glob.glob(os.path.join(lex_dir, "lexicon_*.csv")))
        if files:
            latest = files[-1]
            try:
                import csv as _csv
                with open(latest, newline="", encoding="utf-8") as f:
                    for r in _csv.DictReader(f):
                        t = (r.get("term") or "").strip().lower()
                        if t:
                            terms.add(t)
            except Exception:
                pass
    _LEXICON_TERMS = terms
    return terms


_ANN_PHRASE_CACHE: dict[int, set[str]] = {}


def _load_ann_phrases(year: int) -> set[str]:
    """读单年 raw 标注的短语集合（缓存）。用于判定短语在数据中是否存在。"""
    if year in _ANN_PHRASE_CACHE:
        return _ANN_PHRASE_CACHE[year]
    fp = os.path.join(config.ANN_RAW, f"year={year}", "part-0.parquet")
    s: set[str] = set()
    if os.path.exists(fp):
        try:
            con = duckdb.connect(":memory:")
            rows = con.execute(
                f"SELECT DISTINCT unnest(phrases) FROM read_parquet('{fp}')").fetchall()
            con.close()
            s = {r[0] for r in rows}
        except Exception:
            pass
    _ANN_PHRASE_CACHE[year] = s
    return s


def phrase_match_status(phrase: str, years: list[int] | None = None) -> dict:
    """判定单个短语的命中状态（B2，诚实告知——防「会白高兴」）。

    判定依据是 **raw 标注数据中存在性**（非词典）：
      - exact:   在标注数据中精确存在（检索能精确命中）
      - loose:   无精确，但单复数/缩写归并后存在（如 nickelates→nickelate）
      - missing: 数据中完全不存在（检索真的无精确结果，诚实告知）

    理由（2026-08-13 实测）：词典只收多词学术短语（458k），单 token 词
    （superconductivity/nickelates）天然不在词典 → 不能以词典判 missing。
    应以标注数据为准：superconductivity 在标注中 846 篇（2025）→ exact；
    nickelates 在标注中 0 篇 → missing（用户实测确认 scan 缺 nickelates）。
    """
    if years is None:
        years = _all_annotation_years()
    # 多查几年（用近年数据判定，词汇随时间增长）
    probe_years = years[-3:] if len(years) >= 3 else years
    ann_sets = [_load_ann_phrases(y) for y in probe_years]
    union: set[str] = set()
    for s in ann_sets:
        union |= s
    p = phrase.strip().lower()
    if p in union:
        return {"phrase": phrase, "status": "exact", "loose_candidate": None}
    # loose：去尾 s/es 后命中（单复数归并，如 nickelates→nickelate）
    c = _strip_plural(p)
    if c and c in union:
        return {"phrase": phrase, "status": "loose", "loose_candidate": c}
    return {"phrase": phrase, "status": "missing", "loose_candidate": None}


# ---------------- 倒排预筛 ----------------
def _load_raw_annotation(year: int):
    """读单年 raw 标注 → {arxiv_id: [phrases...]}（保持命中顺序）。"""
    pat = os.path.join(config.ANN_RAW, f"year={year}", "part-0.parquet")
    if not os.path.exists(pat):
        return {}
    con = duckdb.connect(database=":memory:")
    rows = con.execute(
        "SELECT arxiv_id, phrases FROM read_parquet(?)", [pat]).fetchall()
    con.close()
    return {str(aid): list(ph) for aid, ph in rows}


def _candidates_by_phrase(ann: dict, query: list[str]):
    """倒排预筛：含任一查询短语的文章 → (arxiv_id, 命中短语列表)。"""
    qset = set(query)
    cands = []
    for aid, ph in ann.items():
        hits = [p for p in ph if p in qset]
        if hits:
            cands.append((aid, hits))
    return cands


# ---------------- RBO 精排 ----------------
def rank_articles(ann: dict, query: list[str], top_n: int = 20,
                  p: float = P, cap: int = CAP_CANDIDATES):
    """对全部文章做倒排预筛 + RBO 精排。返回 [{arxiv_id, rbo, rbo_norm, hits}, ...]。

    query: 有序短语列表（重要性降序，第一个最重要）。
    ann:   {arxiv_id: [phrases...]}，phrases 为命中顺序。
    """
    cands = _candidates_by_phrase(ann, query)
    # 按命中数降序截 cap（常见词时保留最相关候选）
    cands.sort(key=lambda x: -len(x[1]))
    cands = cands[:cap]
    nq = len(query)

    results = []
    for aid, hits in cands:
        art = ann[aid]
        score = rbo(query, art, p)
        if score <= 0.0:
            continue
        denom = max_rbo(nq, len(art), p)
        norm = (score / denom) if denom > 0 else 0.0
        results.append({
            "arxiv_id": aid,
            "rbo": round(score, 4),
            "rbo_norm": round(norm, 4),
            "n_hits": len(hits),
            "hits": hits,
            "_phrases": art,      # 完整短语序列（供概念组合聚合；序列化时剥离）
        })
    results.sort(key=lambda r: r["rbo"], reverse=True)
    return results[:top_n]


def _sql_candidates(year: int, query_phrases: list[str], cap: int):
    """SQL 下推倒排预筛：只取含任一查询短语的文章行（不全量读入内存）。

    返回 {arxiv_id: [phrases...]}（保持命中顺序）。利用 DuckDB list_contains
    过滤 + LIMIT cap（避免常见词拉爆）。
    """
    fp = os.path.join(config.ANN_RAW, f"year={year}", "part-0.parquet")
    if not os.path.exists(fp):
        return {}
    # 对每个查询短语做 list_contains 过滤（OR 合并），LIMIT 控制候选规模
    cond = " OR ".join(["list_contains(phrases, ?)"] * len(query_phrases))
    con = duckdb.connect(database=":memory:")
    try:
        rows = con.execute(
            f"SELECT arxiv_id, phrases FROM read_parquet('{fp}') "
            f"WHERE {cond} LIMIT {cap}", query_phrases).fetchall()
    finally:
        con.close()
    return {str(aid): list(ph) for aid, ph in rows}


def search_rbo(query_phrases: list[str], years: list[int] | None = None,
               top_n: int = 20, p: float = P, with_pairs: bool = False):
    """跨年 RBO 检索。years=None → 全部年份。返回排序结果（含年份标注）。

    with_pairs=True 时附带概念组合聚合（AB 对 / A-C-B 桥接），
    返回 dict: {results, pairs, bridges}。

    数据源设计（用户 2026-08-13 核心设计）：
      - RBO 主检索 → raw 标注（LLM 编排短语形态不可控，宽松匹配）
      - AC/CB 桥接聚合 → normalized 标注（规则全可控，单复数/缩写已归并，
        桥接信号更准确——同一概念的不同形态不会分裂成两个节点）

    性能（2026-08-13 优化）：
      - 倒排预筛下推到 DuckDB SQL（list_contains 过滤 + LIMIT cap），
        避免每年全量 parquet 读入 Python 内存（34 年累计曾 13s+）。
    """
    if years is None:
        years = _all_annotation_years()
    all_results = []
    for y in years:
        t0 = time.time()
        ann = _sql_candidates(y, query_phrases, CAP_CANDIDATES)
        if not ann:
            print(f"  [year={y}] 无候选", file=sys.stderr)
            continue
        res = rank_articles(ann, query_phrases, top_n=top_n * 3, p=p)  # 每年多取，跨年合并再截
        for r in res:
            r["year"] = y
        all_results.extend(res)
        print(f"  [year={y}] 候选 {len(ann)} → RBO 命中 {len(res)} ({time.time()-t0:.2f}s)",
              file=sys.stderr)
    all_results.sort(key=lambda r: r["rbo"], reverse=True)
    top = all_results[:top_n]
    # A2：补元数据（title/authors/year/abstract）
    _attach_metadata(top)
    # A3：统一论文 schema（FTS/RBO 共用）
    top = [normalize_paper_schema(r, engine="rbo", rank=i + 1)
           for i, r in enumerate(top)]
    if with_pairs:
        # 概念组合聚合需原始结果（含 _phrases），用规范化前的数据
        pairs_info = aggregate_pairs(all_results[:top_n], query_phrases)
        return {"results": top, **pairs_info}
    return top


def _attach_metadata(results: list[dict]):
    """给 RBO 结果补论文元数据（A2）。results 原地修改。"""
    global _KNOWN_YEARS
    ids = [r["arxiv_id"] for r in results]
    for r in results:
        if r.get("year"):
            _KNOWN_YEARS[r["arxiv_id"]] = r["year"]
    meta = _load_paper_metadata(ids)
    for r in results:
        m = meta.get(r["arxiv_id"]) or {}
        r["title"] = m.get("title")
        r["authors"] = m.get("authors")
        r["year"] = m.get("year") or r.get("year")
        r["abstract"] = m.get("abstract")


# ---------------- 统一论文 schema（A3）----------------
def normalize_paper_schema(r: dict, engine: str = "rbo", rank: int = 0) -> dict:
    """FTS / RBO 统一论文 schema（设计规划 1.3.3）。

    {arxiv_id, title, authors, year, abstract,
     score, score_type, rank, engine, hit_phrases}
    - RBO: score = rbo_norm（0-1 直观分），score_type='rbo'
    - FTS: score = bm25 分，score_type='bm25'
    前端双档切换时共用同一渲染组件。
    """
    if engine == "rbo":
        score = r.get("rbo_norm") if r.get("rbo_norm") is not None else r.get("rbo")
        score_type = "rbo"
        hit_phrases = [
            {"query_phrase": q, "doc_phrase": q, "match_type": "exact"}
            for q in (r.get("hits") or [])
        ]
    else:   # fts
        score = r.get("score")
        score_type = "bm25"
        hit_phrases = [{"query_phrase": p, "doc_phrase": p, "match_type": "exact"}
                       for p in (r.get("hits") or [])]
    return {
        "arxiv_id": r.get("arxiv_id"),
        "title": r.get("title"),
        "authors": r.get("authors"),
        "year": r.get("year"),
        "abstract": r.get("abstract"),
        "score": score,
        "score_type": score_type,
        "rank": rank,
        "engine": engine,
        "hit_phrases": hit_phrases,
    }


def _load_norm_phrases(ids_year: list[tuple[str, int]]) -> dict[str, list[str]]:
    """按 (arxiv_id, year) 从 normalized 标注反查短语（用于桥接聚合）。

    只查 top 论文对应的年份切片（轻量），返回 {arxiv_id: [phrases...]}。
    若 normalized 切片缺失（如未标注的年份），回退 raw 的 _phrases。
    """
    by_year: dict[int, list[str]] = {}
    for aid, y in ids_year:
        by_year.setdefault(y, []).append(aid)
    out: dict[str, list[str]] = {}
    for y, ids in by_year.items():
        fp = os.path.join(config.ANN_NORM, f"year={y}", "part-0.parquet")
        if not os.path.exists(fp):
            continue
        ph = ",".join(["?"] * len(ids))
        con = duckdb.connect(database=":memory:")
        try:
            rows = con.execute(
                f"SELECT arxiv_id, phrases FROM read_parquet('{fp}') "
                f"WHERE arxiv_id IN ({ph})", ids).fetchall()
        finally:
            con.close()
        for aid, phrases in rows:
            out[str(aid)] = list(phrases)
    return out


def _all_annotation_years() -> list[int]:
    """扫描 raw 标注存在的全部年份。"""
    ys = []
    for d in glob.glob(os.path.join(config.ANN_RAW, "year=*")):
        try:
            ys.append(int(os.path.basename(d).split("=")[1]))
        except (ValueError, IndexError):
            continue
    return sorted(ys)


# ---------------- 论文元数据反查（A2）----------------
_META_CACHE: dict[str, dict] = {}
_KNOWN_YEARS: dict[str, int] = {}   # arxiv_id → year（供 parquet 回退定位年份切片）


def _load_paper_metadata(arxiv_ids: list[str]) -> dict[str, dict]:
    """按 arxiv_id 批量反查论文元数据（title/authors/year/abstract）。

    数据源（A2，2026-08-13）：
      - 优先 FTS 库（config.FTS_DB 的 papers 表，快）
      - FTS 库缺失/无该 id 时回退 parquet 事实层（config.PAPERS_DIR）
    返回 {arxiv_id: {title, authors, year, abstract}}（缺失的论文不返回）。

    注意：FTS 库可能是前 5 年小样本（分享场景），全量论文元数据
    从 parquet 回退补齐。结果缓存（进程内），避免重复查询。
    """
    missing = [aid for aid in arxiv_ids if aid not in _META_CACHE]
    if not missing:
        return {aid: _META_CACHE[aid] for aid in arxiv_ids if aid in _META_CACHE}
    found: dict[str, dict] = {}

    # 1) FTS 库反查
    if config.FTS_DB and os.path.exists(config.FTS_DB):
        try:
            con = duckdb.connect(config.FTS_DB, read_only=True)
            ph = ",".join(["?"] * len(missing))
            rows = con.execute(
                f"SELECT arxiv_id, title, authors, year, abstract FROM papers "
                f"WHERE arxiv_id IN ({ph})", missing).fetchall()
            con.close()
            for aid, title, authors, year, abstract in rows:
                found[str(aid)] = {
                    "title": title, "authors": authors, "year": year,
                    "abstract": abstract,
                }
        except Exception:
            pass   # FTS 库不可用 → 走 parquet 回退

    # 2) parquet 回退（未在 FTS 找到的）
    still_missing = [aid for aid in missing if aid not in found]
    if still_missing:
        # 利用已知年份（调用方传入的 arxiv_id+year）分组，只查对应年份切片
        by_year: dict[int, list[str]] = {}
        for aid in still_missing:
            y = _KNOWN_YEARS.get(aid)
            if y is not None:
                by_year.setdefault(y, []).append(aid)
        for y, ids in by_year.items():
            fp = os.path.join(config.PAPERS_DIR, f"year={y}", "*.parquet")
            if not glob.glob(fp):
                continue
            ph = ",".join(["?"] * len(ids))
            try:
                con = duckdb.connect(":memory:")
                rows = con.execute(
                    f"SELECT arxiv_id, title, authors, abstract FROM read_parquet('{fp}') "
                    f"WHERE arxiv_id IN ({ph})", ids).fetchall()
                con.close()
            except Exception:
                continue
            for aid, title, authors, abstract in rows:
                found[str(aid)] = {
                    "title": title, "authors": authors,
                    "year": y, "abstract": abstract,
                }

    _META_CACHE.update(found)
    return {aid: _META_CACHE[aid] for aid in arxiv_ids if aid in _META_CACHE}


# ---------------- 概念组合聚合（AB 对 / A-C-B 桥接）----------------
def aggregate_pairs(results: list[dict], query_phrases: list[str]) -> dict:
    """从 RBO top 论文聚合概念组合，输出：
      pairs:   AB 对 = [ {a, b, weight}, ... ]  命中论文中直接共现的短语对
      bridges: A-C-B = [ {a, c, b, weight}, ... ]  A 与 B 未直接共现，但通过共同
               邻居 C 连接（1 度桥接）——潜在连接信号

    语义（用户 2026-08-13 核心设计 + A1 修复）：
      - AB 对 / A-C-B 桥接的统计用 **normalized 标注**（单复数/缩写已归并，
        规则全可控——统一分词后同一概念的不同形态不会分裂）
      - RBO 主检索仍用 raw（LLM 短语形态不可控，宽松匹配）
      - **A 侧约束（铁律）**：A 必须是查询短语之一（query_phrases 中精确或
        normalized 归并后命中），杜绝「不含任何查询短语的概念组合」
    与焦点图 hop0/1/2 结构呼应：hop0=查询词, hop1=AB, hop2=A-C-B。
    """
    # 从 normalized 反查 top 论文短语；缺失年份回退 raw 的 _phrases
    ids_year = [(r["arxiv_id"], r["year"]) for r in results]
    norm = _load_norm_phrases(ids_year)
    paper_phrases = []
    for r in results:
        ph = norm.get(r["arxiv_id"]) or r.get("_phrases") or []
        paper_phrases.append(ph)
    return _aggregate_from_papers(paper_phrases, query_phrases)


def _expand_query_a(query_phrases: list[str]) -> set[str]:
    """A 侧合法集合：查询短语 + normalized 归并后的等价形态。

    查询短语可能形态不匹配（如 LLM 给出 'nickelates'，normalized 标注是
    'nickelate'）。这里做单复数/缩写归并（与 normalized 标注同一规则），
    使 'nickelates' 能命中 'nickelate' 开头的短语。
    返回：set of (精确短语, normalized 前缀匹配短语)。
    """
    qset = set(query_phrases)
    # normalized 归并（与 number_normalize.csv 同一规则，最简实现）
    # 若查询短语以 's'/'es' 结尾，去掉后作为宽松候选
    for q in query_phrases:
        c = _strip_plural(q)
        if c:
            qset.add(c)
    return qset


def _a_is_query(a: str, qset: set[str]) -> bool:
    """A 侧约束判定：a 精确命中查询短语，或为查询短语的带 (s) 形态。"""
    if a in qset:
        return True
    # normalized 标注中的 '(s)' 形态：'black hole(s)' ↔ 查询 'black hole'
    for q in qset:
        if a == q + "(s)" or a == q + "(es)":
            return True
        if q == a + "(s)" or q == a + "(es)":
            return True
    return False


def _aggregate_from_papers(paper_phrases: list[list], query_phrases: list[str]) -> dict:
    """纯函数：给定每篇论文的短语列表，聚合 AB 对与 A-C-B 桥接。

    A1 修复：A 侧必须是查询短语之一（精确或形态归并命中）。
    非查询短语的候选（如旧版 'under compressive strain — normal state —
    oxygen octahedral' 中无任何查询短语）一律过滤。
    """
    qset = _expand_query_a(query_phrases)
    # 词共现计数（同一篇论文中两短语共现）
    cooc: dict[tuple[str, str], int] = {}
    for ph in paper_phrases:
        s = set(ph)
        for a in s:
            for b in s - {a}:
                key = tuple(sorted((a, b)))
                cooc[key] = cooc.get(key, 0) + 1

    # AB 对：A 必须是查询短语（A 侧约束）
    pairs = []
    seen_ab = set()
    for (a, b), w in sorted(cooc.items(), key=lambda x: -x[1]):
        if not _a_is_query(a, qset) and not _a_is_query(b, qset):
            continue   # A、B 都不是查询短语 → 过滤（A 侧约束）
        # 规范 A 侧：优先把查询短语放 A
        if _a_is_query(a, qset):
            A, B = a, b
        else:
            A, B = b, a
        key = (A, B)
        if key not in seen_ab:
            seen_ab.add(key)
            pairs.append({"a": A, "b": B, "weight": w})

    # A-C-B 桥接：A 是查询短语，C 中间概念，B 潜在新连接
    # A 与 C 共现、C 与 B 共现、A 与 B 未直接共现
    bridges = []
    seen_bridge = set()
    # 构建 C 的邻居：C → 与 C 共现的其他短语
    c_neighbors: dict[str, set] = {}
    for ph in paper_phrases:
        s = set(ph)
        for c in s:
            c_neighbors.setdefault(c, set()).update(s - {c})
    c_list = sorted(c_neighbors, key=lambda c: -len(c_neighbors[c]))[:30]  # 高频 C
    for ph in paper_phrases:
        s = set(ph)
        for a in s:
            if not _a_is_query(a, qset):
                continue   # A 侧约束：A 必须是查询短语
            for b in s - {a}:
                if _a_is_query(b, qset):
                    continue   # B 也是查询短语 → 这是查询短语间的关系，非桥接
                if (a, b) in cooc:          # A-B 已直接共现，跳过
                    continue
                for c in c_list:
                    if c in s or c == a or c == b:
                        continue
                    if b in c_neighbors.get(a, set()) and a in c_neighbors.get(c, set()):
                        key = tuple(sorted((a, c, b)))
                        if key not in seen_bridge:
                            seen_bridge.add(key)
                            bridges.append({"a": a, "c": c, "b": b, "weight": 1})
                        break
    return {"pairs": pairs[:50], "bridges": bridges[:50]}


# ---------------- CLI ----------------
def _parse_years(spec: str) -> list[int]:
    """解析年份范围：'1992' / '1991-1995' / '1991,1993,1995' / 'all'。

    'all'（或空）→ 返回 []（语义=全量，调用方 None→全部年份）。
    """
    spec = (spec or "").strip().lower()
    if not spec or spec in ("all", "*"):
        return []
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return sorted(set(out))


def main():
    ap = argparse.ArgumentParser(description="RBO 语义检索（raw 标注命中顺序）")
    ap.add_argument("--query", required=True,
                    help="有序短语，逗号分隔（第一个最重要）：'transmon qubit, surface code'")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--years", default=None,
                    help="限定年份：'1992' / '1991-1995'（默认全部年份）")
    ap.add_argument("--pretty", action="store_true", help="美化打印")
    ap.add_argument("--pairs", action="store_true",
                    help="附带概念组合聚合（AB 对 / A-C-B 桥接）")
    args = ap.parse_args()

    query = [q.strip().lower() for q in args.query.split(",") if q.strip()]
    if not query:
        raise SystemExit("错误: --query 为空")
    if len(query) > 5:
        print(f"警告: 查询短语 {len(query)} 个 > 5，截断前 5 个", file=sys.stderr)
        query = query[:5]
    print(f"查询（重要性降序）: {query}", file=sys.stderr)

    years = _parse_years(args.years) if args.years else None
    t0 = time.time()
    out = search_rbo(query, years=years, top_n=args.top, with_pairs=args.pairs)
    elapsed = time.time() - t0

    if args.pairs:
        results = out["results"]
        payload = {"query": query, "years": years or "all",
                   "n_results": len(results), "elapsed_s": round(elapsed, 2),
                   "results": results, "pairs": out["pairs"], "bridges": out["bridges"]}
    else:
        results = out
        payload = {"query": query, "years": years or "all",
                   "n_results": len(results), "elapsed_s": round(elapsed, 2),
                   "results": results}
    # 剥离内部 _phrases 字段（序列化冗余）
    for r in (payload["results"] if isinstance(payload["results"], list) else []):
        r.pop("_phrases", None)
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    print(f"\n[done] {len(results) if isinstance(results, list) else 0} 结果, {elapsed:.2f}s",
          file=sys.stderr)


if __name__ == "__main__":
    main()
