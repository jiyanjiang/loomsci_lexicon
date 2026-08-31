#!/usr/bin/env python3
"""G(AB) 有效电导核心计算库（/distance API + 批量实验共用）。

单对概念距离计算：A、B 的一跳邻居（top-k 共现词）作为中间节点 C，
构建 A/B/邻居 小网络 → max 电导归一 → 有效电阻 → G=1/R。

性能（2026-08-12 v2 优化）：
  - 形态检测：每概念只查一次（2025 年检测，全年复用）
  - 概念文档缓存：每概念每年只查一次（邻居 Counter 共享），跨对复用
  - AB 直连：单独查（每年 1 次，小查询）
单对 35 年 ≈ 3-4s（含形态一次性 + 概念缓存复用）。

用法：
  from scripts.g_ab_calc import compute_g_series, preload_variants
"""
import os
import sys
import numpy as np
import duckdb
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from phrase_forms import variants as _form_variants

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANN = config.ANN_NORM
# S6：最新标注年（数据驱动，消灭 2025 硬编码）
_LATEST_ANN_YEAR = config.latest_annotation_year() or 2025
TOP_K = 10          # 一跳邻居数（用户 2026-08-12 确认 K=10-15）
MIN_COOC = 1        # 弱共现过滤
NORM = "max"        # 边归一化

# ---------------- 词典短语匹配（A+B：前缀匹配 + 透明展示）----------------
# 加载 lexicon 词典，构建 前缀 → [短语] 索引，用于把用户输入泛词（如 attention）
# 匹配到词典里的真实学术短语（attention mechanism(s)）。
_LEXICON = None        # list of (term, n_tokens, freq)
_LEXICON_PREFIX = {}   # 首词小写 -> [(term, freq), ...]


def _load_lexicon():
    global _LEXICON, _LEXICON_PREFIX
    if _LEXICON is not None:
        return
    lex_path = os.path.join(ROOT, "data", "cumulative", "lexicon_2025.csv")
    _LEXICON = []
    _LEXICON_PREFIX = {}
    if not os.path.exists(lex_path):
        return
    import csv as _csv
    with open(lex_path, encoding="utf-8") as f:
        for r in _csv.DictReader(f):
            term = r["term"].strip().lower()
            try:
                freq = int(r["cumulative_freq"])
            except (ValueError, KeyError):
                freq = 0
            _LEXICON.append((term, freq))
            first = term.split(" ")[0]
            _LEXICON_PREFIX.setdefault(first, []).append((term, freq))
    # 每前缀按 freq 降序
    for k in _LEXICON_PREFIX:
        _LEXICON_PREFIX[k].sort(key=lambda x: -x[1])


def match_lexicon_phrases(concept):
    """输入概念词 → 匹配词典短语。返回 [(短语term, freq), ...]（最多5，按freq降序）。
    规则：输入词作为短语首词前缀（如 attention → attention mechanism）。
    无匹配返回 []。"""
    _load_lexicon()
    c = concept.strip().lower()
    hits = _LEXICON_PREFIX.get(c, [])
    return hits[:5]


def _resolve_variant_in_ann(con, fp, phrase):
    """在标注中确认短语形态（原形 / 加(s) / 加es / 加(es)），返回标注形态或 None。"""
    for v in _form_variants(phrase):
        n = con.execute(
            f"SELECT count(*) FROM read_parquet('{fp}') WHERE list_contains(phrases, ?)",
            [v]).fetchone()[0]
        if n > 0:
            return v
    return None


# ---------------- 概念形态缓存（跨年稳定，检测一次）----------------
_VARIANT_CACHE = {}   # concept -> variant(2025 形态)
_VARIANT_LOADED = False


def preload_variants(concepts):
    """预加载概念形态（在最新标注年检测一次，全年复用）。concepts: list[str]。"""
    global _VARIANT_LOADED
    if _VARIANT_LOADED:
        return
    fp = os.path.join(ANN, f"year={_LATEST_ANN_YEAR}", "part-0.parquet")
    if not os.path.exists(fp):
        return
    con = duckdb.connect(database=":memory:")
    try:
        for c in concepts:
            for v in _form_variants(c):
                n = con.execute(
                    f"SELECT count(*) FROM read_parquet('{fp}') WHERE list_contains(phrases, ?)",
                    [v]).fetchone()[0]
                if n > 0:
                    _VARIANT_CACHE[c] = v
                    break
    finally:
        con.close()
    _VARIANT_LOADED = True


