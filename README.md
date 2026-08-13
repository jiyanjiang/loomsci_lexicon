# loomsci_lexicon · Visualize the Science

> **Diachronic, holistic, cross-disciplinary understanding of science — built from the full arXiv corpus.**

---

## English (EN)

### 0. Contact

- **Website**: https://LoomSci.com
- **Email**: qiji.list@gmail.com
- **WeChat**: ianwest

---

### 1. What this project is

`loomsci_lexicon` builds a **science-macro-understanding system** from the full arXiv corpus
(1991–2025, 2.84M papers). This repository ships the **first 5 years** (1991–1995, ~33K papers)
as sample data so the whole pipeline can run out of the box; the full corpus can be
rebuilt from the Kaggle snapshot — the official arXiv Dataset:
<https://www.kaggle.com/datasets/Cornell-University/arxiv>
(see `docs/parquet_format.md` for the exact schema mapping). The core chain:

```
parquet (fact layer, per-year partitions)
  → scan (academic phrase dictionary extraction)
  → annotate (per-paper phrase tagging, per-year slices = time dimension)
  → visualize (three-mode graphs: static / speed / accel + focus subgraphs)
  → G/R (conductance / resistance of a concept pair via effective resistance)
  → G-over-time evolution (cross-field fusion early-warning signals)
```

Key ideas:

- **Understanding > proving.** In the AI era, machines can prove and retrieve at scale;
  what is scarce is *understanding* — seeing the whole map, how concepts evolve,
  which cross-disciplinary couplings are heating up.
- **Concepts as atoms.** Academic phrases (e.g. `black hole`, `large language model`)
  extracted from titles + abstracts form the atomic units of the network.
- **Network + time.** Nodes = phrases, edges = co-occurring articles; per-year slicing
  makes concept evolution / dynamics observable.
- **G/R metric.** For a concept pair A,B: `G(A,B,t) = 1 / effective resistance`
  (all parallel paths via common neighbors), `R = 1/G`, result-normalized so the
  strongest pair each year = 1 (cross-year comparable). G is interpretable and
  needs no pre-trained embeddings.
- **Benchmark.** First-co-occurrence prediction AUC **0.848–0.870** vs. dynamic
  embedding 0.87 in arXiv:2411.06577 — on par, but fully explainable and two
  orders of magnitude cheaper.

### 2. Repository layout

> Full share boundary is in `SHARE_MANIFEST.md`; here grouped by function,
> reflecting **the actual contents of the share package**.

```
loomsci_lexicon/
├── README.md / LICENSE / requirements.txt
├── config.example.yaml        # empty template — copy to config.yaml (keys & abs paths go here)
├── SHARE_MANIFEST.md          # share / no-share boundary checklist (with reasons)
├── scripts/                   # 18 .py files (all shared, all needed to run the service)
│   ├── config & foundations
│   │   ├── config.py          # single-point config loader (reads config.yaml)
│   │   ├── key_loader.py      # DeepSeek key loader (env → config.yaml)
│   │   ├── tokenizer.py       # unified tokenizer (shared by all stages)
│   │   ├── phrase_forms.py    # phrase-form normalization (hard dep of g_ab_calc/search_rbo)
│   │   └── dns_patch.py       # DNS resilience (hard dep of orchestrate_query)
│   ├── ① lexicon pipeline
│   │   └── scan_year.py       # academic phrase dictionary extraction (per year)
│   ├── ② annotation
│   │   └── annotate.py        # per-paper phrase annotation → parquet
│   ├── ③ visualization
│   │   ├── visualize.py       # three-mode graphs + focus subgraphs
│   │   ├── build_fts_from_parquet.py  # FTS: build BM25 index from parquet
│   │   └── build_visual_registry.py   # rebuild gallery index data/visual/registry.csv
│   ├── ④ G/R core
│   │   ├── g_ab_calc.py       # G/R core library (effective resistance)
│   │   └── run_distance_batch.py  # G/R time series for concept-pair batches
│   ├── ⑤ retrieval chain
│   │   ├── rbo.py             # RBO ranking-similarity algorithm (pure, no I/O)
│   │   ├── search_rbo.py      # RBO semantic search (SQL prefilter + RBO rank, reads raw annotation)
│   │   ├── search_recommend.py# concept-pair recommender (raw prefilter + normalized aggregation)
│   │   ├── orchestrate_query.py  # query orchestration (NL→phrases + cache)
│   │   ├── fts_helper.py      # FTS(BM25) query helper (reads fts.duckdb)
│   │   └── test_rbo.py        # quick sanity check (6 items, zero-destructive)
├── web/
│   ├── explore.py             # PaperExplore: 3-mode search + gallery + focus + G/R pages
│   ├── gallery.py             # pure-function library (load_registry/focus_map, no Flask app)
│   └── templates/             # explore.html / gallery.html / distance.html
├── docs/
│   ├── parquet_format.md      # Kaggle → parquet correspondence (how to build the input)
│   └── pipeline_sop.md        # full pipeline runbook (scan → annotate → visualize → G/R)
└── data/                      # by_year full + lexicon_2025 + first-5-years parquet/annotation + stopwords + normalize tables
```

