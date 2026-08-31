#!/usr/bin/env python3
"""学术短语标注（pipeline2 新版本，统一分词 + 单token支持，2026-08-07）。

由 scripts/annotate_articles_phrases.py 升级而来，本质改动：
1. 分词统一用 pipeline2/tokenizer.py 的 tokenize()（与 scan 一致，命中自然一致）。
2. 词典 = scan 多词短语 + LLM 单-token 白名单(2000) + LLM 多-token 白名单。
3. 词典短语也用 tokenize() 规范化，与摘要 tokenize 对齐。
4. 滑窗 n_tokens>=1（支持单-token 术语标注，如 llm/3-manifold）。

全流程：统一分词 → scan → 学术短语库 → 后置黑名单 → LLM白名单 → 标注 → 单复数 → 可视化
"""
import os, sys, argparse, re, csv, glob, time
from collections import defaultdict
import duckdb
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tokenizer import tokenize

# 对外分享版：路径统一从 config.py 读取（config.yaml 配置）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
PAPERS = config.PAPERS_DIR
SCAN_DIR = config.SCAN_DIR
ARXIV_TERMS = config.ARXIV_TERMS_DIR
SINGLE_TOK_FILE = os.path.join(ARXIV_TERMS, "single_tok_keep_final.txt")
NUMBER_NORMALIZE = config.NUMBER_NORMALIZE
ABBREV_FOLLOW = config.ABBREV_FOLLOW
MANUAL_WHITELIST = config.MANUAL_WHITELIST
BLACKLIST_MANUAL = config.BLACKLIST_MANUAL
OUT_BASE = config.ANN_RAW
OUT_NORM = config.ANN_NORM

MAX_N = 6  # 短语最长 token 数
MANUAL_WHITELIST_MAX = 5  # 用户自定义白名单最多词数


# ---------- 词典加载 ----------
def load_scan_phrases():
    """加载 scan 多词短语（pipeline2/data/scan/*.csv）。"""
    terms = set()
    # 只加载全年完整文件，排除单月测试文件（terms_2021-01_test.csv）
    for fp in sorted(glob.glob(os.path.join(SCAN_DIR, "terms_*_pipeline2.csv"))):
        bn = os.path.basename(fp)
        if "_test" in bn or "-" in bn.split("terms_")[1].split("_")[0]:
            continue
        with open(fp, newline="") as fh:
            for r in csv.DictReader(fh):
                t = (r.get("term") or "").strip().lower()
                if t and len(t.split()) >= 2:
                    terms.add(tuple(t.split()))
    return terms


def load_llm_single_tokens():
    """加载 LLM 单-token 精简白名单（2000 词）。"""
    terms = set()
    if os.path.exists(SINGLE_TOK_FILE):
        for line in open(SINGLE_TOK_FILE):
            w = line.strip().lower()
            if w:
                terms.add((w,))
    return terms


def load_manual_whitelist():
    """加载用户自定义白名单（pipeline2/data/whitelist_manual.txt）。

    用户手动维护，找回被消杀/未加载的概念（如 STOP 误杀、1-token 缺省不加载）。
    每行一个词（# 注释忽略），最多取 MANUAL_WHITELIST_MAX 个；统一 tokenize 规范化后入词典。
    与 LLM 白名单同级，在标注阶段加载。
    """
    terms = set()
    if not os.path.exists(MANUAL_WHITELIST):
        return terms
    n = 0
    for line in open(MANUAL_WHITELIST):
        t = line.strip().lower()
        if not t or t.startswith("#"):
            continue
        if n >= MANUAL_WHITELIST_MAX:
            break
        tt = tuple(tokenize(t))
        if tt:
            terms.add(tt)
            n += 1
    return terms


def load_llm_multi_tokens():
    """加载 arxiv_terms 多-token 白名单（LLM 生成，多词短语）。"""
    terms = set()
    for fp in sorted(glob.glob(os.path.join(ARXIV_TERMS, "*.csv"))):
        if "_index" in fp or "single_tok" in fp:
            continue
        with open(fp, newline="") as fh:
            for r in csv.DictReader(fh):
                e = (r.get("en") or "").strip().lower()
                if e and len(e.split()) >= 2:
                    # 用统一 tokenize 规范化；若被符号破坏成单-token(如 π₁¹ set→set)则跳过
                    tt = tuple(tokenize(e))
                    if len(tt) >= 2:
                        terms.add(tt)
    return terms


