# SHARE MANIFEST

> What this package contains and how to rebuild the generated artifacts.

---

## 1. Included in this package

> **18 `.py` files** in `scripts/` + 2 in `web/` + 3 templates. Everything needed
> to run the service (scan → annotate → visualize → G/R → Web).

| Path | Stage | Note |
|---|---|---|
| `scripts/config.py` | config layer | reads `config.yaml` |
| `scripts/key_loader.py` | config layer | DeepSeek key loader (env → config.yaml) |
| `scripts/scan_year.py` | ① dictionary | academic phrase extraction (θ=0.3, freq_min=5) |
| `scripts/tokenizer.py` | ① tokenization | unified tokenizer |
| `scripts/annotate.py` | ② annotation | per-paper phrase annotation → parquet |
| `scripts/visualize.py` | ③ visualization | three-mode graphs + focus subgraphs |
| `scripts/g_ab_calc.py` | ④ G/R | effective-conductance/resistance library |
| `scripts/run_distance_batch.py` | ⑤ evolution | batch G/R time series |
| `scripts/build_fts_from_parquet.py` | FTS | build BM25 index from parquet |
| `scripts/search_rbo.py` | retrieval | RBO semantic search (SQL prefilter + RBO rank) |
| `scripts/search_recommend.py` | retrieval | concept-pair recommender (raw prefilter + norm aggregation) |
| `scripts/rbo.py` | retrieval | RBO ranking-similarity algorithm |
| `scripts/orchestrate_query.py` | retrieval | query orchestration (NL→phrases + cache) |
| `scripts/fts_helper.py` | retrieval | FTS(BM25) query helper |
| `scripts/test_rbo.py` | sanity check | quick self-check (6 items) |
| `scripts/phrase_forms.py` | foundation | phrase-form normalization (hard dep) |
| `scripts/dns_patch.py` | foundation | DNS resilience (hard dep) |
| `scripts/build_visual_registry.py` | gallery | rebuild `data/visual/registry.csv` (gallery index) |
| `web/explore.py` | Web | PaperExplore: focus-graph + preprint panel |
| `web/gallery.py` | Web | FocusView: per-year gallery |
| `web/templates/explore.html` | Web | PaperExplore page |
| `web/templates/distance.html` | Web | distance page |
| `web/templates/gallery.html` | Web | gallery page |
| `docs/parquet_format.md` | docs | Kaggle → parquet correspondence |
| `docs/pipeline_sop.md` | docs | full pipeline runbook |
| `README.md` / `LICENSE` / `requirements.txt` / `.gitignore` | engineering | standard project files |
| `config.example.yaml` | config | empty template (copy to `config.yaml`) |
| `SHARE_MANIFEST.md` | engineering | this document |

### Data

| Path | Note |
|---|---|
| `data/by_year/` (`terms_*.csv`) | per-year scan dictionaries, **52MB total** |
| `data/cumulative/lexicon_2025.csv` | cumulative lexicon (17.7MB) |
| `data/parquet/papers/year={1991..1995}/` | fact-layer first 5 years (~33k papers) |
| `data/annotation/raw/year={1991..1995}/` | raw annotation (RBO search source) |
| `data/annotation/normalized/year={1991..1995}/` | normalized annotation (visualization input) |
| `data/number_normalize.csv` / `abbrev_follow.csv` | singular/plural & abbreviation tables |
| `data/whitelist_manual.txt` / `blacklist_manual.txt` | manual whitelist/blacklist |
| `data/arxiv_terms/single_tok_keep_final.txt` | LLM single-token whitelist |
| `data/arxiv_terms/LLM_arxiv_multi_tokens_pub.csv` | LLM multi-token whitelist (29,979 terms, always loaded) |
| `data/stop/` | scan stopword tables (required by scan) |

---

## 2. Artifacts you need to rebuild

The following are generated at runtime / build time, so they are **not** shipped. You rebuild them with the commands below.

