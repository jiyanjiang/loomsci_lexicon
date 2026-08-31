#!/usr/bin/env python3
"""从 sci365 主库提取 arxiv_id → 首要分类 映射，落轻量库供领域归一使用。

分类规则（arXiv OAI 约定，已抽样验证）：
  首要分类 = categories 字段的第一个 token（如 "cond-mat.supr-con cond-mat.mes-hall" → cond-mat.supr-con）
  领域粗分 = 首要分类前缀的根（cond-mat / hep-th / cs.AI / astro-ph / math / quant-ph ...）

输出：
  data/category_map.duckdb  → 表 paper_category(arxiv_id VARCHAR PRIMARY KEY, primary_cat VARCHAR, domain VARCHAR)
"""
import os
import sys
import duckdb

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

# 源库：本机 sci365 主库（含 arXiv categories）。从 config 读取，分享模板留空。
# 环境变量 SCI365_SOURCE_DB 可覆盖（如 /path/to/sci365.duckdb）。
SCI365_DB = os.getenv("SCI365_SOURCE_DB") or config.CATEGORY_MAP_SOURCE_DB
if not SCI365_DB:
    sys.exit("未配置源库：请设置 config.yaml 的 category_map_source_db 或环境变量 SCI365_SOURCE_DB")
OUT_DB = os.path.join(config.OUTPUT_DIR, "category_map.duckdb")


def domain_of(primary):
    """由首要分类派生领域粗分（arXiv 顶级分类）。"""
    p = primary.lower()
    if p.startswith("cs."):
        return "cs"
    if p.startswith("astro-ph"):
        return "astro"
    if p.startswith("math."):
        return "math"
    if p.startswith("quant-ph"):
        return "quant-ph"
    if p.startswith("cond-mat"):
        return "cond-mat"
    if p.startswith("hep-"):
        return "hep"
    if p.startswith("nucl-"):
        return "nucl"
    if p.startswith("physics."):
        return "physics"
    if p.startswith("stat."):
        return "stat"
    return p.split(".")[0] if "." in p else p


def main():
    print(f"读取主库 {SCI365_DB} ...", flush=True)
    con = duckdb.connect(SCI365_DB, read_only=True)
    print("导出 arxiv_id + categories ...", flush=True)
    # 流式导出（主库 85GB，只取两列）
    out = duckdb.connect(OUT_DB)
    out.execute("DROP TABLE IF EXISTS paper_category")
    out.execute("""CREATE TABLE paper_category(
        arxiv_id VARCHAR PRIMARY KEY, primary_cat VARCHAR, domain VARCHAR)""")
    out.execute("BEGIN TRANSACTION")
    # 分批流式读取主库
    batch = 200000
    offset = 0
    while True:
        rows = con.execute(
            f"SELECT arxiv_id, categories FROM papers "
            f"ORDER BY arxiv_id LIMIT {batch} OFFSET {offset}").fetchall()
        if not rows:
            break
        # 批量插入
        out.executemany(
            "INSERT INTO paper_category VALUES (?, ?, ?)",
            [(aid, cat.split(" ")[0] if cat else "", domain_of(cat.split(" ")[0] if cat else ""))
             for aid, cat in rows])
        offset += batch
        print(f"  {offset} 行", flush=True)
    out.execute("COMMIT")
    n = out.execute("SELECT count(*) FROM paper_category").fetchone()[0]
    # 领域分布
    dist = out.execute(
        "SELECT domain, count(*) FROM paper_category GROUP BY domain ORDER BY 2 DESC").fetchall()
    print(f"\n完成 {n} 行 → {OUT_DB}")
    print("领域分布:")
    for d, c in dist:
        print(f"  {d}: {c:,}")
    out.close()
    con.close()


if __name__ == "__main__":
    main()
