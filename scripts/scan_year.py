#!/usr/bin/env python3
"""
loomsci_lexicon - scan one year (or founding range) of arXiv papers.

THIS FILE IS A FAITHFUL PORT of sci365/pipeline2/scan.py + tokenizer.py
(2026-08-07 production pipeline). Every function below mirrors the
production implementation line-for-line so the published data is
reproducible and consistent with the internal pipeline.

Differences from the production script (publication-only):
  1. config.py holds all paths/parameters (single-point configuration).
  2. Output goes to config.BY_YEAR_DIR as terms_{tag}_pipeline2.csv
     (tag = "1992" or "1986-1991" for the founding period).
  3. No web UI / API integration (pure CLI).

Usage:
    python scripts/scan_year.py --year 1992
    python scripts/scan_year.py --year 1986-1991     # founding period
"""
import os
import sys
import time
import csv
import re
import gc
import argparse
from collections import Counter

import duckdb

# Unified tokenizer (faithful copy of pipeline2/tokenizer.py)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tokenizer import tokenize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

# ----------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------
def load_abstracts(year, category=None, month=None):
    """Read title+abstract, equal weight. year is int or (start, end)."""
    con = duckdb.connect(database=":memory:")
    if isinstance(year, tuple):
        import glob as _g
        files = []
        for y in range(year[0], year[1] + 1):
            files += _g.glob(os.path.join(config.PAPERS_DIR, f"year={y}/*.parquet"))
        if not files:
            con.close()
            return {}
        src = files
    else:
        src = os.path.join(config.PAPERS_DIR, f"year={year}/*.parquet")
    where_parts = []
    if category:
        where_parts.append(
            "starts_with(str_split(categories, ' ')[1], 'cs')"
            if category == "cs" else
            "NOT starts_with(str_split(categories, ' ')[1], 'cs')")
    if month:
        where_parts.append(
            f"(submission_date >= '{year}-{month:02d}-01' AND "
            f"submission_date < '{year}-{month+1:02d}-01')")
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    sql = (f"SELECT arxiv_id, title, abstract FROM read_parquet({src!r})"
           + (f" {where}" if where else ""))
    rows = con.execute(sql).fetchall()
    con.close()
    out = {}
    for aid, ti, ab in rows:
        ti = (ti or "").lower()
        ab = (ab or "").lower()
        out[aid] = ti + ".\n" + ab
    return out


# ----------------------------------------------------------------------
# STOP loading (identical to production load_stop_file / _2 / _3)
# ----------------------------------------------------------------------
def load_stop_file(path):
    s = set()
    if not path or not os.path.exists(path):
        return s
    with open(path) as f:
        for line in f:
            line = line.strip().lower()
            if line and not line.startswith("#"):
                s.add(line)
    return s


# ----------------------------------------------------------------------
# Seed tokens (production load_raw_single_tokens)
# ----------------------------------------------------------------------
def load_raw_single_tokens(abstracts, top_k=50, stop=None, no_stop=False):
    _s = set() if no_stop else (stop if stop is not None else set())
    concepts = {}
    for aid, ab in abstracts.items():
        tks = tokenize(ab)
        cnt = Counter(t for t in tks if t not in _s)
        concepts[aid] = [t for t, _ in cnt.most_common(top_k)]
    return concepts


# ----------------------------------------------------------------------
# Seed/phrase validation (production is_garbage_single / is_seed_ok)
# ----------------------------------------------------------------------
def is_garbage_single(kw):
    if len(kw) <= 1:
        return True
    if not re.search(r"[a-z]", kw):   # no letter: pure digits/symbols
        return True
    return False


def is_seed_ok(kw):
    if " " in kw:
        return True
    return not is_garbage_single(kw)


def toks(s):
    return tuple(s.split())


# ----------------------------------------------------------------------
# Candidate generation (production build_candidates)
# ----------------------------------------------------------------------
def build_candidates(concepts, stop2=None):
    co = Counter()
    n_total = len(concepts)
    for idx, (aid, kws) in enumerate(concepts.items(), 1):
        ks = [kw for kw in kws if is_seed_ok(kw)]
        n = len(ks)
        for i in range(n):
            a = ks[i]
            for j in range(i + 1, n):
                b = ks[j]
                if stop2 and " " not in a and " " not in b:
                    ab = f"{a} {b}"
                    ba = f"{b} {a}"
                    if ab in stop2 or ba in stop2:
                        continue
                x, y = (a, b) if a < b else (b, a)
                if x == y:
                    continue
                co[(x, y)] += 1
        if idx % 50000 == 0:
            print(f"  [cand] {idx}/{n_total} papers (co={len(co)})",
                  flush=True)
    cand = [(a, b, c) for (a, b), c in co.items() if c >= config.T_MERGE]
    cand_set = set((a, b) for a, b, _ in cand)
    return co, cand, cand_set