def load_post_blacklist(bl_path=None):
    """加载后置黑名单（默认 pipeline2/data/blacklist_manual.txt；可传 --blacklist 指定）。

    严格精确匹配：黑名单条目须与实际短语整串一致（如 https github.com），
    词典中「完全相等」的短语才会被剔除。不发明子串/token 匹配算法。"""
    bl = set()
    fp = bl_path or BLACKLIST_MANUAL
    if os.path.exists(fp):
        for line in open(fp):
            term = line.strip().lower()
            if term and not term.startswith("#"):
                bl.add(term)
    return bl


def load_lexicon(path):
    """加载外部学术短语词典 CSV（--lexicon，如 loomsci lexicon_2025.csv）。

    格式：任意含 term / n_tokens 列的 CSV（rank,term,n_tokens,first_year,...）。
    词典条目统一 tokenize 规范化（与摘要分词一致），仅收多-token 短语。"""
    terms = set()
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            t = (r.get("term") or "").strip().lower()
            if not t:
                continue
            tt = tuple(tokenize(t))
            if len(tt) >= 2:
                terms.add(tt)
    return terms


def load_override(path):
    """加载 override 白名单（--override，如 loomsci whitelist_override.txt）。

    每行一个短语（# 注释忽略），强制加入词典（在 LLM 白名单之后、黑名单之前，
    即使词典/scan 中不存在也强制命中）。统一 tokenize 规范化。"""
    terms = set()
    if not path or not os.path.exists(path):
        return terms
    for line in open(path):
        t = line.strip().lower()
        if not t or t.startswith("#"):
            continue
        tt = tuple(tokenize(t))
        if tt:
            terms.add(tt)
    return terms


def build_dict(use_single_tokens=True, lexicon=None, override=None, blacklist=None):
    """合并词典：scan 多词（或外部词典） + [可选]LLM 单token + LLM 多token
    + 用户自定义白名单 + override 白名单，最后统一做后置黑名单精确剔除。

    参数：
    - lexicon: 外部词典 CSV 路径。给定后替代 scan 多词（如 loomsci lexicon_2025.csv）
    - override: override 白名单 txt（强制+，在 LLM 白名单之后并入）
    - blacklist: 后置黑名单 txt（默认 pipeline2/data/blacklist_manual.txt）
    - use_single_tokens=False 时卸载单-token 白名单（no-1-token-white-list 选项）。
    返回 (set_of_token_tuples, 最大 n_tokens, manual_terms)。"""
    dset = set()
    # scan 多词 或 外部词典
    if lexicon:
        lx = load_lexicon(lexicon)
        dset |= lx
        print(f"  外部词典(--lexicon): {len(lx)}")
    else:
        scan = load_scan_phrases()
        dset |= scan
        print(f"  scan 多词短语: {len(scan)}")
    # LLM 单-token（可卸载）
    if use_single_tokens:
        single = load_llm_single_tokens()
        dset |= single
        print(f"  LLM 单-token: {len(single)}")
    else:
        print(f"  LLM 单-token: 已卸载（no-1-token-white-list）")
    # LLM 多-token
    multi = load_llm_multi_tokens()
    dset |= multi
    print(f"  LLM 多-token: {len(multi)}")
    # 用户自定义白名单（找回被消杀/未加载概念，强制入词典）
    manual = load_manual_whitelist()
    dset |= manual
    print(f"  用户自定义白名单: {len(manual)}")
    # override 白名单（强制+，最灵活；在黑名单之前并入）
    ov = load_override(override)
    if ov:
        dset |= ov
        print(f"  override 白名单: {len(ov)}")
    # 后置黑名单精确剔除（最后，最高优先级）
    bl = load_post_blacklist(blacklist)
    if bl:
        before = len(dset)
        dset = {t for t in dset if " ".join(t) not in bl}
        print(f"  后置黑名单精确剔除: {before} -> {len(dset)} (黑名单{len(bl)}条)")
    max_n = min(MAX_N, max((len(t) for t in dset), default=2))
    return dset, max_n, manual


