#!/usr/bin/env python3
"""从 parquet 事实层构建预印本 BM25 检索库（DuckDB FTS）。

本项目 Web 交互页（explore.py /distance 面板）的「预印本检索」基于本库：
  - 点节点 → 检索含该短语的文章
  - 点边   → 检索同时含两个短语的文章（AND）
输出 `data/fts.duckdb`（项目内自带），`explore.py` 通过 config.fts_db 读取。

用法：
  python scripts/build_fts_from_parquet.py                     # 默认全量（所有年份）
  python scripts/build_fts_from_parquet.py --years 1991-1995   # 选择性构建（小样本测试）
  python scripts/build_fts_from_parquet.py --years 1992        # 单年
  python scripts/build_fts_from_parquet.py --dry-run           # 只报告规模，不构建

依赖：duckdb（fts 扩展）。查询入口 `fts_main_papers.match_bm25(paper_id, ?)`。
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

import duckdb

FTS_DB = os.path.join(config.OUTPUT_DIR, "fts.duckdb")
FTS_COLUMNS = "paper_id, title, abstract, authors, arxiv_id, submission_date"


def _iter_year_dirs(years: list[int]):
    """解析年份列表 → 存在的 year=YYYY 目录。"""
    found = []
    for y in years:
        pat = os.path.join(config.PAPERS_DIR, f"year={y}", "*.parquet")
        if glob.glob(pat):
            found.append(y)
        else:
            print(f"  [skip] year={y}: 无 parquet 数据")
    return found


def build(years: list[int], out_path: str = FTS_DB) -> tuple[str, int, int]:
    """从 parquet 构建 BM25 索引。返回 (库路径, 总行数, 抽样命中数)。"""
    year_dirs = _iter_year_dirs(years)
    if not year_dirs:
        raise SystemExit("错误: 指定年份无任何 parquet 数据")

    # 构造 read_parquet 多文件路径（DuckDB 支持逗号分隔 + 通配）
    all_files = []
    for y in year_dirs:
        all_files += glob.glob(os.path.join(config.PAPERS_DIR, f"year={y}", "*.parquet"))
    src = ", ".join(f"'{f}'" for f in all_files)

    new_path = out_path + ".new.duckdb"
    if os.path.exists(new_path):
        os.remove(new_path)

    con = duckdb.connect(new_path)
    con.execute("INSTALL fts")
    con.execute("LOAD fts")

    # 建 papers 表（paper_id = row_number，与主库语义一致）
    con.execute(
        f"CREATE TABLE papers AS "
        f"SELECT row_number() OVER () AS paper_id, "
        f"       arxiv_id, title, abstract, authors, submission_date, year "
        f"FROM read_parquet([{src}])"
    )
    # 过滤空文本（title/abstract 均空的行无检索价值）
    n_all = con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    con.execute(
        "DELETE FROM papers WHERE (title IS NULL OR title='') "
        "AND (abstract IS NULL OR abstract='')"
    )
    n_kept = con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]

    # BM25 索引（PRAGMA 新签名：DuckDB >= 1.5 build 6814ec9）
    con.execute(
        "PRAGMA create_fts_index('papers', 'paper_id', "
        "'title', 'abstract', 'authors')"
    )
    con.execute("CHECKPOINT")

    sample = con.execute(
        "SELECT COUNT(*) FROM papers "
        "WHERE fts_main_papers.match_bm25(paper_id, ?) IS NOT NULL",
        ["learning"]).fetchone()[0]
    con.close()

    # 原子替换：旧库先备份（避免 os.remove 被安全策略拦截导致 rename 失败）
    if os.path.exists(out_path):
        bak = out_path + f".bak_{int(time.time())}"
        os.rename(out_path, bak)
    os.replace(new_path, out_path)
    return out_path, n_kept, sample


def dry_run(years: list[int]):
    """只报告规模：各年 parquet 行数与预计总量。"""
    year_dirs = _iter_year_dirs(years)
    if not year_dirs:
        raise SystemExit("错误: 指定年份无任何 parquet 数据")
    total = 0
    for y in year_dirs:
        files = glob.glob(os.path.join(config.PAPERS_DIR, f"year={y}", "*.parquet"))
        src = ", ".join(f"'{f}'" for f in files)
        n = duckdb.connect(":memory:").execute(
            f"SELECT COUNT(*) FROM read_parquet([{src}])").fetchone()[0]
        total += n
        print(f"  year={y}: {n} 篇")
    print(f"  合计: {total} 篇（年份 {year_dirs[0]}-{year_dirs[-1]}）")
    print(f"  输出: {FTS_DB}")
    print(f"  预计索引构建时间: 全量 ~2-3 分钟 / 单年 <10 秒")


def _parse_years(spec: str) -> list[int]:
    """解析 --years：'1992' / '1991-1995' / '1991,1993,1995'。"""
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return sorted(set(out))


def _all_years() -> list[int]:
    """扫描 papers_dir 下全部存在的年份。"""
    ys = []
    for d in glob.glob(os.path.join(config.PAPERS_DIR, "year=*")):
        try:
            ys.append(int(os.path.basename(d).split("=")[1]))
        except (ValueError, IndexError):
            continue
    return sorted(ys)


def main():
    ap = argparse.ArgumentParser(description="从 parquet 构建 BM25 FTS 库")
    ap.add_argument("--years", default=None,
                    help="构建年份：'1992' / '1991-1995' / '1991,1993'（默认全量）")
    ap.add_argument("--dry-run", action="store_true", help="只报告规模，不构建")
    ap.add_argument("--out", default=FTS_DB, help="输出 duckdb 路径（默认 data/fts.duckdb）")
    args = ap.parse_args()

    years = _parse_years(args.years) if args.years else _all_years()
    print(f"papers_dir = {config.PAPERS_DIR}")
    print(f"构建年份   = {years[0]}-{years[-1]}（{len(years)} 年）")

    if args.dry_run:
        dry_run(years)
        return

    print("[build] 构建 BM25 索引 ...")
    path, n, sample = build(years, args.out)
    print(f"[done] {path}")
    print(f"  索引文章数 : {n}")
    print(f"  抽样命中   : {sample}（query='learning'）")
    print("  查询入口   : fts_main_papers.match_bm25(paper_id, ?)")


if __name__ == "__main__":
    main()