# ----------------------------------------------------------------------
# Bigram stats (production build_bigram_stats)
# ----------------------------------------------------------------------
def build_bigram_stats(abstracts, seed_sets):
    co = Counter()
    cadj = Counter()
    n_total = len(abstracts)
    for idx, (aid, ab) in enumerate(abstracts.items(), 1):
        seeds = seed_sets.get(aid, set())
        sl = sorted(seeds)
        for i in range(len(sl)):
            for j in range(i + 1, len(sl)):
                co[(sl[i], sl[j])] += 1
        tks = tokenize(ab)
        seen = set()
        for i in range(len(tks) - 1):
            x, y = tks[i], tks[i + 1]
            if x == y:
                continue
            key = (x, y)
            if key not in seen:
                seen.add(key)
                cadj[key] += 1
        if idx % 50000 == 0:
            print(f"  [bigram] {idx}/{n_total} papers "
                  f"(co={len(co)} cadj={len(cadj)})", flush=True)
    return co, cadj


# ----------------------------------------------------------------------
# Junction approval (production approve_pairs + _junction_ok)
# ----------------------------------------------------------------------
def _minpair(j):
    return (j[0], j[1]) if j[0] < j[1] else (j[1], j[0])


def _junction_ok(j, co, cadj):
    x, y = j
    if x == y:
        return False
    denom = co.get(_minpair(j), 0)
    if denom == 0:
        return False
    num = cadj.get(j, 0)
    return num >= config.FREQ_MIN and (num / denom) >= config.THETA


def approve_pairs(cand, co, cadj):
    approved = []
    rdist = []
    for a, b, c in cand:
        ta, tb = toks(a), toks(b)
        ja = (ta[-1], tb[0])
        jb = (tb[-1], ta[0])
        va = _junction_ok(ja, co, cadj)
        vb = _junction_ok(jb, co, cadj)
        if not (va or vb):
            continue
        if va and vb:
            if cadj.get(ja, 0) >= cadj.get(jb, 0):
                merged, jchosen = f"{a} {b}", ja
            else:
                merged, jchosen = f"{b} {a}", jb
        elif va:
            merged, jchosen = f"{a} {b}", ja
        else:
            merged, jchosen = f"{b} {a}", jb
        denom = co.get(_minpair(jchosen), 0)
        r = cadj.get(jchosen, 0) / denom if denom > 0 else 0.0
        rdist.append(r)
        approved.append((a, b, merged, jchosen))
    return approved, rdist


# ----------------------------------------------------------------------
# Merge (production _contains_stop2/3 + apply_merge + _blocked)
# ----------------------------------------------------------------------
def _contains_stop2(merged, stop2):
    if not stop2:
        return False
    mtoks = merged.split()
    n = len(mtoks)
    if n < 2:
        return False
    for i in range(n - 1):
        w = f"{mtoks[i]} {mtoks[i+1]}"
        if w in stop2:
            return True
    return False


def _contains_stop3(merged, stop3):
    if not stop3:
        return False
    mtoks = merged.split()
    n = len(mtoks)
    if n < 3:
        return False
    for i in range(n - 2):
        w = f"{mtoks[i]} {mtoks[i+1]} {mtoks[i+2]}"
        if w in stop3:
            return True
    return False


def apply_merge(concepts, approved, stop2=None, stop3=None):
    approved_terms = set()
    for a, b, _merged, _j in approved:
        approved_terms.add(a)
        approved_terms.add(b)
    new_concepts = {}
    changed_aids = []
    for aid, kws in concepts.items():
        cur = list(kws)
        cur_set = set(cur)
        if not (cur_set & approved_terms):
            new_concepts[aid] = cur
            continue
        changed = False
        for (a, b, merged, _j) in approved:
            if a in cur_set and b in cur_set:
                ml = toks(merged)
                if len(ml) > config.MAX_MERGE_LEN:
                    continue
                if _contains_stop2(merged, stop2):
                    continue
                if _contains_stop3(merged, stop3):
                    continue
                cur = [w for w in cur if w != a and w != b]
                cur_set.discard(a)
                cur_set.discard(b)
                if merged not in cur_set:
                    cur_set.add(merged)
                    cur.append(merged)
                changed = True
        new_concepts[aid] = cur
        if changed:
            changed_aids.append(aid)
    return new_concepts, changed_aids


