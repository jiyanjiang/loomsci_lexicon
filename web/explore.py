#!/usr/bin/env python3
# =====================================================================
# PaperExplore 3.0 · 独立开发（FocusView 2.0 已封闭，勿改冻结文件）
# 依据：docs/focus_view_v3_plan.md §3.3
# =====================================================================
"""PaperExplore: 焦点图 + 右侧预印本互动面板（2025 最小测试版）。

S2（本步）：后端 API /focus/papers
  - 点节点 → 当年含该短语的预印本（FTS/B25 降序，30/页）
  - 点边   → 当年同时含两短语的预印本（AND，FTS 降序，30/页）
后续：S3 左图右栏布局 / S4 交互接线 / S5 首页入口

数据链路：
  annotation parquet（当年短语 → arxiv_id，严格限定年份）
    → arxiv_id 集合
    → FTS papers 库（match_bm25 排序，IN 集合内，30/页）

用法：python web/explore.py [--port 5011]
"""
import argparse
import os
import re
import sys
import time
import threading
import duckdb
from flask import Flask, jsonify, request, render_template, send_from_directory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import config
ANN_BASE = config.ANN_NORM
FTS_DB = config.FTS_DB                                    # 可空：FTS 检索关闭
PIPELINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VISUAL_DIR = config.VISUAL_FULL_DIR                       # FocusView 全年图产物
FOCUS_V2 = os.path.join(config.VISUAL_DIR, "focus")       # FocusView 焦点图产物
FOCUS_V3 = os.path.join(config.VISUAL_DIR, "focus_v3")    # PaperExplore 焦点图产物
# S6：年份边界数据驱动（从标注目录实测推导，消灭 1991/2025 硬编码）
_ANN_YEARS = config.annotation_years() or [1991, 2025]    # 无标注时保守回退
YEAR_MIN, YEAR_MAX = _ANN_YEARS[0], _ANN_YEARS[-1]
LATEST_ANN_YEAR = YEAR_MAX                                # 最新完整标注年
YEAR_SEL_MIN = 2000    # 前端年份下拉范围（用户确认 2000-{YEAR_MAX}，缺省 YEAR_MAX）
PER_DEFAULT, PER_MAX = 30, 50

app = Flask(__name__)

# 线程本地 FTS 连接（只读，进程内复用；duckdb 连接非线程安全）
_fts_local = threading.local()


def _get_fts_con():
    """FTS 连接；config 未配置 fts_db 或库不存在时返回 None（检索功能关闭）。"""
    if not FTS_DB or not os.path.exists(FTS_DB):
        return None
    con = getattr(_fts_local, "fts_con", None)
    if con is None:
        con = duckdb.connect(FTS_DB, read_only=True)
        _fts_local.fts_con = con
    return con


_fts_index_range: tuple | None = None


def _fts_index_years() -> tuple | None:
    """当前 FTS 索引的年份范围 (min, max)。查询一次缓存。"""
    global _fts_index_range
    if _fts_index_range is not None:
        return _fts_index_range
    con = _get_fts_con()
    if con is None:
        _fts_index_range = None
        return None
    try:
        row = con.execute("SELECT min(year), max(year) FROM papers").fetchone()
        _fts_index_range = (row[0], row[1]) if row and row[0] is not None else None
    except Exception:
        _fts_index_range = None
    return _fts_index_range


def _clean_fts_query(phrase):
    """FTS 查询清洗：annotation 短语含 '(s)' 复数标记，BM25 tokenizer 会把
    括号当分隔符导致 's' 被当独立 token。统一去 '(s)' 尾部 + 符号转空格。"""
    p = re.sub(r"\(s\)$", "", phrase.strip())      # surface code(s) -> surface code
    p = re.sub(r"[^0-9a-zA-Z \-]", " ", p)          # 其他符号 -> 空格
    p = " ".join(p.split())
    return p or phrase.strip()


def _annotation_ids(year, phrase, phrase2=None):
    """annotation 当年反查 arxiv_id 集合（节点=1词 / 边=2词 AND）。
    返回 (ids: list[str], total: int)。"""
    fp = os.path.join(ANN_BASE, f"year={year}", "part-0.parquet")
    if not os.path.exists(fp):
        return [], 0
    con = duckdb.connect(database=":memory:")
    try:
        if phrase2:
            rows = con.execute(
                f"SELECT arxiv_id FROM read_parquet('{fp}') "
                f"WHERE list_contains(phrases, ?) AND list_contains(phrases, ?)",
                [phrase, phrase2]).fetchall()
        else:
            rows = con.execute(
                f"SELECT arxiv_id FROM read_parquet('{fp}') "
                f"WHERE list_contains(phrases, ?)",
                [phrase]).fetchall()
        ids = [r[0] for r in rows]
        return ids, len(ids)
    finally:
        con.close()