# ---------- 数据读取 ----------
def read_abstracts(year, category=None):
    """读单年论文，返回 list of (arxiv_id_str, text)。text = title + '.\n' + abstract。"""
    pat = os.path.join(PAPERS, f"year={year}/*.parquet")
    if not glob.glob(pat):
        return []
    con = duckdb.connect(database=":memory:")
    where = ""
    if category:
        where = ("WHERE starts_with(str_split(categories, ' ')[1], 'cs')"
                 if category == "cs" else
                 "WHERE NOT starts_with(str_split(categories, ' ')[1], 'cs')")
    sql = (f"SELECT arxiv_id, title, abstract FROM read_parquet('{pat}')"
           + (f" {where}" if where else ""))
    rows = con.execute(sql).fetchall()
    con.close()
    out = []
    for aid, ti, ab in rows:
        ti = (ti or "").lower()
        ab = (ab or "").lower()
        out.append((str(aid), ti + ".\n" + ab))
    return out


def _absorb(records):
    """全局子串吸收：若某短语被另一个更长短语在文本中完全包含（连续子串且位置重叠），
    则删掉短的，只保留长的（信息量最大）。
    例：A B C 在位置[0,3)，B C 在位置[1,3) ⊂ [0,3) → 删 B C，留 A B C。
    不同位置（不重叠）的短语即使有子串关系也保留（如 ni ma 独立出现）。
    """
    keep = []
    for i, (ph, s0, e0) in enumerate(records):
        absorbed = False
        for j, (q, s1, e1) in enumerate(records):
            if i == j:
                continue
            # q 更长且完全包含 ph（s1<=s0 且 e0<=e1）
            if s1 <= s0 and e0 <= e1 and len(q.split()) > len(ph.split()):
                absorbed = True
                break
        if not absorbed:
            keep.append(ph)
    return keep


def match_article(toks, dset, max_n):
    """贪心最长匹配标注（n_tokens>=1）+ 全局子串吸收。

    1. 每个位置 i 从最长到最短贪心，取第一个命中的最长短语（同一 start 只取最长）。
    2. 全局子串吸收：若某短语被更长短语在文本中完全包含，删掉短的。
       → A B C 会吸收内部/前缀的 A B、B C；
         但文本别处独立出现的短语（不重叠）仍保留（如 ni ma）。
    """
    seen_ph = set()
    records = []   # (phrase, start, end)
    n = len(toks)
    for i in range(n):
        for L in range(max_n, 0, -1):
            j = i + L
            if j > n:
                continue
            t = tuple(toks[i:j])
            if t in dset:
                s = " ".join(t)
                if s not in seen_ph:
                    seen_ph.add(s)
                    records.append((s, i, j))
                break   # 该位置取最长命中
    return _absorb(records)


def write_parquet(out_dir, year, rows):
    d = os.path.join(out_dir, f"year={year}")
    os.makedirs(d, exist_ok=True)
    con = duckdb.connect(database=":memory:")
    con.execute("CREATE TABLE t(arxiv_id VARCHAR, phrases VARCHAR[], n_phrases INT)")
    con.executemany("INSERT INTO t VALUES (?, ?, ?)", rows)
    path = os.path.join(d, "part-0.parquet")
    con.execute(f"COPY t TO '{path}' (FORMAT PARQUET)")
    con.close()
    return path


def load_number_normalize():
    """加载归一化映射（单复数 + 缩写跟随），返回穷举映射 {变体: merged}。
    单复数（number_normalize.csv）：{singular: merged, plural: merged}
    缩写跟随（abbrev_follow.csv）：{canonical: merged, abbrev_follow: merged}
    """
    m = {}
    if os.path.exists(NUMBER_NORMALIZE):
        with open(NUMBER_NORMALIZE, newline="") as fh:
            for r in csv.DictReader(fh):
                s = (r.get("singular") or "").strip().lower()
                p = (r.get("plural") or "").strip().lower()
                mrg = (r.get("merged") or "").strip().lower()
                if s:
                    m[s] = mrg
                if p:
                    m[p] = mrg
    if os.path.exists(ABBREV_FOLLOW):
        with open(ABBREV_FOLLOW, newline="") as fh:
            for r in csv.DictReader(fh):
                c = (r.get("canonical") or "").strip().lower()
                a = (r.get("abbrev_follow") or "").strip().lower()
                mrg = (r.get("merged") or "").strip().lower()
                if c:
                    m[c] = mrg
                if a:
                    m[a] = mrg
    return m


