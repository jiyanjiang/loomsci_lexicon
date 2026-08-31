# -*- coding: utf-8 -*-
"""200 个平衡起点：按 arXiv 大类系统覆盖（用户 2026-08-15 要求重做）

原则：
1. 覆盖 arXiv 主要大类（astro/cond-mat/hep/quant/math/physics/cs/biology...）
2. 每学科取"代表性学术短语"（2015 数据中该学科高频短语）
3. AI 相关只占合理比例（~15%），不主导
4. 颗粒度：细分领域（强关联/霍尔效应/量子信息...）
"""
import json, os, sys
from collections import defaultdict, Counter
import duckdb

ANN = "data/annotation/normalized"
PAPERS = "data/parquet/papers"

# 学科 → arXiv 主类匹配（2025 分布的概括）
# 每学科配 1-2 个主类前缀
FIELD_MAP = {
    "astro-physics": ["astro-ph%"],          # 天体物理
    "cond-mat-strongly-correlated": ["cond-mat.str-el%", "cond-mat.supr-con%"],  # 强关联/超导
    "cond-mat-materials": ["cond-mat.mtrl-sci%"],  # 材料
    "cond-mat-mesoscopic": ["cond-mat.mes-hall%"], # 介观/量子输运
    "cond-mat-soft": ["cond-mat.soft%"],     # 软物质
    "cond-mat-disorder": ["cond-mat.dis-nn%"],  # 无序
    "cond-mat-statmech": ["cond-mat.stat-mech%"], # 统计力学
    "hep-ph": ["hep-ph%"],                   # 高能唯象
    "hep-th": ["hep-th%"],                   # 高能理论
    "hep-ex": ["hep-ex%"],                   # 高能实验
    "gr-qc": ["gr-qc%"],                     # 引力
    "nucl-th": ["nucl-th%"],                 # 核理论
    "quant-ph": ["quant-ph%"],               # 量子物理
    "quantum-info": ["quant-ph%"],           # 量子信息（与上同源，另选词）
    "physics-optics": ["physics.optics%"],   # 光学
    "physics-plasma": ["physics.plasm-ph%"], # 等离子体
    "physics-atomic": ["physics.atom-ph%"],  # 原子物理
    "physics-fluids": ["physics.flu-dyn%"],  # 流体
    "physics-geo": ["physics.geo-ph%"],      # 地球物理
    "physics-bio": ["physics.bio-ph%"],      # 生物物理
    "physics-chem": ["physics.chem-ph%"],    # 化学物理
    "math-AP": ["math.AP%"],                 # 应用数学
    "math-CO": ["math.CO%"],                 # 组合数学
    "math-OC": ["math.OC%"],                 # 优化
    "math-NA": ["math.NA%"],                 # 数值分析
    "math-NT": ["math.NT%"],                 # 数论
    "math-PR": ["math.PR%"],                 # 概率
    "math-DS": ["math.DS%"],                 # 动力系统
    "math-AG": ["math.AG%"],                 # 代数几何
    "math-DG": ["math.DG%"],                 # 微分几何
    "stat-ML": ["stat.ML%", "stat.ME%"],     # 统计/ML理论
    "cs-AI": ["cs.AI%", "cs.LG%"],           # AI（限制数量）
    "cs-CV": ["cs.CV%"],                     # 视觉（AI系）
    "cs-CL": ["cs.CL%"],                     # NLP（AI系）
    "cs-RO": ["cs.RO%"],                     # 机器人
    "cs-CR": ["cs.CR%"],                     # 安全
    "cs-SY": ["eess.SY%", "cs.SY%"],         # 控制/系统
    "cs-SE": ["cs.SE%"],                     # 软件
    "cs-DC": ["cs.DC%", "cs.NI%"],           # 分布式/网络
    "cs-DB": ["cs.DB%"],                     # 数据库
    "eess-SP": ["eess.SP%"],                 # 信号处理
    "eess-IV": ["eess.IV%"],                 # 图像处理
    "q-bio": ["q-bio%"],                     # 生物
    "q-fin": ["q-fin%"],                     # 金融
    "q-bio-GE": ["q-bio.GN%"],               # 基因组
    "q-bio-BM": ["q-bio.BM%"],               # 生物医学
    "nlin": ["nlin%"],                       # 非线性
}