def _fts_rank(ids, query, page, per):
    """arxiv_id 集合内按 FTS(BM25) 分数降序分页。返回 (items, 本页命中数)。"""
    if not ids:
        return [], 0
    con = _get_fts_con()
    if con is None:
        return [], 0
    offset = (page - 1) * per
    rows = con.execute(
        """SELECT arxiv_id, title, authors, submission_date, abstract,
                  fts_main_papers.match_bm25(paper_id, ?) AS score
           FROM papers
           WHERE list_contains(?, arxiv_id)
             AND fts_main_papers.match_bm25(paper_id, ?) IS NOT NULL
           ORDER BY score DESC
           LIMIT ? OFFSET ?""",
        [query, ids, query, per, offset]).fetchall()
    items = [{
        "arxiv_id": r[0], "title": r[1], "authors": r[2],
        "date": str(r[3]) if r[3] is not None else "",
        "abstract": r[4], "score": round(r[5], 4),
    } for r in rows]
    return items, len(rows)


def _fts_global(query, page, per):
    """scope=all：全局 FTS（不反查、不限定年份，含 2026）。返回 (items, total)。"""
    con = _get_fts_con()
    if con is None:
        return [], 0
    offset = (page - 1) * per
    total = con.execute(
        "SELECT count(*) FROM papers "
        "WHERE fts_main_papers.match_bm25(paper_id, ?) IS NOT NULL",
        [query]).fetchone()[0]
    rows = con.execute(
        """SELECT arxiv_id, title, authors, submission_date, abstract,
                  fts_main_papers.match_bm25(paper_id, ?) AS score
           FROM papers
           WHERE fts_main_papers.match_bm25(paper_id, ?) IS NOT NULL
           ORDER BY score DESC
           LIMIT ? OFFSET ?""",
        [query, query, per, offset]).fetchall()
    items = [{
        "arxiv_id": r[0], "title": r[1], "authors": r[2],
        "date": str(r[3]) if r[3] is not None else "",
        "abstract": r[4], "score": round(r[5], 4),
    } for r in rows]
    return items, total


@app.route("/static/chartjs/<path:fp>")
def serve_chartjs(fp):
    """服务本地 Chart.js（避免 CDN 依赖导致图不渲染）。"""
    return send_from_directory(os.path.join(ROOT, "lib", "chartjs"), fp)


@app.route("/explore")
def explore():
    """PaperExplore 页面：左图右栏（S3 布局）。"""
    return render_template("explore.html", year_min=YEAR_MIN, year_max=YEAR_MAX,
                           year_sel_min=YEAR_SEL_MIN, latest_year=LATEST_ANN_YEAR)


# ---------------------------------------------------------------------------
# /distance：概念距离 G(AB) 随时间演化（前瞻验证工具）
# ---------------------------------------------------------------------------
@app.route("/distance")
def distance_page():
    """概念距离页面：输入 A/B/年份 → Chart.js 交互折线。"""
    return render_template("distance.html", year_min=YEAR_MIN, year_max=YEAR_MAX,
                           latest_year=LATEST_ANN_YEAR)


# 二分领域归一：概念 → cs/非cs（arXiv 首要分类多数票）
def _concept_binary_cs(concept):
    """概念 → True(cs)/False(non-cs)/None(未知)。用 2025 年文章首要分类多数票。"""
    try:
        import duckdb as _db
        cat_path = os.path.join(ROOT, "data", "category_map.duckdb")
        if not os.path.exists(cat_path):
            return None
        from phrase_forms import variants as _form_variants
        cat = _db.connect(cat_path, read_only=True)
        ann_latest = os.path.join(ANN_BASE, f"year={LATEST_ANN_YEAR}", "part-0.parquet")
        variant = None
        for v in _form_variants(concept):
            n = cat.execute(
                f"SELECT count(*) FROM read_parquet('{ann_latest}') WHERE list_contains(phrases, ?)",
                [v]).fetchone()[0]
            if n > 0:
                variant = v
                break
        if not variant:
            cat.close()
            return None
        dist = cat.execute(f"""
            SELECT pc.domain, count(*) FROM read_parquet('{ann25}') a
            JOIN paper_category pc USING (arxiv_id)
            WHERE list_contains(a.phrases, ?) GROUP BY pc.domain
        """, [variant]).fetchall()
        cat.close()
        n_cs = sum(int(n) for d, n in dist if d in ("cs", "eess"))
        n_oth = sum(int(n) for d, n in dist if d not in ("cs", "eess"))
        return bool(n_cs > n_oth)
    except Exception:
        return None