### 3. Prerequisites

- **macOS** (developed and tested on macOS; should work on Linux)
- **Python 3.10+** (tested on 3.12)
- venv recommended

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Configuration (IMPORTANT)

The project reads all paths from `config.yaml` at the repo root.

1. **Copy the template:**
   ```bash
   cp config.example.yaml config.yaml
   ```
2. **Edit `config.yaml`** if needed. Defaults already point to the repo's own data
   (the first 5 years 1991–1995 ship in `data/parquet/papers/`), so **no edit is
   required to run the demo**. Fill in when needed:
   - `papers_dir`: relative `data/parquet/papers` by default; set an absolute path
     only if you built your own full corpus (see `docs/parquet_format.md`).
   - `deepseek_api_key`: **optional** — only needed for query orchestration
     (concept-pair mode NL→phrases translation). The core pipeline (scan → annotate →
     visualize → G/R) and FTS / RBO retrieval run without any API key.
   - All other fields have sensible defaults relative to the repo.
3. **Never commit `config.yaml`.** It is already in `.gitignore`.

4. **Verify the installation** (quick sanity check, zero-destructive):
   ```bash
   python scripts/test_rbo.py --quick        # 6 checks: data/config/retrieval
   ```
   All PASS = environment is correct.

> Note: the empty template `config.example.yaml` has all keys blank / defaulted;
> it is the file you share publicly.

### 5. Run the core pipeline

```bash
# ① scan one year (e.g. 1992) → data/by_year/terms_1992_pipeline2.csv
python scripts/scan_year.py --year 1992

# ② annotate one year → data/annotation/normalized/year=1992/part-0.parquet
python scripts/annotate.py --years 1992 --normalize

# ③ visualize (three modes) — requires annotation output
python scripts/visualize.py --mode static --year 1992
python scripts/visualize.py --mode speed --target 1992 --base 1991
python scripts/visualize.py --mode accel --target 1992 --prev 1991 --base 1990

# ④ G/R for a concept pair (single-pair, ~2s for 35 years)
python -c "
import sys; sys.path.insert(0, 'scripts')
from g_ab_calc import compute_g_series
_, g = compute_g_series('attention mechanism', 'few shot', [2022, 2023, 2024, 2025])
print(g)
"

# ⑤ build the BM25 retrieval index (needed by the interactive pages)
python scripts/build_fts_from_parquet.py --years 1991-1995   # sample build (<30s)

# ⑥ interactive web (PaperExplore + gallery)
python web/explore.py --port 5010   # http://localhost:5010/explore  (gallery at /list)
```

> **Note on the gallery (`/list`):** the per-year network graphs
> (`data/visual/static|speed|accel/*.html`) and their index `data/visual/registry.csv`
> are **generated artifacts, not shipped** — rebuild them once from the shipped
> annotations + lexicon (a few minutes):
>
> ```bash
> for y in 1991 1992 1993 1994 1995; do
>   python scripts/visualize.py --mode static --year $y
>   python scripts/visualize.py --mode speed  --target $y --base $((y-1))
>   python scripts/visualize.py --mode accel  --target $y --prev $((y-1)) --base $((y-2))
> done
> python scripts/build_visual_registry.py     # writes data/visual/registry.csv
> ```
>
> `web/gallery.py` is a pure-function library (no Flask app) — all routes
> (including `/list`) live in `web/explore.py`.

### 6. Retrieval (three modes, all built into `explore.py`)

| mode | engine | data source | notes |
|---|---|---|---|
| Concept pairs | `search_recommend.py` | **raw prefilter + normalized aggregation** | LLM translation → concept-pair recommendations (Pairs / bridges) |
| FTS | `fts_helper.py` | `fts.duckdb` | BM25 exact-keyword search (index built by §5 step ⑤) |
| RBO | `search_rbo.py` | **raw annotation** | semantic search, RBO ranking |