# ---------------- LLM 临时概念映射表（2026-08-12 用户定稿，非破坏性）----------------
# 修正词典前缀匹配的歧义（如 hubble→hubble constant）。用完可删，生产流程固化。
_LLM_MAPPING = {}


def load_llm_mapping(path=None):
    """加载 LLM 映射表 {input: term}。path 缺省用 data/llm_mapping_20260812.json。"""
    global _LLM_MAPPING
    if not path:
        path = os.path.join(ROOT, "data", "llm_mapping_20260812.json")
    if not os.path.exists(path):
        return
    import json as _json
    with open(path, encoding="utf-8") as f:
        data = _json.load(f)
    _LLM_MAPPING = data.get("mapping", {})
    return _LLM_MAPPING


def _get_variant(con, fp, concept, year):
    """返回概念在某年的形态（缓存优先；未预载则现场检测）。

    A+B+LLM（2026-08-12）：解析优先级：
      1. 原形/(s)/es 直接匹配
      2. LLM 映射表（hubble→hubble constant）——修正多义词歧义
      3. 词典前缀匹配兜底（attention → attention mechanism）
    """
    if concept in _VARIANT_CACHE:
        return _VARIANT_CACHE[concept]
    for v in _form_variants(concept):
        n = con.execute(
            f"SELECT count(*) FROM read_parquet('{fp}') WHERE list_contains(phrases, ?)",
            [v]).fetchone()[0]
        if n > 0:
            _VARIANT_CACHE[concept] = v
            return v
    # LLM 映射表（用户定稿：修正多义词歧义）
    mapped = _LLM_MAPPING.get(concept)
    if mapped:
        v = _resolve_variant_in_ann(con, fp, mapped)
        if v:
            _VARIANT_CACHE[concept] = v
            _VARIANT_CACHE[concept + "__matched"] = mapped
            return v
    # 词典前缀匹配兜底（A）：输入泛词 → 找词典里以它开头的短语
    hits = match_lexicon_phrases(concept)
    for term, _freq in hits:
        v = _resolve_variant_in_ann(con, fp, term)
        if v:
            _VARIANT_CACHE[concept] = v
            _VARIANT_CACHE[concept + "__matched"] = term  # 记录实际匹配的词典短语
            return v
    _VARIANT_CACHE[concept] = None
    return None


# ---------------- 概念文档缓存（每概念每年查一次）----------------
_CONCEPT_DOC_CACHE = {}   # (concept, year) -> (variant, docfreq, Counter)
_CACHE_MAX = 5000


def _cache_trim():
    if len(_CONCEPT_DOC_CACHE) > _CACHE_MAX:
        _CONCEPT_DOC_CACHE.clear()


def _load_concept_docs(con, fp, concept, year):
    key = (concept, year)
    if key in _CONCEPT_DOC_CACHE:
        return _CONCEPT_DOC_CACHE[key]
    variant = _get_variant(con, fp, concept, year)
    if variant is None:
        _CONCEPT_DOC_CACHE[key] = (None, 0, Counter())
        _cache_trim()
        return _CONCEPT_DOC_CACHE[key]
    rows = con.execute(
        f"SELECT phrases FROM read_parquet('{fp}') WHERE list_contains(phrases, ?)",
        [variant]).fetchall()
    neigh = Counter()
    for (pl,) in rows:
        for w in set(pl):
            if w != variant:
                neigh[w] += 1
    out = (variant, len(rows), neigh)
    _CONCEPT_DOC_CACHE[key] = out
    _cache_trim()
    return out