def _pair_binary_group(A, B):
    """概念对 → 'cs' / 'non-cs' / None（策展：联合命中率选组）。

    用户 2026-08-12 定稿：按 A、B 在 cs 文章 vs 非cs 文章的**共现命中数**选组，
    选数据更多的那组——跨域对也归组，不丢数据。
    形态解析复用 g_ab_calc 的词典匹配（attention → attention mechanism(s)）。
    返回 (group, cs_hits, non_hits)。"""
    try:
        import duckdb as _db
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        from g_ab_calc import resolve_concept_with_match
        va, ma, vb, mb = resolve_concept_with_match(A, B)
        if not va or not vb:
            return None, 0, 0
        cat_path = os.path.join(ROOT, "data", "category_map.duckdb")
        if not os.path.exists(cat_path):
            return None, 0, 0
        cat = _db.connect(cat_path, read_only=True)
        ann_latest = os.path.join(ANN_BASE, f"year={LATEST_ANN_YEAR}", "part-0.parquet")
        cs_hits = cat.execute(f"""
            SELECT count(*) FROM read_parquet('{ann_latest}') a
            JOIN paper_category pc USING (arxiv_id)
            WHERE list_contains(a.phrases, ?) AND list_contains(a.phrases, ?)
              AND (pc.domain = 'cs' OR pc.domain = 'eess')
        """, [va, vb]).fetchone()[0]
        non_hits = cat.execute(f"""
            SELECT count(*) FROM read_parquet('{ann_latest}') a
            JOIN paper_category pc USING (arxiv_id)
            WHERE list_contains(a.phrases, ?) AND list_contains(a.phrases, ?)
              AND pc.domain != 'cs' AND pc.domain != 'eess'
        """, [va, vb]).fetchone()[0]
        cat.close()
        cs_hits, non_hits = int(cs_hits), int(non_hits)
        if cs_hits == 0 and non_hits == 0:
            return None, 0, 0
        if cs_hits >= non_hits:
            return "cs", cs_hits, non_hits
        return "non-cs", cs_hits, non_hits
    except Exception:
        return None, 0, 0


@app.route("/distance/api")
def distance_api():
    """计算 G(AB,t)，与对标实验一致：
      观察窗口 3 年（默认 2015-2017，可调 window_begin）+ 二分(cs/非cs)结果归一 + Youden 阈值。
    返回 {years, G, R, A, B, threshold, group, window, ...}。"""
    A = (request.args.get("A") or "").strip()
    B = (request.args.get("B") or "").strip()
    if not A or not B:
        return jsonify({"ok": False, "error": "请输入两个概念词"}), 400
    if A == B:
        return jsonify({"ok": False, "error": "A 与 B 不能相同"}), 400
    try:
        window_begin = int(request.args.get("window_begin", 2015))
        window_years = int(request.args.get("window_years", 3))
    except ValueError:
        return jsonify({"ok": False, "error": "window 参数必须为整数"}), 400
    last_begin = YEAR_MAX - 1   # 窗口起始年上限 = 数据最新年-1（S6 数据驱动）
    if not (YEAR_MIN <= window_begin <= last_begin and window_years in (1, 2, 3)):
        return jsonify({"ok": False,
                        "error": f"窗口：起始 {YEAR_MIN}-{last_begin}，年数 1/2/3"}), 400
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from g_ab_calc import compute_g_series, resolve_concept_with_match, clear_cache
    # 只清理失败缓存条目（S3，2026-08-13）：成功条目跨请求复用，防止旧失败污染
    clear_cache(failed_only=True)
    # 解析实际学术短语（A+B：词典匹配 + 透明展示复核）
    va, ma, vb, mb = resolve_concept_with_match(A, B)
    # 观察窗口 = [begin, begin+window_years-1]；曲线从窗口起始年显示（用户 2026-08-12）
    window = list(range(window_begin, min(window_begin + window_years, YEAR_MAX + 1)))
    years = list(range(window_begin, YEAR_MAX + 1))
    # S3（2026-08-13）：一次算全量序列，窗口段从全量切片（window 是 years 前缀）
    _, g_full = compute_g_series(A, B, years)
    g_raw = g_full[:len(window)]
    # 二分归一基准（策展：联合命中率选组）
    group, cs_hits, non_hits = _pair_binary_group(A, B)
    # 二分 G_max 参考（cs/非cs 各自逐年最强=1，用户定稿）
    bm_path = os.path.join(ROOT, "data", "binary_gmax_20260812.json")
    gmax_ref = {"cs": {}, "non-cs": {}}
    if os.path.exists(bm_path):
        import json as _json
        with open(bm_path, encoding="utf-8") as f:
            gmax_ref = _json.load(f).get("G_max", gmax_ref)
    g_norm_series = []
    for y, v in zip(years, g_full):
        if v is None or v <= 0 or group is None:
            g_norm_series.append(None)
            continue
        gm = gmax_ref.get(group, {}).get(str(y))
        if not gm:
            g_norm_series.append(None)
        else:
            g_norm_series.append(round(min(v / float(gm), 1.0), 4))  # 组内最强=1，钳≤1
    # g_last_raw：观察窗口末 G_raw
    fin_raw = [v for v in g_raw if v is not None and v > 0]
    g_last_raw = fin_raw[-1] if fin_raw else 0.0
    # 观察窗口末年的归一值（预警依据）
    fin = [v for v in g_norm_series if v is not None]
    g_norm_last = fin[-1] if fin else 0.0
    # 数据驱动阈值（对标实验：二分 cs 更强；用 0.5 = 达到组内最强一半）
    threshold = 0.5
    alert = bool(g_norm_last >= threshold)
    return jsonify({
        "ok": True, "A": A, "B": B,
        "matched_terms": {
            "A": {"input": A, "variant": va, "dict_term": ma},
            "B": {"input": B, "variant": vb, "dict_term": mb},
        },
        "years": years, "G": g_norm_series,
        "R": [None if v is None else float(round(1 / v, 4)) for v in g_norm_series],
        "window": window, "window_begin": window_begin, "window_years": window_years,
        "group": group, "cs_hits": cs_hits, "non_hits": non_hits,
        "g_last_raw": float(round(g_last_raw, 4)),
        "g_norm": float(g_norm_last), "threshold": threshold, "alert": alert,
        "normalized": True,
        "note": "二分归一=cs/非cs各自最强=1（用户定稿）；G_max参考=data/binary_gmax_20260812.json",
    })