def normalize_phrases(phrases, norm_map):
    """把一篇的标注短语按归一化映射归一化（单复数 + 缩写跟随）。
    等价变体合并为 merged 形式；无映射的短语保持原样。去重保持顺序。"""
    out = []
    seen = set()
    for ph in phrases:
        t = norm_map.get(ph, ph)   # 命中映射 → 归一，否则原样
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def annotate_year(year, dset, max_n, out_base, category=None, normalize=False,
                  raw_out=None):
    """单年标注。normalize=True 时对标注短语做单复数归一，输出到 normalized/。
    返回统计 dict。

    2026-08-31 v21（Windows 复现报告收编）：normalize=True 时**双写**——
      未归一版 → raw_out（默认 config.ANN_RAW，RBO 检索数据源）
      归一版   → out_base（默认 config.ANN_NORM，可视化输入）
    旧行为一次运行只写一个目录，导致 raw/ 从未落盘、依赖 raw 的 RBO 检索无数据。
    """
    ab = read_abstracts(year, category)
    if not ab:
        return {"year": year, "n_articles": 0}
    norm_map = load_number_normalize() if normalize else None
    rows_raw, rows_norm = [], []
    for aid, text in ab:
        toks = tokenize(text)   # 统一分词
        ph = match_article(toks, dset, max_n)
        rows_raw.append((aid, ph, len(ph)))
        if normalize:
            phn = normalize_phrases(ph, norm_map)
            rows_norm.append((aid, phn, len(phn)))
    # 双写：raw 先落盘（RBO 检索源），再写归一版（可视化输入）
    if normalize and raw_out:
        write_parquet(raw_out, year, rows_raw)
    rows = rows_norm if normalize else rows_raw
    write_parquet(out_base, year, rows)
    n_art = len(rows)
    n_matched = sum(1 for _, _, c in rows if c > 0)
    tot = sum(c for _, _, c in rows)
    return {
        "year": year, "n_articles": n_art,
        "n_matched": n_matched,
        "coverage": n_matched / n_art if n_art else 0.0,
        "total_tags": tot,
        "avg_per_article": tot / n_art if n_art else 0.0,
        "dict_size": len(dset),
        "normalized": normalize,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=str, required=True, help="单年 1992 或区间 1986-1993")
    ap.add_argument("--category", type=str, default=None, choices=["cs", "basic"])
    ap.add_argument("--out", default=OUT_BASE)
    ap.add_argument("--normalize", action="store_true",
                    help="归一化单复数后标注，输出到 normalized/（适合可视化/嗅探）")
    ap.add_argument("--no-single-token", action="store_true",
                    help="卸载单-token 白名单，只加载多-token 白名单")
    ap.add_argument("--lexicon", default=None,
                    help="外部词典 CSV（如 loomsci lexicon_2025.csv），替代 scan 多词")
    ap.add_argument("--override", default=None,
                    help="override 白名单 txt（强制+，每行一个短语）")
    ap.add_argument("--blacklist", default=None,
                    help="后置黑名单 txt（默认 pipeline2/data/blacklist_manual.txt）")
    ap.add_argument("--out-norm", default=OUT_NORM,
                    help="归一化输出目录（默认 pipeline2/data/annotation/normalized）")
    args = ap.parse_args()

    # 归一化输出到 normalized/，否则默认 raw/
    out_base = args.out_norm if args.normalize else args.out
    # v21 双写：--normalize 时同时写 raw（ANN_RAW），保证 raw 目录始终有数据
    raw_out = args.out if args.normalize else None

    print("[dict] 构建标注词典（scan多词/外部词典 + LLM 单token + LLM 多token + 用户白名单 + override，统一 tokenize）")
    t0 = time.time()
    dset, max_n, manual = build_dict(use_single_tokens=not args.no_single_token,
                                     lexicon=args.lexicon, override=args.override,
                                     blacklist=args.blacklist)
    print(f"  词典共 {len(dset)} 个短语, max_n={max_n} ({time.time()-t0:.1f}s)")

    if "-" in args.years:
        a, b = args.years.split("-")
        years = list(range(int(a), int(b) + 1))
    else:
        years = [int(args.years)]

    for year in years:
        print(f"[annotate] year={year} {'normalized' if args.normalize else 'raw'} ...", flush=True)
        st = annotate_year(year, dset, max_n, out_base, category=args.category,
                           normalize=args.normalize, raw_out=raw_out)
        print(f"  {st}")


if __name__ == "__main__":
    main()