**Retrieval-chain division of labor (which file does what):**
- `rbo.py` — **algorithm only**: computes RBO ranking similarity between two
  ranked lists (`rbo()` + `max_rbo()`). No data access, no I/O.
- `search_rbo.py` — **RBO search service**: NL→phrases→SQL prefilter (raw annotation)
  → RBO ranking→metadata.
- `search_recommend.py` — **concept-pair recommender**: outputs AB pairs and A-C/B
  bridges for the concept-pair tab. This is the file that "produces AB pairs & bridges".

> **How the concept-pair mode uses annotation (2-step)**: it first pre-filters
> candidate papers from **raw** annotation (finds papers containing any query phrase,
> loose match), then re-reads those papers' phrases from **normalized** annotation to
> aggregate AB pairs and A-C/B bridges (so `LLM`/`LLMs` are unified, counts are
> merged). Raw keeps recall, normalized keeps consistency — both are used.
> See `scripts/search_recommend.py` → `_load_norm_phrases` / `_aggregate_from_papers`.
>
> **Search scope note**: the RBO and concept-pair modes depend on annotation data.
> The share package ships the **first 5 years (1991-1995)** of annotation, so these
> two modes search those 5 years out of the box. Annotate more years via the
> pipeline and the scope expands automatically (no code change). The FTS mode covers
> whatever years you built with `build_fts_from_parquet.py`.

### 7. LLM usage

The only LLM call in this package is query orchestration (`orchestrate_query.py`):
the concept-pair mode translates a natural-language query into phrases via
DeepSeek (`deepseek-v4-pro`). The core pipeline (scan → annotate → visualize → G/R)
and FTS / RBO retrieval run without any API key. The key is read via
`key_loader.py` from `DEEPSEEK_API_KEY` env var, falling back to `deepseek_api_key`
in `config.yaml`. No key is hard-coded anywhere.

### 8. Reproducibility

- Random seeds fixed, LLM mapping table version-locked.
- `docs/pipeline_sop.md` documents every stage with commands.
- The scan step reproduces the published lexicon byte-for-byte
  (θ=0.3, freq_min=5, t_merge=3, max_merge_len=6).

---

## 中文 (CN)

> **从全量 arXiv 到科学的宏观理解：历时、整体、跨学科的科学可视化与演化分析。**


### 0. 联系方式

- **网站**：https://LoomSci.com
- **邮箱**：qiji.list@gmail.com
- **微信**：ianwest

---

### 1. 项目是什么

`loomsci_lexicon` 从全量 arXiv（1991–2025，284 万篇）构建**科学宏观理解系统**。
本仓库自带**前 5 年（1991-1995，约 3.3 万篇）**示例数据，开箱即可跑通全管线；
全量语料可从 Kaggle 快照重建——arXiv 官方数据集：
<https://www.kaggle.com/datasets/Cornell-University/arxiv>
（字段对应关系见 `docs/parquet_format.md`）。核心链条：

```
parquet（事实层，按年分区）
  → scan（学术短语词典提取）
  → 标注（逐篇短语标注，逐年分片 = 时间维度）
  → 可视化（三图：静态 / 速度 / 加速度 + 焦点子图）
  → G/R（概念对的电导 / 电阻，基于有效电阻）
  → G 随时间演化（跨领域融合早期预警信号）
```

核心思想：

- **理解 > 证明**：AI 时代机器可大规模证明与检索，稀缺的是*理解*——看见科学全貌、
  概念如何演化、哪些跨学科耦合在升温。
- **概念为原子**：从标题+摘要提取学术短语（如 `black hole`、`large language model`），
  作为网络的原子单位。
- **网络 + 时间**：节点=短语，边=共现文章；逐年分片使概念演化/动力学可观测。
- **G/R 度量**：概念对 A,B 的 `G(A,B,t) = 1 / 有效电阻`（经共同邻居的全部并联路径），
  `R = 1/G`，结果归一（当年最强对=1，跨年可比）。G 可解释、无需预训练向量。
- **对标结果**：首次共现预测 AUC **0.848–0.870** vs 文献 arXiv:2411.06577 动态嵌入 0.87
  ——性能持平，但完全可解释、计算量低两个数量级。

### 2. 目录结构

> 完整分享清单见 `SHARE_MANIFEST.md`；此处按功能分组，**反映分享包实际内容**。