@app.route("/focus_v3/<path:fp>")
def serve_focus_v3(fp):
    """服务 V3 焦点图产物（data/visual/focus_v3/）。"""
    return send_from_directory(FOCUS_V3, fp)


# 焦点图点击注入 JS（S4）：iframe 内 network.on('click') → postMessage 传父页面。
# 节点：params.nodes[0] = 节点 id = 短语（pyvis id=label）；边：edges.get(eid) 取 from/to。
FOCUS_CLICK_JS = """
<script type="text/javascript">
(function() {
    if (window.self === window.top) return;  // 只在 iframe 内激活
    var tries = 0;
    function bindClick() {
        var net = window.__net;
        if (!net || !net.on) { if (tries < 300) { tries++; setTimeout(bindClick, 100); } return; }
        net.on('click', function(params) {
            var msg = { type: 'focus-click', kind: 'blank' };
            if (params.nodes && params.nodes.length) {
                msg.kind = 'node'; msg.phrase = params.nodes[0];
            } else if (params.edges && params.edges.length) {
                var eid = params.edges[0];
                var e = null;
                try { e = net.body.data.edges.get(eid); } catch (err) {}
                if (e && e.from && e.to) { msg.kind = 'edge'; msg.phrase = e.from; msg.phrase2 = e.to; }
                else { return; }
            }
            try { window.parent.postMessage(msg, '*'); } catch (err) {}
        });
    }
    bindClick();
})();
</script>
"""


def _inject_focus_click(html):
    """在焦点图 html 的 </body> 前注入点击 JS（重复注入时幂等）。"""
    if "focus-click" in html:
        return html  # 已注入
    if "</body>" in html:
        return html.replace("</body>", FOCUS_CLICK_JS + "</body>", 1)
    return html + FOCUS_CLICK_JS