| Artifact | Rebuild command |
|---|---|
| BM25 index | `python scripts/build_fts_from_parquet.py --years 1991-1995` |
| Network graphs + gallery index | see §3 |

---

## 3. Reproduction path

```bash
cp config.example.yaml config.yaml   # fill in papers_dir etc.
pip install -r requirements.txt
python scripts/build_fts_from_parquet.py --years 1991-1995   # self-build FTS (<30s)
python scripts/scan_year.py --year 1992                       # dictionary
python scripts/annotate.py --years 1992 --normalize           # annotation
# --- gallery rebuild (graphs + registry) ---
for y in 1991 1992 1993 1994 1995; do
  python scripts/visualize.py --mode static --year $y
  python scripts/visualize.py --mode speed  --target $y --base $((y-1))
  python scripts/visualize.py --mode accel  --target $y --prev $((y-1)) --base $((y-2))
done
python scripts/build_visual_registry.py    # rebuild data/visual/registry.csv (gallery index)
python web/explore.py --port 5010           # interactive web
```

> **Gallery (`/list`) note**: the per-year network graphs and `data/visual/registry.csv`
> (gallery index) are generated artifacts — run the `visualize.py` +
> `build_visual_registry.py` loop above once, then the gallery works.

---

# 分享清单

> 本包包含什么，以及如何重建生成类产物。

---

## 一、包内包含

> **scripts/ 18 个 .py + web/ 2 个 + 3 模板**。跑通服务所需全部（scan → 标注 → 可视化 → G/R → Web）。

| 路径 | 环节 | 说明 |
|---|---|---|
| `scripts/config.py` | 配置层 | 读取 config.yaml |
| `scripts/key_loader.py` | 配置层 | DeepSeek key 加载（env → config.yaml）|
| `scripts/scan_year.py` | ① 词典提取 | 学术短语提取（θ=0.3, freq_min=5）|
| `scripts/tokenizer.py` | ① 分词 | 统一分词器 |
| `scripts/annotate.py` | ② 标注 | 逐篇短语标注 → parquet |
| `scripts/visualize.py` | ③ 可视化 | 三图 + 焦点子图 |
| `scripts/g_ab_calc.py` | ④ G/R | G/R 核心库（有效电阻）|
| `scripts/run_distance_batch.py` | ⑤ 演化 | 概念对批量 G/R |
| `scripts/build_fts_from_parquet.py` | FTS | 从 parquet 构建 BM25 检索库 |
| `scripts/search_rbo.py` | 检索 | RBO 语义检索（SQL 预筛 + RBO 精排）|
| `scripts/search_recommend.py` | 检索 | 概念对推荐（raw 预筛 + norm 聚合）|
| `scripts/rbo.py` | 检索 | RBO 排名相似度算法 |
| `scripts/orchestrate_query.py` | 检索 | 查询编排（NL→短语 + 缓存）|
| `scripts/fts_helper.py` | 检索 | FTS(BM25) 查询助手 |
| `scripts/test_rbo.py` | 冒烟 | 快速自检（6 项）|
| `scripts/phrase_forms.py` | 基础 | 短语形态归一（硬依赖）|
| `scripts/dns_patch.py` | 基础 | DNS 弹性（硬依赖）|
| `scripts/build_visual_registry.py` | 画廊 | 重建 data/visual/registry.csv（画廊索引）|
| `web/explore.py` | Web | PaperExplore：焦点图 + 预印本面板 |
| `web/gallery.py` | Web | FocusView：逐年画廊 |
| `web/templates/explore.html` | Web | PaperExplore 页面 |
| `web/templates/distance.html` | Web | 距离页面 |
| `web/templates/gallery.html` | Web | 画廊页面 |
| `docs/parquet_format.md` | 文档 | parquet 格式说明 |
| `docs/pipeline_sop.md` | 文档 | 全流程运行手册 |
| `README.md` / `LICENSE` / `requirements.txt` / `.gitignore` | 工程 | 标准工程文件 |
| `config.example.yaml` | 配置 | 空模板（复制为 config.yaml）|
| `SHARE_MANIFEST.md` | 工程 | 本文档 |