# ----------------------------------------------------------------------
# Main scan
# ----------------------------------------------------------------------
def scan_year(year, out_dir_override=None):
    t0 = time.time()
    ylabel = f"{year[0]}-{year[1]}" if isinstance(year, tuple) else str(year)
    print(f"[version] loomsci_lexicon  θ={config.THETA}  freq_min={config.FREQ_MIN}  "
          f"t_merge={config.T_MERGE}  max_merge_len={config.MAX_MERGE_LEN}", flush=True)

    stop = load_stop_file(config.STOP1_FILE)
    stop2 = load_stop_file(config.STOP2_FILE)
    stop3 = load_stop_file(config.STOP3_FILE)
    print(f"[v3] 1-token STOP={len(stop)}  2-token STOP={len(stop2)}  "
          f"3-token STOP={len(stop3)}", flush=True)

    print(f"[load] abstracts {ylabel} (read-only)...", flush=True)
    abstracts = load_abstracts(year)
    print(f"  abstracts={len(abstracts)}", flush=True)

    print(f"[load] 统一分词 tokenize 抽种子 (top-50/paper) 1-token STOP={len(stop)}...",
          flush=True)
    concepts = load_raw_single_tokens(abstracts, stop=stop)
    print(f"  papers={len(concepts)}  tokens/paper avg="
          f"{sum(len(v) for v in concepts.values())/max(1,len(concepts)):.1f}  "
          f"({time.time()-t0:.1f}s)", flush=True)
    seed_sets = {aid: set(kws) for aid, kws in concepts.items()}

    t1 = time.time()
    co, cadj = build_bigram_stats(abstracts, seed_sets)
    print(f"[bigram] co={len(co)} cadj={len(cadj)} ({time.time()-t1:.1f}s)", flush=True)
    # abstracts & seed_sets no longer needed (co/cadj captured the stats);
    # free them NOW to cut peak memory before the iteration loop.
    del abstracts, seed_sets
    gc.collect()

    all_r = []
    prev_n = None
    for it in range(1, config.MAX_ITER + 1):
        t1 = time.time()
        co_c, cand, cand_set = build_candidates(concepts, stop2=stop2)
        print(f"[iter {it}] candidates(co>={config.T_MERGE})={len(cand)} "
              f"({time.time()-t1:.1f}s)", flush=True)
        # co_c (per-iteration co-occurrence) and cand_set are NOT used by
        # approve_pairs (it uses the global co from bigram stats); free them
        # immediately to cut peak memory.
        del co_c, cand_set
        t2 = time.time()
        approved, rdist = approve_pairs(cand, co, cadj)
        all_r.extend(rdist)
        print(f"[iter {it}] approved={len(approved)}  approve-cost={time.time()-t2:.1f}s",
              flush=True)
        if not approved:
            print(f"[iter {it}] no new approval -> stop", flush=True)
            del cand, approved, rdist
            break
        # cand no longer needed after approve_pairs consumed it.
        del cand, rdist
        concepts, changed_aids = apply_merge(concepts, approved,
                                             stop2=stop2, stop3=stop3)
        # approved no longer needed after apply_merge consumed it.
        del approved
        n_phrases = sum(1 for v in concepts.values() for ph in v
                        if len(ph.split()) >= 2)
        print(f"[iter {it}] merged multi-word phrases total={n_phrases} | "
              f"changed_aids={len(changed_aids)}/{len(concepts)}", flush=True)
        if prev_n is not None and n_phrases == prev_n:
            break
        prev_n = n_phrases

    term_freq = Counter()
    for aids in concepts.values():
        for ph in aids:
            if len(ph.split()) >= 2:
                term_freq[ph] += 1

    # Export (publication format)
    if isinstance(year, tuple):
        tag = f"{year[0]}-{year[1]}"
    else:
        tag = str(year)
    out_dir = config.BY_YEAR_DIR
    if out_dir_override:
        out_dir = out_dir_override
    csv_path = os.path.join(out_dir, f"terms_{tag}_pipeline2.csv")
    multi = [(t, f) for t, f in term_freq.items() if len(t.split()) >= 2]
    multi.sort(key=lambda x: -x[1])
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        # Identical to production: terms are quoted (",term,") so CSV
        # files match pipeline2 output byte-for-byte.
        fh.write("rank,term,n_tokens,freq\n")
        for i, (t, freq) in enumerate(multi, 1):
            fh.write(f"{i},\"{t}\",{len(t.split())},{freq}\n")
    print(f"\n[export] {csv_path}  multi-word terms={len(multi)}", flush=True)

    print(f"\n=== Top 20 学术短语（{ylabel}）===")
    for i, (t, freq) in enumerate(multi[:20], 1):
        print(f"{i:3d}. {freq:6d}  {t}  ({len(t.split())}tok)")

    print(f"\n[done] total wall={time.time()-t0:.1f}s  terms={len(term_freq)}",
          flush=True)
    return term_freq


def parse_year_arg(s):
    s = s.strip()
    if "-" in s:
        a, b = s.split("-", 1)
        return (int(a), int(b))
    return int(s)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Scan one year (or founding range) into phrases")
    ap.add_argument("--year", type=str, required=True,
                    help="e.g. 1992 (single) or 1986-1991 (founding range)")
    ap.add_argument("--out-dir", type=str, default=None,
                    help="write terms CSV to this directory instead of "
                         "data/by_year (for throwaway validation runs; "
                         "prevents accidental pollution of the pipeline)")
    args = ap.parse_args()
    scan_year(parse_year_arg(args.year), args.out_dir)