@app.route("/explore/focus", methods=["POST"])
def explore_focus():
    """生成焦点图三图（复用 FocusView 2.0 export_focus_series），注入点击 JS，复制到 focus_v3/。"""
    data = request.get_json(silent=True) or {}
    try:
        year = int(data.get("year", LATEST_ANN_YEAR))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "year 必须为整数"}), 400
    if not (YEAR_MIN <= year <= YEAR_MAX):
        return jsonify({"ok": False, "error": f"year 超出范围 {YEAR_MIN}-{YEAR_MAX}"}), 400
    focus_str = (data.get("focus", "") or "").strip()
    if not focus_str:
        return jsonify({"ok": False, "error": "请输入焦点词"}), 400
    words = [w.strip() for w in focus_str.split(",") if w.strip()]
    if len(words) > 5:
        return jsonify({"ok": False, "error": "焦点词最多 5 个"}), 400
    os.makedirs(FOCUS_V3, exist_ok=True)
    _SCRIPTS = os.path.join(PIPELINE, "scripts")
    if _SCRIPTS not in sys.path:
        sys.path.insert(0, _SCRIPTS)
    try:
        import visualize as V
    except ImportError as e:
        return jsonify({"ok": False, "error": f"visualize 模块导入失败: {e}"}), 500
    V.ANN = ANN_BASE
    try:
        paths = V.export_focus_series(year, ", ".join(words), min_edge=20, top_edges=1500,
                                      ui_name="standard", theme_name="starry")
    except Exception as e:
        return jsonify({"ok": False, "error": f"焦点图生成异常: {e}"}), 500
    if not paths:
        return jsonify({"ok": False, "error": "焦点子图为空（焦点词可能无效或无篇目）"}), 500
    files = {}
    for mode, src in paths.items():
        base_fn = os.path.basename(src)
        dst = os.path.join(FOCUS_V3, base_fn)
        if os.path.abspath(src) != os.path.abspath(dst):
            with open(src, "r", encoding="utf-8") as fh:
                html = fh.read()
            html = _inject_focus_click(html)
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write(html)
        files[mode] = base_fn
    return jsonify({"ok": True, "year": year, "files": files})


@app.route("/focus/papers")
def focus_papers():
    scope = (request.args.get("scope") or "this_year").strip()
    if scope not in ("this_year", "all"):
        return jsonify({"ok": False, "error": "scope 必须为 this_year 或 all"}), 400
    try:
        year = int(request.args.get("year", LATEST_ANN_YEAR))
    except ValueError:
        return jsonify({"ok": False, "error": "year 必须为整数"}), 400
    if not (YEAR_MIN <= year <= YEAR_MAX):
        return jsonify({"ok": False, "error": f"year 超出范围 {YEAR_MIN}-{YEAR_MAX}"}), 400
    phrase = (request.args.get("phrase") or "").strip()
    phrase2 = (request.args.get("phrase2") or "").strip() or None
    if not phrase:
        return jsonify({"ok": False, "error": "缺少 phrase"}), 400
    try:
        page = max(1, int(request.args.get("page", 1)))
        per = min(PER_MAX, max(1, int(request.args.get("per", PER_DEFAULT))))
    except ValueError:
        return jsonify({"ok": False, "error": "page/per 必须为整数"}), 400

    raw_query = f"{phrase} {phrase2}" if phrase2 else phrase
    query = _clean_fts_query(raw_query)

    if scope == "all":
        items, total = _fts_global(query, page, per)
        pages = (total + per - 1) // per
        return jsonify({"ok": True, "scope": "all", "year": year,
                        "query": {"phrase": phrase, "phrase2": phrase2},
                        "total": total, "page": page, "per": per, "pages": pages,
                        "items": items})

    ids, total = _annotation_ids(year, phrase, phrase2)
    if not ids:
        return jsonify({"ok": True, "scope": "this_year", "year": year,
                        "query": {"phrase": phrase, "phrase2": phrase2},
                        "total": 0, "page": page, "per": per, "pages": 0, "items": []})
    items, hit = _fts_rank(ids, query, page, per)
    pages = (total + per - 1) // per
    return jsonify({"ok": True, "scope": "this_year", "year": year,
                    "query": {"phrase": phrase, "phrase2": phrase2},
                    "total": total, "page": page, "per": per, "pages": pages,
                    "items": items})


# ---------------------------------------------------------------------------
# RBO 语义检索（raw 标注命中顺序）：与 FTS(BM25) 互补
#   NL → LLM 编排 ≤5 有序学术短语（跳过判断）→ 倒排预筛 + RBO 精排 → 降序
# ---------------------------------------------------------------------------
@app.route("/cache/llm", methods=["GET"])
def cache_llm_list():
    """B2：查看 LLM 编排缓存条目列表（GET /cache/llm）。"""
    from orchestrate_query import cache_entries
    entries = cache_entries()
    return jsonify({"ok": True, "count": len(entries), "entries": entries})


@app.route("/cache/llm", methods=["DELETE"])
def cache_llm_clear():
    """B2：清除全部 LLM 编排缓存（DELETE /cache/llm）。"""
    from orchestrate_query import cache_clear
    n = cache_clear()
    return jsonify({"ok": True, "cleared": n})