def compute_g_series(A, B, years, top_k=TOP_K, min_cooc=MIN_COOC, norm=NORM):
    """计算 A、B 在逐年网络中的有效电导 G(AB,t)。返回 (years, G_list)。

    性能（2026-08-12 v3 根治）：**每对每年一次 `read_parquet`**——取含 A 或 B
    的文章一次加载，内存中同时算邻居 + 直连（避免 2-3 次独立 read_parquet）。
    单对 35 年应 <1s。

    关键（用户 2026-08-12）：AB 不直连 ≠ G=0。正确建模间接路径（A-C-B 及并联）：
      1. 每对每年一次查询（含 A 或 B 的文章）
      2. 内存统计：neigh_a（A 邻居）、neigh_b（B 邻居）、ab 直连
      3. 小网络 = {A, B} ∪ A top_k ∪ B top_k → max 归一 → 有效电阻 → G_raw=1/R
    """
    preload_variants([A, B])
    g_list = []
    con = duckdb.connect(database=":memory:")
    try:
        for year in years:
            fp = os.path.join(ANN, f"year={year}", "part-0.parquet")
            if not os.path.exists(fp):
                g_list.append(None)
                continue
            va, vb = _get_variant(con, fp, A, year), _get_variant(con, fp, B, year)
            if not va or not vb:
                g_list.append(None)
                continue
            # 一次查询：含 A 或 B 的文章
            rows = con.execute(
                f"SELECT phrases FROM read_parquet('{fp}') "
                f"WHERE list_contains(phrases, ?) OR list_contains(phrases, ?)",
                [va, vb]).fetchall()
            neigh_a, neigh_b, ab = Counter(), Counter(), 0
            for (pl,) in rows:
                ps = set(pl)
                has_a, has_b = va in ps, vb in ps
                if has_a and has_b:
                    ab += 1
                if has_a:
                    for w in ps:
                        if w != va and w != vb:
                            neigh_a[w] += 1
                if has_b:
                    for w in ps:
                        if w != va and w != vb:
                            neigh_b[w] += 1
            na = neigh_a.most_common(top_k)
            nb = neigh_b.most_common(top_k)
            nodes = [va, vb] + [w for w, _ in na if w not in (va, vb)] \
                              + [w for w, _ in nb if w not in (va, vb)]
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
            base = mat.max()
            if base <= 0:
                g_list.append(0.0)
                continue
            cond = mat / base
            deg = cond.sum(axis=1)
            L = np.diag(deg) - cond
            try:
                Lpinv = np.linalg.pinv(L)
            except np.linalg.LinAlgError:
                g_list.append(None)
                continue
            r = Lpinv[0, 0] + Lpinv[1, 1] - 2 * Lpinv[0, 1]
            g_list.append(round(1.0 / r, 4) if r > 1e-6 else 0.0)
    finally:
        con.close()
    return years, g_list


def clear_cache(failed_only: bool = False):
    """清空概念缓存（S3，2026-08-13）。

    failed_only=True：只清理**失败条目**（解析为 None 的形态/文档缓存），
    成功条目保留供跨请求复用——web 长驻进程下每次请求不再全量冷算。
    防污染语义保留：旧失败缓存（variant=None）不再残留误导新概念解析。
    """
    global _VARIANT_LOADED
    if failed_only:
        for k in [k for k, v in _VARIANT_CACHE.items() if v is None]:
            del _VARIANT_CACHE[k]
        for k in [k for k, v in _CONCEPT_DOC_CACHE.items() if v[0] is None]:
            del _CONCEPT_DOC_CACHE[k]
        return
    _CONCEPT_DOC_CACHE.clear()
    _VARIANT_LOADED = False
    _VARIANT_CACHE.clear()


def resolve_concept_with_match(A, B):
    """解析 A、B 实际使用的学术短语（含词典匹配信息），供 web 展示复核。

    返回 (variant_a, matched_a, variant_b, matched_b)：
      variant_* = 标注中的实际形态（如 'attention mechanism(s)'）或 None
      matched_*  = 词典匹配到的短语 term（如 'attention mechanism'）或 None
    """
    preload_variants([A, B])
    con = duckdb.connect(database=":memory:")
    fp_latest = os.path.join(ANN, f"year={_LATEST_ANN_YEAR}", "part-0.parquet")
    try:
        va = _get_variant(con, fp_latest, A, _LATEST_ANN_YEAR)
        vb = _get_variant(con, fp_latest, B, _LATEST_ANN_YEAR)
    finally:
        con.close()
    ma = _VARIANT_CACHE.get(A + "__matched")
    mb = _VARIANT_CACHE.get(B + "__matched")
    return va, ma, vb, mb
