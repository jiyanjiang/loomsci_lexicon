# SHARE MANIFEST

> What this package contains and how to rebuild the generated artifacts.

---

## 1. Included in this package

> **31 `.py` files** in `scripts/` + 2 in `web/` + 3 templates. Everything needed
> to run the service (scan → annotate → visualize → G/R → prediction → Web).
>
> **v21 (2026-08-31)** — merges all fixes found by the independent Windows
> reproduction (see `docs/reproduction_report.md`):
> `.gitignore` and `lib/chartjs` restored (both were declared here but never
> packed), G-prediction module (`sci_*`) added, `ai_filter` added (hard
> dependency discovered by dependency-closure check).

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
| `scripts/sci_g_series_fast.py` | G-prediction | fast G-series extraction (hard dep of `sci_*`) |
| `scripts/sci_rw_sampler.py` | G-prediction | random-walk sampler (bridge / hit detection) |
| `scripts/sci_rank_experiment.py` | G-prediction | 2015-snapshot ranking experiment |
| `scripts/sci_rank_figs.py` | G-prediction | rank-experiment figures |
| `scripts/sci_gplots_50.py` | G-prediction | top-50 pair G-series plots |
| `scripts/sci_predict2026_v3.py` | G-prediction | 2026 prediction pipeline |
| `scripts/sci_backtest_2016.py` | G-prediction | 2016 backtest |
| `scripts/sci_backtest_fair.py` | G-prediction | fair backtest |
| `scripts/sci_seeds_balanced.py` | G-prediction | balanced seed selection |
| `scripts/sci_llm_filter_2026.py` | G-prediction | LLM filter stage |
| `scripts/ai_filter.py` | foundation | AI-term filter (hard dep of `sci_seeds_balanced`) |
| `scripts/build_category_map.py` | domain-norm | rebuild `data/category_map.duckdb` (needs source DB) |
| `scripts/build_binary_gmax.py` | domain-norm | rebuild `data/binary_gmax_*.json` |
| `lib/chartjs/chart.umd.min.js` | Web | local Chart.js (offline charts; was missing pre-v21) |
| `docs/reproduction_report.md` | docs | Windows 48h reproduction + v21 fix log |
| `web/explore.py` | Web | PaperExplore: focus-graph + preprint panel |
| `web/gallery.py` | Web | FocusView: per-year gallery |
| `web/templates/explore.html` | Web | PaperExplore page |
| `web/templates/distance.html` | Web | distance page |
| `web/templates/gallery.html` | Web | gallery page |
| `docs/parquet_format.md` | docs | Kaggle → parquet correspondence |
| `docs/pipeline_sop.md` | docs | full pipeline runbook |
| `README.md` / `LICENSE` / `requirements.txt` / `.gitignore` | engineering | standard project files (`.gitignore` restored in v21) |
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
| `data/category_map.duckdb.tar.gz` | domain-normalisation DB, compressed (23MB → unpacks to 142MB) |

---

## 2. Artifacts you need to rebuild

The following are generated at runtime / build time, so they are **not** shipped. You rebuild them with the commands below.

| Artifact | Rebuild command |
|---|---|
| BM25 index | `python scripts/build_fts_from_parquet.py --years 1991-1995` |
| Network graphs + gallery index | see §3 |
| `data/binary_gmax_*.json` (optional) | `python scripts/build_binary_gmax.py`. A 1.6KB snapshot **is** shipped; rebuild only if you extend the year range. |
| `data/category_map.duckdb` (optional) | **Shipped compressed** — see the data table below; unpack once with `tar xzf data/category_map.duckdb.tar.gz -C data/`. To rebuild from your own source DB instead: `python scripts/build_category_map.py` (needs a DB with an arXiv `categories` column; set `category_map_source_db` or env `SCI365_SOURCE_DB`). |

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

> **scripts/ 31 个 .py + web/ 2 个 + 3 模板**。跑通服务所需全部（scan → 标注 → 可视化 → G/R → 预测 → Web）。
>
> **v21（2026-08-31）**：已收编 Windows 独立复现发现的全部修复（详见 `docs/reproduction_report.md`）：
> `.gitignore` 与 `lib/chartjs` 恢复（二者此前只写在清单里、打包脚本从未复制），
> 新增 G 预测模块（`sci_*`），补 `ai_filter`（依赖闭合性自检发现的硬依赖）。

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
| `scripts/sci_g_series_fast.py` | G 预测 | G 序列快速抽取（`sci_*` 硬依赖）|
| `scripts/sci_rw_sampler.py` | G 预测 | 随机游走采样（桥接/命中判定）|
| `scripts/sci_rank_experiment.py` | G 预测 | 2015 快照排序实验 |
| `scripts/sci_rank_figs.py` | G 预测 | 排序实验出图 |
| `scripts/sci_gplots_50.py` | G 预测 | Top50 概念对 G 序列图 |
| `scripts/sci_predict2026_v3.py` | G 预测 | 2026 预测流水线 |
| `scripts/sci_backtest_2016.py` | G 预测 | 2016 回测 |
| `scripts/sci_backtest_fair.py` | G 预测 | 公平回测 |
| `scripts/sci_seeds_balanced.py` | G 预测 | 均衡种子选取 |
| `scripts/sci_llm_filter_2026.py` | G 预测 | LLM 过滤阶段 |
| `scripts/ai_filter.py` | 基础 | AI 词过滤（`sci_seeds_balanced` 硬依赖）|
| `scripts/build_category_map.py` | 领域归一 | 重建 data/category_map.duckdb（需源库）|
| `scripts/build_binary_gmax.py` | 领域归一 | 重建 data/binary_gmax_*.json |
| `lib/chartjs/chart.umd.min.js` | Web | 本地 Chart.js（离线图表，v21 前长期漏带）|
| `docs/reproduction_report.md` | 文档 | Windows 48h 复现报告 + v21 修复记录 |
| `web/explore.py` | Web | PaperExplore：焦点图 + 预印本面板 |
| `web/gallery.py` | Web | FocusView：逐年画廊 |
| `web/templates/explore.html` | Web | PaperExplore 页面 |
| `web/templates/distance.html` | Web | 距离页面 |
| `web/templates/gallery.html` | Web | 画廊页面 |
| `docs/parquet_format.md` | 文档 | parquet 格式说明 |
| `docs/pipeline_sop.md` | 文档 | 全流程运行手册 |
| `README.md` / `LICENSE` / `requirements.txt` / `.gitignore` | 工程 | 标准工程文件（`.gitignore` 已于 v21 恢复打包）|
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
| `data/category_map.duckdb.tar.gz` | 领域归一库压缩版（23MB，解压后 142MB）|

---

## 二、需自行重建的产物

以下为运行/构建时生成的产物，**不随包提供**，按下方命令自行重建。

| 产物 | 重建命令 |
|---|---|
| BM25 检索库 | `python scripts/build_fts_from_parquet.py --years 1991-1995` |
| 网络图 + 画廊索引 | 见 §三 |
| `data/binary_gmax_*.json`（可选）| `python scripts/build_binary_gmax.py`。1.6KB 的快照**已随包**，仅扩展年份时才需重建。|
| `data/category_map.duckdb`（可选）| **随包提供压缩版** —— 见下方数据表，解压一次即可：`tar xzf data/category_map.duckdb.tar.gz -C data/`。若要从自己的源库重建：`python scripts/build_category_map.py`（需带 arXiv `categories` 列的库，配 `category_map_source_db` 或环境变量 `SCI365_SOURCE_DB`）。|

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