@app.route("/cache/llm/<path:key>", methods=["DELETE"])
def cache_llm_delete(key):
    """B2：删除指定缓存条目（DELETE /cache/llm/{key}）。key=规范化查询串。"""
    from orchestrate_query import cache_remove
    removed = cache_remove(key)
    return jsonify({"ok": True, "key": key, "removed": removed})


@app.route("/cache/llm/stats", methods=["GET"])
def cache_llm_stats():
    """D2：缓存命中率监控（GET /cache/llm/stats）。"""
    from orchestrate_query import cache_stats
    stats = cache_stats()
    return jsonify({"ok": True, "stats": stats})


@app.route("/search/rbo")
def search_rbo_api():
    """RBO 语义检索 API。

    GET /search/rbo?query=<NL 或 逗号分隔短语>&top=20&years=1991-1995
    返回：{ok, query, phrases(编排后), mode(skip/llm/cache), years, n_results, results}
    results[i] = {arxiv_id, year, rbo, rbo_norm, n_hits, hits[]}
    """
    import search_rbo
    from orchestrate_query import orchestrate_query

    query = (request.args.get("query") or "").strip()
    if not query:
        return jsonify({"ok": False, "error": "缺少 query"}), 400
    try:
        top = min(50, max(1, int(request.args.get("top", 20))))
    except ValueError:
        return jsonify({"ok": False, "error": "top 必须为整数"}), 400
    years = None
    ys = (request.args.get("years") or "").strip()
    if ys:
        try:
            from search_rbo import _parse_years
            years = _parse_years(ys) or None   # 'all'/空 → None（全量）
        except Exception:
            return jsonify({"ok": False, "error": f"years 解析失败: {ys}"}), 400

    t0 = time.time()
    phrases, mode = orchestrate_query(query)
    llm_ms = int((time.time() - t0) * 1000)
    if not phrases:
        return jsonify({"ok": False, "error": f"编排失败（{mode}）", "mode": mode}), 400
    # B2：缓存命中标记（mode=='cache' → 本次编排命中缓存，跳过 LLM 调用）
    cache_hit = (mode == "cache")
    cache_created_at = None
    if cache_hit:
        try:
            from orchestrate_query import cache_entries
            for e in cache_entries():
                if e["query"] == query.strip():
                    ts = e.get("ts") or 0
                    if ts:
                        import datetime as _dt
                        cache_created_at = _dt.datetime.fromtimestamp(
                            ts).strftime("%Y-%m-%d %H:%M:%S")
                    break
        except Exception:
            pass
    # B2：短语命中状态（exact/loose/missing，诚实告知）
    phrase_status = [search_rbo.phrase_match_status(p, years=years) for p in phrases]
    # RBO 档纯论文列表（概念组合已移到 /search/recommend，2026-08-13 定稿）
    results = search_rbo.search_rbo(phrases, years=years, top_n=top)
    total_ms = int((time.time() - t0) * 1000)
    n_missing = sum(1 for s in phrase_status if s["status"] == "missing")
    n_loose = sum(1 for s in phrase_status if s["status"] == "loose")
    warnings = []
    if n_missing > 0:
        warnings.append({
            "code": "PHRASE_MISSING",
            "level": "warning",
            "message": f"{n_missing} 个查询短语未在学术词典中精确命中（可能无精确结果，结果为语义邻近匹配）",
            "detail": [s for s in phrase_status if s["status"] == "missing"],
        })
    if n_loose > 0:
        warnings.append({
            "code": "PHRASE_LOOSE",
            "level": "info",
            "message": f"{n_loose} 个查询短语为形态归并命中（如单复数）",
            "detail": [s for s in phrase_status if s["status"] == "loose"],
        })
    idx_range = _fts_index_years()
    idx_note = (f"当前 FTS 索引（{idx_range[0]}-{idx_range[1]} 年）"
                if idx_range else "当前 FTS 索引不可用")
    return jsonify({
        "ok": True, "query": query, "phrases": phrases, "mode": mode,
        "cache_hit": cache_hit, "cache_created_at": cache_created_at,
        "phrase_status": phrase_status,
        "years": years or "all", "n_results": len(results), "results": results,
        "meta": {
            "llm_ms": llm_ms, "total_ms": total_ms,
            "n_missing": n_missing, "n_loose": n_loose,
        },
        "warnings": warnings,
        "index_note": idx_note, "index_years": list(idx_range) if idx_range else None,
    })