```
loomsci_lexicon/
├── README.md / LICENSE / requirements.txt
├── config.example.yaml        # 空模板——复制为 config.yaml（key 与绝对路径都在这里填）
├── SHARE_MANIFEST.md          # 分享/不分享边界清单（含理由）
├── scripts/                   # 18 个 .py（全部分享，均为跑通服务所需）
│   ├── 配置与基础
│   │   ├── config.py          # 单点配置加载（读 config.yaml）
│   │   ├── key_loader.py      # DeepSeek key 加载（env → config.yaml）
│   │   ├── tokenizer.py       # 统一分词器（各阶段共用）
│   │   ├── phrase_forms.py    # 短语形态归一（g_ab_calc/search_rbo 硬依赖）
│   │   └── dns_patch.py       # DNS 弹性（orchestrate_query 硬依赖）
│   ├── ① 词典管线
│   │   └── scan_year.py       # 学术短语词典提取（按年，读 data/stop 停用词表）
│   ├── ② 标注
│   │   └── annotate.py        # 逐篇短语标注 → parquet
│   ├── ③ 可视化
│   │   ├── visualize.py       # 三图（static/speed/accel）+ 焦点子图
│   │   ├── build_fts_from_parquet.py  # FTS：从 parquet 构建 BM25 检索库
│   │   └── build_visual_registry.py   # 重建画廊索引 data/visual/registry.csv
│   ├── ④ G/R 核心
│   │   ├── g_ab_calc.py       # G/R 核心库（有效电阻，含 lexicon 匹配）
│   │   └── run_distance_batch.py  # 概念对批量 G/R 时间序列
│   ├── ⑤ 检索链
│   │   ├── rbo.py             # RBO 排名相似度算法（纯函数，无 I/O）
│   │   ├── search_rbo.py      # RBO 语义检索（SQL 预筛 + RBO 精排，读 raw 标注）
│   │   ├── search_recommend.py# 概念对推荐（raw 预筛 + normalized 聚合）
│   │   ├── orchestrate_query.py  # 查询编排（NL→短语 + 缓存）
│   │   ├── fts_helper.py      # FTS(BM25) 查询助手（读 fts.duckdb）
│   │   └── test_rbo.py        # 快速冒烟（6 项，零破坏）
├── web/
│   ├── explore.py             # PaperExplore：三档检索 + 画廊 + 焦点图 + G/R 页
│   ├── gallery.py             # 纯函数库（load_registry/focus_map，无 Flask app）
│   └── templates/             # explore.html / gallery.html / distance.html
├── docs/
│   ├── parquet_format.md      # Kaggle → parquet 对应关系（输入如何构建）
│   └── pipeline_sop.md        # 全流程运行手册（scan → 标注 → 可视化 → G/R）
└── data/                      # by_year 全量 + lexicon_2025 + 前5年 parquet/标注 + 停用词表 + 归一表
```

### 3. 环境要求

- **macOS**（开发与测试环境；Linux 应也可用）
- **Python 3.10+**（3.12 实测）
- 建议使用 venv

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 4. 配置（重要）

项目从仓库根目录的 `config.yaml` 读取所有路径。

1. **复制模板：**
   ```bash
   cp config.example.yaml config.yaml
   ```
2. **按需编辑 `config.yaml`**。默认值已指向仓库自带数据（前 5 年 1991-1995 在
   `data/parquet/papers/`），**跑示例无需修改**。需要时填写：
   - `papers_dir`：默认相对路径 `data/parquet/papers`；仅当你自建全量语料时
     改为绝对路径（见 `docs/parquet_format.md`）。
   - `deepseek_api_key`：**可选**——仅查询编排（概念对档 NL→短语翻译）需要。
     核心管线（scan → 标注 → 可视化 → G/R）与 FTS / RBO 检索无需任何 key 即可运行。
   - 其余字段均有合理的仓库内默认值。
3. **切勿提交 `config.yaml`**（已在 `.gitignore` 中）。

4. **验证安装**（快速冒烟，零破坏）：
   ```bash
   python scripts/test_rbo.py --quick        # 6 项检查：数据/配置/检索
   ```
   全部 PASS = 环境正确。

> 注：空模板 `config.example.yaml` 的 key 均留空 / 默认——这是你对外分享的文件。

### 5. 运行核心管线