def field_phrases(cats, y, top_n):
    """提取某学科 2015 年高频短语"""
    con = duckdb.connect(":memory:")
    conds = " OR ".join([f"p.categories LIKE '{c}'" for c in cats])
    rows = con.execute(f"""
        SELECT ph, count(*) n FROM (
            SELECT unnest(a.phrases) ph
            FROM read_parquet('{ANN}/year={y}/part-0.parquet') a
            JOIN read_parquet('{PAPERS}/year={y}/*.parquet') p ON a.arxiv_id = p.arxiv_id
            WHERE {conds}
        ) GROUP BY ph ORDER BY n DESC LIMIT 500
    """).fetchall()
    con.close()
    # 过滤套话
    JARGON = ['real world', 'real time', 'high quality', 'large scale', 'novel',
              'state of the art', 'various', 'multiple', 'different', 'improve',
              'enhance', 'comprehensive', 'extensive', 'diverse', 'significant',
              'recent', 'important', 'general', 'specific', 'certain', 'single',
              'due to', 'order to', 'terms of', 'well known', 'new method',
              'proposed', 'presented', 'obtained', 'studied', 'shown', 'used',
              'scheme', 'flux', 'invariant', 'positive integer', 'upper bound',
              'lower bound', 'sufficient condition', 'numerical simulation',
              'experimental data', 'convergence rate', 'time dependent',
              'functional', 'fitness', 'steady state']
    out = []
    for ph, n in rows:
        if n < 20:
            continue
        if any(j in ph.lower() for j in JARGON):
            continue
        if ph.count(' ') > 3:  # 4+ 词太具体
            continue
        out.append((ph, n))
        if len(out) >= top_n:
            break
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-n", type=int, default=4, help="非 AI 学科每类 seed 数（AI 类减半）")
    ap.add_argument("--ai-mult", type=float, default=0.5, help="AI 类每类 seed = top_n * ai_mult")
    ap.add_argument("--year", type=int, default=2015, help="提取高频短语的年份")
    ap.add_argument("--out", default="data/sci/discovery/randomwalk/seeds_balanced.json")
    ap.add_argument("--exclude-ai", action="store_true",
                    help="完全排除 AI 学科 + 短语级 AI 过滤（用户 2026-08-17：AI 容忍度=0）")
    args = ap.parse_args()

    t0 = __import__("time").time()
    # 每学科选节点（AI 类限量；--exclude-ai 时完全排除）
    ai_fields = {"cs-AI", "cs-CV", "cs-CL", "stat-ML"}
    seeds = []
    seen_ph = set()   # 全局去重
    for field, cats in FIELD_MAP.items():
        if args.exclude_ai and field in ai_fields:
            continue
        top_n = max(1, int(args.top_n * args.ai_mult)) if field in ai_fields else args.top_n
        phs = field_phrases(cats, args.year, top_n * 2)
        # 跨学科去重 + 每学科取前 top_n
        picked = 0
        for ph, n in phs:
            if ph in seen_ph:
                continue
            if args.exclude_ai:
                from ai_filter import is_ai
                if is_ai(ph):
                    continue  # AI 容忍度=0
            seen_ph.add(ph)
            seeds.append({"field": field, "phrase": ph, "freq": n})
            picked += 1
            if picked >= top_n:
                break

    print(f"seed 总数: {len(seeds)}")
    # 分布检查
    from collections import Counter
    fc = Counter(s["field"] for s in seeds)
    print("按学科:")
    for f, c in sorted(fc.items(), key=lambda x: -x[1]):
        print(f"  {f:28s}: {c}")
    ai_n = sum(1 for s in seeds if s["field"] in ai_fields)
    print(f"\nAI 类占比: {ai_n}/{len(seeds)} = {ai_n/len(seeds)*100:.0f}%")

    print("\n=== seed 明细 ===")
    for s in seeds:
        print(f"  [{s['field']:24s}] {s['phrase']} ({s['freq']})")

    json.dump(seeds, open(args.out, "w"), ensure_ascii=False, indent=1)
    print(f"\n→ {args.out} ({__import__('time').time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