@app.route("/search/recommend")
def search_recommend_api():
    """焦点推荐：只出概念组合（AB 对 + AC-CB 桥接），与 RBO 脱钩。

    用户定稿（2026-08-13）：
      - RBO/FTS 只做论文检索（纯列表 + 得分）
      - 焦点推荐只出概念组合，不输出论文列表（需论文时切到 FTS/RBO）
      - 概念组合聚合不依赖 RBO 排序（独立程序 search_recommend.py）

    GET /search/recommend?query=<NL 或 逗号分隔短语>&top=50&years=...
    返回：{ok, phrases, mode, phrase_status, pairs, bridges, n_papers, meta}
    """
    import search_recommend
    import search_rbo
    from orchestrate_query import orchestrate_query

    query = (request.args.get("query") or "").strip()
    if not query:
        return jsonify({"ok": False, "error": "缺少 query"}), 400
    years = None
    ys = (request.args.get("years") or "").strip()
    if ys:
        try:
            from search_rbo import _parse_years
            years = _parse_years(ys) or None   # 'all'/空 → None（全量）
        except Exception:
            return jsonify({"ok": False, "error": f"years 解析失败: {ys}"}), 400
    try:
        top = min(100, max(1, int(request.args.get("top", 50))))
    except ValueError:
        return jsonify({"ok": False, "error": "top 必须为整数"}), 400

    t0 = time.time()
    phrases, mode = orchestrate_query(query)
    llm_ms = int((time.time() - t0) * 1000)
    if not phrases:
        return jsonify({"ok": False, "error": f"编排失败（{mode}）", "mode": mode}), 400
    cache_hit = (mode == "cache")
    phrase_status = [search_rbo.phrase_match_status(p, years=years) for p in phrases]
    out = search_recommend.recommend(phrases, years=years, top_n=top)
    total_ms = int((time.time() - t0) * 1000)
    n_missing = sum(1 for s in phrase_status if s["status"] == "missing")
    warnings = []
    if n_missing > 0:
        warnings.append({
            "code": "PHRASE_MISSING", "level": "warning",
            "message": f"{n_missing} 个查询短语未在数据中精确命中（概念组合基于语义邻近）",
            "detail": [s for s in phrase_status if s["status"] == "missing"],
        })
    return jsonify({
        "ok": True, "query": query, "phrases": phrases, "mode": mode,
        "cache_hit": cache_hit,
        "phrase_status": phrase_status,
        "years": years or "all",
        "pairs": out["pairs"], "bridges": out["bridges"],
        "n_papers": out["n_papers"],
        "meta": {"llm_ms": llm_ms, "total_ms": total_ms, "n_missing": n_missing},
        "warnings": warnings,
    })