```bash
# ① scan 单年（如 1992）→ data/by_year/terms_1992_pipeline2.csv
python scripts/scan_year.py --year 1992

# ② 标注单年 → data/annotation/normalized/year=1992/part-0.parquet
python scripts/annotate.py --years 1992 --normalize

# ③ 可视化（三模式）——需先有标注产物
python scripts/visualize.py --mode static --year 1992
python scripts/visualize.py --mode speed --target 1992 --base 1991
python scripts/visualize.py --mode accel --target 1992 --prev 1991 --base 1990

# ④ 概念对 G/R（单对，35 年约 2 秒）
python -c "
import sys; sys.path.insert(0, 'scripts')
from g_ab_calc import compute_g_series
_, g = compute_g_series('attention mechanism', 'few shot', [2022, 2023, 2024, 2025])
print(g)
"

# ⑤ 构建 BM25 检索索引（交互页检索依赖）
python scripts/build_fts_from_parquet.py --years 1991-1995   # 小样本构建（<30s）

# ⑥ 交互 Web（PaperExplore + 画廊）
python web/explore.py --port 5010   # http://localhost:5010/explore  （画廊在 /list）
```

> **画廊（/list）说明**：逐年网络图（`data/visual/static|speed|accel/*.html`）
> 与其索引 `data/visual/registry.csv` 是**生成产物，不随包分享**——用分享的
> 标注 + 词典一次性重建（约几分钟）：
>
> ```bash
> for y in 1991 1992 1993 1994 1995; do
>   python scripts/visualize.py --mode static --year $y
>   python scripts/visualize.py --mode speed  --target $y --base $((y-1))
>   python scripts/visualize.py --mode accel  --target $y --prev $((y-1)) --base $((y-2))
> done
> python scripts/build_visual_registry.py     # 生成 data/visual/registry.csv
> ```
>
> `web/gallery.py` 是纯函数库（无 Flask app）——所有路由（含 /list）都在
> `web/explore.py`。

### 6. 检索（三档，全部内置在 explore.py）

| 档位 | 引擎 | 数据源 | 特点 |
|---|---|---|---|
| 概念对 | `search_recommend.py` | **raw 预筛 + normalized 聚合** | LLM 翻译 → 概念组合推荐（Pairs/桥接） |
| FTS | `fts_helper.py` | `fts.duckdb` | BM25，精确关键词（索引由 §5 步骤⑤构建） |
| RBO | `search_rbo.py` | **raw 标注** | 语义检索，RBO 排序 |

**检索链分工（哪个文件干什么）**：
- `rbo.py` —— **纯算法**：计算两个排序列表的 RBO 相似度（`rbo()` + `max_rbo()`），不碰数据、无 I/O。
- `search_rbo.py` —— **RBO 检索服务**：NL→短语→SQL 倒排预筛（raw 标注）→RBO 精排→元数据。
- `search_recommend.py` —— **概念对推荐**：输出 AB 对 + AC-CB 桥接，服务"概念对"档位。
  **"出 AB 对和桥接对"的正式入口是这个文件**。

> **概念对档如何使用标注（两步）**：先用 **raw** 标注预筛候选论文（找含任一查询短语
> 的论文，宽松匹配），再对候选论文从 **normalized** 标注反查短语、聚合 AB 对与
> AC-CB 桥接（`LLM`/`LLMs` 归一，计数合并）。raw 保召回、normalized 保一致性——两者都用。
> 实现见 `scripts/search_recommend.py` → `_load_norm_phrases` / `_aggregate_from_papers`。
>
> **检索范围说明**：RBO 档与概念对档依赖标注数据，分享包只含 **1991-1995**
> （前 5 年示例），故这两档在分享数据上检索范围是前 5 年。按 §流程自行标注更多
> 年份后，检索范围自动扩展（无需改代码）。FTS 档则取决于 `build_fts_from_parquet.py`
> 构建了哪些年份。

### 7. LLM 使用

本包中唯一的 LLM 调用是查询编排（`orchestrate_query.py`）：概念对档将自然语言
查询翻译为短语（DeepSeek `deepseek-v4-pro`）。核心管线（scan → 标注 → 可视化 →
G/R）与 FTS / RBO 检索均无需任何 key。key 经 `key_loader.py` 从环境变量
`DEEPSEEK_API_KEY` 读取，回退到 `config.yaml` 的 `deepseek_api_key`。
代码中无任何硬编码 key。

### 8. 可复现性

- 随机种子固定，LLM 映射表版本锁定。
- `docs/pipeline_sop.md` 逐步记录了每个阶段的命令。
- scan 步骤可逐字节复现已发布词典（θ=0.3, freq_min=5, t_merge=3, max_merge_len=6）。