### 数据

| 路径 | 说明 |
|---|---|
| `data/by_year/`（terms_*.csv）| scan 词典逐年产物，共 52MB |
| `data/cumulative/lexicon_2025.csv` | 累计词典（17.7MB）|
| `data/parquet/papers/year={1991..1995}/` | 事实层前 5 年（约 3.3 万篇）|
| `data/annotation/raw/year={1991..1995}/` | raw 标注（RBO 检索数据源）|
| `data/annotation/normalized/year={1991..1995}/` | normalized 标注（可视化输入）|
| `data/number_normalize.csv` / `abbrev_follow.csv` | 单复数/缩写归一表 |
| `data/whitelist_manual.txt` / `blacklist_manual.txt` | 手工白名单/黑名单 |
| `data/arxiv_terms/single_tok_keep_final.txt` | LLM 单 token 白名单 |
| `data/arxiv_terms/LLM_arxiv_multi_tokens_pub.csv` | LLM 多 token 白名单（29,979 词，始终读入）|
| `data/stop/` | scan 停用词表（scan 必读）|

---

## 二、需自行重建的产物

以下为运行/构建时生成的产物，**不随包提供**，按下方命令自行重建。

| 产物 | 重建命令 |
|---|---|
| BM25 检索库 | `python scripts/build_fts_from_parquet.py --years 1991-1995` |
| 网络图 + 画廊索引 | 见 §三 |

---

## 三、复现路径

```bash
cp config.example.yaml config.yaml   # 填写 papers_dir 等路径
pip install -r requirements.txt
python scripts/build_fts_from_parquet.py --years 1991-1995   # 自建 FTS（<30s）
python scripts/scan_year.py --year 1992                       # 词典
python scripts/annotate.py --years 1992 --normalize           # 标注
# --- 画廊重建（图产物 + registry）---
for y in 1991 1992 1993 1994 1995; do
  python scripts/visualize.py --mode static --year $y
  python scripts/visualize.py --mode speed  --target $y --base $((y-1))
  python scripts/visualize.py --mode accel  --target $y --prev $((y-1)) --base $((y-2))
done
python scripts/build_visual_registry.py    # 重建 data/visual/registry.csv（画廊索引）
python web/explore.py --port 5010           # 交互
```

> **画廊（/list）说明**：逐年网络图与 `data/visual/registry.csv`（画廊索引）是生成产物——
> 按上面循环运行一次 `visualize.py` + `build_visual_registry.py` 后，画廊即可用。

---

## 2. G 预测模块（v20 并入）

> v18 的 G(AB,t) 科学方向涌现预测模块，干净并入（无 `__pycache__`/`.DS_Store`）。

| 类别 | 文件 |
|---|---|
| 脚本 | `sci_seeds_balanced.py` `sci_rw_sampler.py` `sci_backtest_2016.py` `sci_backtest_fair.py` `sci_g_series_fast.py` `sci_predict2026_v3.py` `sci_llm_filter_2026.py` `sci_gplots_50.py` `sci_rank_experiment.py` `sci_rank_figs.py` |
| 数据 | `data/sci/discovery/randomwalk/{seeds_balanced.json,rank_2000.json,backtest_fair_2015.json,rankfigs/*}` `data/sci/discovery/backtest/backtest_2016.json` `data/sci/discovery/predict2026/{gseries_2025_sorted,selected50,verdicts,gfigs_stats}.json` `data/sci/discovery/predict2026/gfigs/*.png`(50) `gfigs_50.html` |
| 文档 | `docs/sci_predict_reproduce.md`（完整复现指南）`docs/SCI_G_PREDICT_RECORD_20260814.md` `docs/SCI_DISCOVERY_STUDY_PLAN_20260814.md` `docs/SCI_WECHAT_POST_20260815.md` |
| 共用基建 | `config.py` `key_loader.py` `dns_patch.py` `phrase_forms.py`（与主链共用） |