@app.route("/search/fts")
def search_fts_api():
    """FTS 主检索（缺省）：NL → LLM 编排短语 → BM25 检索。

    用户定位（2026-08-13）：FTS 是主搜索（结果质量最好），RBO 是可选辅助。
    统一流程：LLM --> FTS（缺省）--> 可选切换 RBO。

    GET /search/fts?query=<NL 或 逗号分隔短语>&top=20&years=1991-1995
    返回：{ok, query, phrases, mode, engine, n_results, results(统一schema)}
    """
    import search_rbo
    from orchestrate_query import orchestrate_query

    query = (request.args.get("query") or "").strip()
    if not query:
        return jsonify({"ok": False, "error": "缺少 query"}), 400
    try:
        top = min(100, max(1, int(request.args.get("top", 30))))
    except ValueError:
        return jsonify({"ok": False, "error": "top 必须为整数"}), 400
    try:
        page = max(1, int(request.args.get("page", 1)))
        per = min(30, max(1, int(request.args.get("per", 10))))
    except ValueError:
        page, per = 1, 10
    year = request.args.get("year", str(LATEST_ANN_YEAR))
    year_all = (str(year).strip().lower() in ("", "all"))
    try:
        year = int(year) if not year_all else None
    except ValueError:
        year_all, year = True, None

    phrases, mode = orchestrate_query(query)
    if not phrases:
        return jsonify({"ok": False, "error": f"编排失败（{mode}）", "mode": mode}), 400
    # C1：短语命中状态（exact/loose/missing，诚实告知）
    phrase_status = [search_rbo.phrase_match_status(p) for p in phrases]

    # 对每个编排短语做 FTS 检索，合并去重（取全部命中，前端分页）
    fts_con = _get_fts_con()
    if fts_con is None:
        return jsonify({"ok": False, "error": "FTS 库未配置（config.yaml fts_db 为空）",
                        "phrases": phrases, "mode": mode}), 400
    seen: dict[str, dict] = {}
    for phrase in phrases:
        q = _clean_fts_query(phrase)
        try:
            if year_all:
                # All Years：不加年份过滤（全索引检索）
                rows = fts_con.execute(
                    """SELECT arxiv_id, title, authors, submission_date, abstract,
                              fts_main_papers.match_bm25(paper_id, ?) AS score
                       FROM papers
                       WHERE fts_main_papers.match_bm25(paper_id, ?) IS NOT NULL
                       ORDER BY score DESC LIMIT 200""",
                    [q, q]).fetchall()
            else:
                rows = fts_con.execute(
                    """SELECT arxiv_id, title, authors, submission_date, abstract,
                              fts_main_papers.match_bm25(paper_id, ?) AS score
                       FROM papers
                       WHERE fts_main_papers.match_bm25(paper_id, ?) IS NOT NULL
                         AND year = ?
                       ORDER BY score DESC LIMIT 200""",
                    [q, q, year]).fetchall()
        except Exception:
            continue
        for aid, title, authors, sdate, abstract, score in rows:
            if aid not in seen or score > seen[aid]["score"]:
                # year_all 时从 submission_date 提取真实年份（All Years 展示各年论文）
                y = year
                if y is None:
                    try:
                        y = int(str(sdate)[:4]) if sdate is not None else None
                    except (ValueError, TypeError):
                        y = None
                seen[aid] = {
                    "arxiv_id": aid, "title": title, "authors": authors,
                    "year": y,
                    "abstract": abstract, "score": round(score, 4),
                    "hits": [phrase],
                }
    all_items = list(seen.values())
    total = len(all_items)
    # 分页
    start = (page - 1) * per
    page_items = all_items[start:start + per]
    results = [search_rbo.normalize_paper_schema(r, engine="fts", rank=start + i + 1)
               for i, r in enumerate(page_items)]
    idx_range = _fts_index_years()
    idx_note = (f"当前 FTS 索引（{idx_range[0]}-{idx_range[1]} 年）"
                if idx_range else "当前 FTS 索引不可用")
    pages = (total + per - 1) // per if total else 0
    return jsonify({
        "ok": True, "query": query, "phrases": phrases, "mode": mode,
        "cache_hit": (mode == "cache"),
        "phrase_status": phrase_status,
        "engine": "fts", "year": year,
        "n_results": len(results), "total": total,
        "page": page, "per": per, "pages": pages,
        "results": results,
        "index_note": idx_note, "index_years": list(idx_range) if idx_range else None,
    })


@app.route("/")
def index():
    """根路径直接进入 PaperExplore 探索页。"""
    from flask import redirect
    return redirect("/explore")


# ---------------------------------------------------------------------------
# FocusView 2.0 画廊（/list 入口）：复用冻结模板 gallery.html + focus_map 逻辑。
# gallery.html 依赖 /visual/* 与 /focus/* 路由，此处补上（同域名，独立页面）。
# ---------------------------------------------------------------------------
@app.route("/list")
def list_view():
    """FocusView 2.0 画廊（逐年可视化列表）。复用冻结 gallery.py 的 registry。"""
    import gallery
    reg = gallery.load_registry()
    years = [int(y) for y in sorted(reg)]
    return render_template("gallery.html", years=years, reg=reg, top_n=1500,
                           year_min=YEAR_MIN, year_max=YEAR_MAX)


@app.route("/visual/<path:fp>")
def serve_visual(fp):
    """服务 FocusView 全年图（sci365/pipeline2/data/visual/）。"""
    return send_from_directory(VISUAL_DIR, fp)


@app.route("/focus/<path:fp>")
def serve_focus_v2(fp):
    """服务 FocusView 焦点图（loomsci data/visual/focus/）。"""
    return send_from_directory(FOCUS_V2, fp)


@app.route("/focus", methods=["POST"])
def focus_v2():
    """FocusView 2.0 焦点图生成（复用冻结的 gallery.focus_map 单子图三渲染）。"""
    import gallery
    data = request.get_json(silent=True) or {}
    try:
        year = int(data.get("year", LATEST_ANN_YEAR))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "year 必须为整数"}), 400
    words = [w.strip() for w in (data.get("focus", "") or "").split(",") if w.strip()]
    if not words:
        return jsonify({"ok": False, "error": "请输入焦点词"}), 400
    if len(words) > 5:
        return jsonify({"ok": False, "error": "焦点词最多 5 个"}), 400
    ok, res = gallery.focus_map(words, year)
    if ok:
        return jsonify({"ok": True, **res})
    return jsonify({"ok": False, "error": res}), 500


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5010)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    print(f"[PaperExplore 3.0] http://{args.host}:{args.port}  (可视化搜索)")
    app.run(host=args.host, port=args.port, debug=False)
