# Pipeline SOP · 全流程文档

---

## English (EN)

> This document walks you through the whole pipeline step by step: for each stage,
> first the idea, then the exact commands. All paths come from `config.yaml`; run
> commands at the repo root. Python 3.12 venv recommended
> (`pip install -r requirements.txt`).

---

### 0. Before you start — what you need

Two things:

1. **The raw papers** (the "fact layer"). We start from a folder of paper metadata
   (title + abstract of every arXiv paper, split into `year=YYYY` subfolders).
   The share package already includes the first 5 years (1991–1995, ~33K papers) at
   `data/parquet/papers/`. To get the full 1991–2025 corpus, download from
   <https://www.kaggle.com/datasets/Cornell-University/arxiv> and export to the same
   layout (see `docs/parquet_format.md`). Check where your data is:

   ```bash
   python -c "import sys; sys.path.insert(0,'scripts'); import config; print(config.PAPERS_DIR)"
   ls data/parquet/papers/year=1992/   # you should see *.parquet files
   ```

2. **A working Python environment**:

   ```bash
   pip install -r requirements.txt
   python scripts/test_rbo.py --quick    # 6 quick checks; all PASS = ready
   ```

---

### 1. scan — build the dictionary of academic phrases

**The idea:** an arXiv title/abstract is a string of words. But the meaningful unit is
not a single word — it's a *phrase*: `black hole`, `large language model`. This step
finds those phrases automatically. The trick: **words that keep appearing next to each
other** across many papers are probably one concept. `black` and `hole` sit side by
side thousands of times; `the` and `hole` never do. So we count, for every pair of
neighboring words, how often they occur together, and "fuse" the pairs that are stable
enough. Then we repeat, so a fused pair can grow into a longer phrase.

**First, each paper is reduced to its "top-word set".** We cannot count how often
every possible pair of words co-occurs — the vocabulary is far too big, the count
would be astronomically large. So the algorithm starts by summarizing each paper:
tokenize its title+abstract, count word frequencies (a plain TF count), drop
stop-words, and keep the **top-50 most frequent words** as that paper's seed set.
All co-occurrence statistics below are defined on these seed sets — a word pair
counts as "co-occurring" in a paper only if both words are in that paper's top-50.
This is not an extra rule; it is where the algorithm must start: computationally it
is the reduction that makes the counting feasible, and semantically a word that
appears often in a paper is exactly what "this paper is about that word" means.
Everything below builds on this seed set.

Three small knobs control "stable enough" (already tuned, don't change):

- `freq_min=5` — the junction (the two words touching) must be observed at least
  5 times.
- `θ=0.3` — two words co-occur in the same paper's top-word set (co), and sometimes
  they literally appear side by side in the text (adjacent, cadj). We accept the pair
  only when `cadj/co ≥ 0.3` — i.e. **of every 10 papers where both words appear, at
  least 3 have them right next to each other**. Below that, they are just two words
  that happen to share papers, not a phrase.
- `max_merge_len=6` — phrases grow to at most 6 words.

**Before scanning, remove the junk.** Abstracts are full of meaningless fragments
(`ability to the`, `et al phys rev`, `https github.com`). If we scanned as-is, these
would fuse into fake "terms". So three block-list files (in `data/stop/`, shipped with
the package) say "never treat these as terms":

| File | Size | How it was made |
|---|---|---|
| `stop_list_1tok_dynB_v1.txt` | 639 words | single words with no specific meaning, e.g. `the`, `using`. |
| `stop_list_2tok_simple1000_v2.txt` | 765 phrases | two-word fragments. An LLM suggested candidates, then a **human reviewed**. |
| `stop_list_3tok_v2.txt` | 1,324 phrases | three-word fragments. Found by scanning a no-STOP baseline (Jan 2021, freq≥5) and asking an LLM which ones were junk. |

**Do it.**

```bash
# one year
python scripts/scan_year.py --year 1992
# founding period (1986–1991 merged, 387 papers)
python scripts/scan_year.py --year 1986-1991
```

**Check:** you now have `data/by_year/terms_1992_pipeline2.csv` (the dictionary for 1992:
phrase, how many tokens, how often it appears). Its row count must match the shipped
one — the scan reproduces it exactly.

---

### 2. annotate — tag every paper with its phrases

**The idea:** now that we have a dictionary of phrases, the next step answers: *which
phrases does each paper use?* For every paper, we read its title+abstract, look up
each phrase of the dictionary in it, and record the ones found. The result is one row
per paper: `(arxiv_id, phrases_used[], ...)`. This is the input for everything that
follows (graphs, G/R, search).

To make sure we don't miss important terms, the dictionary is not just scan's output —
it's assembled from 6 layers, added in this order:

```
Layer 1  scan terms          the multi-word dictionary from step 1 (the base)
Layer 2  LLM single-token    (optional) data/arxiv_terms/single_tok_keep_final.txt
                             (high-value single words scan missed, e.g. `qubit`)
Layer 3  LLM multi-token     data/arxiv_terms/LLM_arxiv_multi_tokens_pub.csv
                             (merged from LLM review, 29,979 multi-word terms — always loaded)
Layer 4  manual whitelist    data/whitelist_manual.txt (terms a human rescued)
Layer 5  override whitelist  data/stop/whitelist_override.txt
                             (FORCED add — tagged even if not in the dictionary,
                              e.g. `state of the art performance`)
Layer 6  post blacklist      data/stop/blacklist_manual.txt
                             (FORCED remove — junk deleted last,
                              e.g. `et al phys rev`, `https github.com`)
```

Layers 5 and 6 are the strongest: 5 force-adds phrases scan missed, 6 force-removes
junk at the very end so it can't survive.

We also merge singular/plural and abbreviations, so `LLM` and `LLMs` count as the same
concept (files `number_normalize.csv` / `abbrev_follow.csv`).

**Do it.**

```bash
# one year (also normalizes singular/plural)
python scripts/annotate.py --years 1992 --normalize
# a range
python scripts/annotate.py --years 1992-1995 --normalize
```

**Check:** you now have `data/annotation/normalized/year=1992/part-0.parquet`
(one row per paper with its phrases). It must exist and be non-empty.

---

### 3. visualize — draw the concepts as a network

**The idea:** a phrase is a *node*; two phrases that appear in the same papers are
*connected*. The more often they co-appear, the stronger the connection. That gives a
network of concepts per year, drawn as a graph. Three views, each answering a
different question:

- **static** — the map of one year: what exists, what is connected.
- **speed** — how the map *changed* from last year: for each concept, its share of
  that year's papers vs. last year's. Growing = red, shrinking = blue. ("Share change"
  is measured on a log scale so a doubling and a halving look symmetric.)
- **accel** — how the *change itself* is changing over 3 years (second difference):
  accelerating = orange, decelerating = green.

The edge weight is a blend of "how often together" and "how surprising the
co-occurrence is" (PMI — two rare terms co-appearing is more meaningful than two common
ones): `score = 0.2·PMI + 0.8·log2(weight+1)`.

**Do it.**

```bash
python scripts/visualize.py --mode static --year 1992
python scripts/visualize.py --mode speed --target 1992 --base 1991
python scripts/visualize.py --mode accel --target 1992 --prev 1991 --base 1990
# focus on specific concepts (hop0/1/2 neighborhood)
python scripts/visualize.py --mode static --year 1992 --focuson "transmon, surface code" --nobackground
```

**Check:** each command writes an `.html` file under `data/visual/static|speed|accel/`.
These are interactive HTML (the graph renders live in the browser; no PNG, no base64
images — ~10-15 KB each). Rendering loads the vis-network library from a CDN, so the
browser needs internet access — same as clicking a result through to arXiv.

---

### 4. G/R — how tightly are two concepts coupled

**The idea:** take two concepts, say `algebraic geometry` and `stochastic process`.
Are they getting closer over time? We build a small network around them: the two
concepts plus their most frequent co-occurring neighbors (K of them, K=10 by default).
Then we measure the **effective resistance** between the two — a graph-theory quantity
that sums *all* paths between them, not just the direct link. Convert to
**conductance** G = 1/resistance: high G = tightly coupled. This is what "a field is
warming up" means — even before two fields directly connect, they get coupled through
intermediate concepts, and G rises.

**Why K doesn't matter** (K ∈ {10,15,20}): G is a *ratio* — we divide by the year's
strongest pair, computed on the same network size. So the absolute size cancels out;
we verified G with K=10/15/20 is nearly identical (corr > 0.99). We just use the
smallest (fastest).

**Do it.**

```bash
# one pair, all years (~2s)
python -c "
import sys; sys.path.insert(0,'scripts')
from g_ab_calc import compute_g_series
_, g = compute_g_series('attention mechanism', 'few shot', [2022,2023,2024,2025])
print(g)  # expect ≈ [0.21, 0.29, 0.48, 1.06]
"
# batch of pairs (try a small one first)
python scripts/run_distance_batch.py --limit 5
```

> Note: the optional LLM mapping table (`data/llm_mapping_*.json`) is not shipped.
> Without it, concepts resolve via plain dictionary matching — same behavior.

**How the result is normalized.** For each year we divide every pair's raw G by that
year's strongest pair, so the strongest pair = 1 every year and all years are
comparable.

---

### 5. Interactive web

**The idea:** all of the above, clickable in a browser.

**Do it.**

```bash
python web/explore.py --port 5010
```

- `/explore` — generate a focus graph, click a node or edge → the papers using it
  (FTS-ranked, 30/page).
- `/list` — the per-year gallery (static/speed/accel, 1991–2025).
- `/distance` — type two concepts, see their G/R time series with an alert when G
  crosses the threshold.

The gallery needs the graph artifacts from step 3 (they are generated, not shipped).
Build them once:

```bash
for y in 1991 1992 1993 1994 1995; do
  python scripts/visualize.py --mode static --year $y
  python scripts/visualize.py --mode speed  --target $y --base $((y-1))
  python scripts/visualize.py --mode accel  --target $y --prev $((y-1)) --base $((y-2))
done
python scripts/build_visual_registry.py    # writes data/visual/registry.csv (gallery index)
```

`web/gallery.py` is a pure-function library (no Flask app) — all routes live in
`web/explore.py`. Templates shipped: `explore.html`, `gallery.html`, `distance.html`.

---

### 6. Quick self-checks

| Stage | Command | Expected |
|---|---|---|
| config | `python -c "import sys;sys.path.insert(0,'scripts');import config;print(config.PAPERS_DIR)"` | prints your papers_dir |
| key | `python scripts/key_loader.py` | `has_api_key = True/False` |
| scan | `python scripts/scan_year.py --year 1992` | `terms_1992_pipeline2.csv` generated |
| annotate | `python scripts/annotate.py --years 1992 --normalize` | normalized parquet generated |
| G/R | the single-pair command above | `[0.21, 0.29, 0.48, 1.06]` magnitude |
| web | `python web/explore.py --port 5010` | `http://localhost:5010` 200 |

---

### 7. What this method is, and where it's going

- It gives **diachronic macro-understanding + concept-coupling awareness**, on par with
  the dynamic-embedding literature (AUC 0.848–0.870 vs 0.87) but fully explainable and
  ~100× cheaper.
- Scientific breakthroughs are inherently unpredictable — a statistical signal cannot
  see what does not exist yet. What this method offers is a **sniffer for the
  extrapolable part**: objective, interpretable coupling measures (G/R, speed/accel)
  that tell you *which cross-field couplings are warming up*. That is its value.
- **Forward-looking roadmap** — this is a long-lived, continuously evolving project;
  much work lies ahead:
  - Keep curating and improving the STOP lists and the academic-phrase whitelists —
    improving scan quality is the core of this project
  - Open-question annotation (an academic-phrase wiki)
  - Finer time slicing and more year windows
  - Rank-aware (not binary) coupling prediction
  - Interactive query tools on top of the G/R time series

---

## 中文 (CN)

> 本文档带你一步步走通全流程：每一步先讲思路，再给具体命令。所有路径来自
> `config.yaml`；命令在仓库根目录执行。建议 Python 3.12 venv
> （`pip install -r requirements.txt`）。

---

### 0. 开始之前——你需要什么

两样东西：

1. **原始论文数据**（"事实层"）。我们要从一个存有每篇 arXiv 论文元数据
   （标题+摘要，按 `year=YYYY` 子目录分年存放）的目录开始。分享包已自带前 5 年
   （1991-1995，约 3.3 万篇）在 `data/parquet/papers/`。要全量 1991-2025，从
   <https://www.kaggle.com/datasets/Cornell-University/arxiv> 下载并按同样布局导出
   （见 `docs/parquet_format.md`）。先确认你的数据在哪：

   ```bash
   python -c "import sys; sys.path.insert(0,'scripts'); import config; print(config.PAPERS_DIR)"
   ls data/parquet/papers/year=1992/   # 应看到 *.parquet 文件
   ```

2. **可用的 Python 环境**：

   ```bash
   pip install -r requirements.txt
   python scripts/test_rbo.py --quick    # 6 项快速检查；全 PASS 即就绪
   ```

---

### 1. scan —— 构建学术短语词典

**思路**：一篇 arXiv 摘要是一串单词，但有意义的单位不是单个词，而是*短语*：
`black hole`、`large language model`。这一步自动找出这些短语。诀窍是：**很多论文里
总挨着出现的词**，很可能是同一个概念。`black` 和 `hole` 挨着出现成千上万次；
`the` 和 `hole` 从不挨着。于是我们统计每对相邻词一起出现的次数，把足够稳定的
相邻词对"缝合"成短语；然后重复，缝合后的短语还能继续长成更长的术语。

**第一步：每篇论文先缩成"高频词集"。** 我们没法统计全词表里任意两两词的同现——
词表太大，算量是天文数字。所以算法先给每篇论文做"摘要"：把标题+摘要分词、做词频
统计（纯 TF 计数）、去掉停用词，**取出现最多的前 50 个词**作为这篇论文的种子集。
后面所有同现统计都定义在这个种子集上——只有两词同时进了某篇的 top-50，才在这篇
里记一次同现。这不是额外加的规则，而是算法必须从这里开始：计算上，它是让计数
可行的降维；语义上，"在论文里出现得多"正是"这篇论文在讲什么"的操作化定义。
后面的一切都建立在这个种子集上。

三个小旋钮控制"多稳定才算数"（已调好，勿改）：

- `freq_min=5` —— 两词的"接缝"（紧挨出现）至少观察到 5 次才考虑。
- `θ=0.3` —— 两词在**同一篇论文的高频词集**里同现（记 co），有时它们还会在
  **原文里紧挨着**出现（记 cadj）。只有当 `cadj/co ≥ 0.3` 时才接受这对词——
  即**两词每同现 10 篇，至少有 3 篇里它们是紧挨着的**。低于此，它们只是恰好
  出现在同一批论文里的两个词，不是短语。
- `max_merge_len=6` —— 短语最长长到 6 个词。

**扫描前先清垃圾**。摘要里满是无意义碎片（`ability to the`、`et al phys rev`、
`https github.com`）。不拦的话，它们会被缝成假"术语"。所以有三份黑名单文件
（在 `data/stop/`，随包分享），内容是"这些永远不要当术语"：

| 文件 | 规模 | 说明 |
|---|---|---|
| `stop_list_1tok_dynB_v1.txt` | 639 词 | 单个单词，无特定语义，如：`the`、`using` 等。|
| `stop_list_2tok_simple1000_v2.txt` | 765 条 | 两个词的碎片。先让 LLM 提议候选，再**人工复核**。|
| `stop_list_3tok_v2.txt` | 1,324 条 | 三个词的碎片。从一次无黑名单的基线（2021-01，freq≥5）里找出来，再让 LLM 判断哪些是垃圾。|

**动手**。

```bash
# 单年
python scripts/scan_year.py --year 1992
# 创始期（1986-1991 合并，387 篇）
python scripts/scan_year.py --year 1986-1991
```

**检查**：你得到了 `data/by_year/terms_1992_pipeline2.csv`（1992 年的词典：短语、几个词、
出现多少次）。行数必须与随包的一致——scan 可精确复现。

---

### 2. annotate —— 给每篇论文打上短语标签

**思路**：有了短语词典，下一步回答：*每篇论文用了哪些短语？* 对每篇论文，读它的
标题+摘要，在词典里逐条比对，记下命中的短语。结果每篇一行：
`(arxiv_id, phrases_used[], ...)`。这是后面所有环节（图、G/R、检索）的输入。

为了不漏掉重要术语，词典不只是 scan 的输出，而是按顺序由 6 层拼出来的：

```
第 1 层  scan 术语          第 1 步的多词词典（基础）
第 2 层  LLM 单词白名单    （可选）data/arxiv_terms/single_tok_keep_final.txt
                           （救回 scan 漏掉的高价值单词，如 `qubit`）
第 3 层  LLM 多词白名单    data/arxiv_terms/LLM_arxiv_multi_tokens_pub.csv
                           （LLM 审阅合并，29,979 个多词词条——始终读入）
第 4 层  手工白名单         data/whitelist_manual.txt（人工复核救回的概念）
第 5 层  override 白名单    data/stop/whitelist_override.txt
                           （强制加——即使词典里没有也照常标注，
                            如 `state of the art performance`）
第 6 层  后置黑名单         data/stop/blacklist_manual.txt
                           （强制删——最后精确剔除，
                            如 `et al phys rev`、`https github.com`）
```

第 5-6 层最强：5 强制加入 scan 漏掉的短语，6 在最后强制清掉垃圾、让它无处存活。

还会做单复数/缩写归并，让 `LLM` 和 `LLMs` 算同一个概念
（文件 `number_normalize.csv` / `abbrev_follow.csv`）。

**动手**。

```bash
# 单年（同时做单复数归一）
python scripts/annotate.py --years 1992 --normalize
# 区间
python scripts/annotate.py --years 1992-1995 --normalize
```

**检查**：得到 `data/annotation/normalized/year=1992/part-0.parquet`
（每篇论文一行，含它的短语）。必须存在且非空。

---

### 3. visualize —— 把概念画成网络

**思路**：短语是*点*；在同一批论文里出现的两个短语*相连*。共现越多，连线越粗。
于是每年得到一张概念网络，画成图。三种视图，各回答一个问题：

- **static** —— 一年的地图：有什么、连着什么。
- **speed** —— 比去年*变了多少*：每个概念今年占的论文份额 vs 去年。涨=红、
  缩=蓝。（"份额变化"用对数刻度，翻倍和减半看起来对称。）
- **accel** —— *变化本身*在 3 年里怎么变（二阶差）：加速=橙、减速=绿。

边权是"共现多不多"和"共现惊不惊喜"的混合（PMI——两个罕见词共现比两个常见词
共现更有意义）：`score = 0.2·PMI + 0.8·log2(weight+1)`。

**动手**。

```bash
python scripts/visualize.py --mode static --year 1992
python scripts/visualize.py --mode speed --target 1992 --base 1991
python scripts/visualize.py --mode accel --target 1992 --prev 1991 --base 1990
# 聚焦特定概念（hop0/1/2 邻域）
python scripts/visualize.py --mode static --year 1992 --focuson "transmon, surface code" --nobackground
```

**检查**：每条命令在 `data/visual/static|speed|accel/` 下生成一个 `.html` 文件。
这些是交互式 HTML（浏览器现场渲染图；无 PNG、无 base64 图片——每个约 10-15 KB）。
渲染需联网加载 vis-network 库，浏览器需要联网——与点击结果跳转 arXiv 同理。

---

### 4. G/R —— 两个概念的耦合有多紧

**思路**：取两个概念，比如 `algebraic geometry` 和 `stochastic process`。它们随时间
在靠近吗？我们在它们周围建一个小网络：两个概念 + 它们最常见的共现邻居（取 K 个，
默认 K=10）。然后测两者之间的**有效电阻**——一个图论量，累加两点之间的*所有*
路径，不只是直接相连那条。再转成**电导** G = 1/电阻：G 高 = 耦合紧。这就是"某
领域在升温"的含义——两个领域甚至还没直接相连时，就已经通过中间概念耦合起来，
G 就会上升。

**为什么 K 无所谓**（K ∈ {10,15,20}）：G 是*比值*——我们要除以当年的最强对，
而它是在同一网络规模上算的。所以绝对大小互相抵消；我们实测 K=10/15/20 时
G 几乎一致（相关 > 0.99）。就用最小的（最快）。

**动手**。

```bash
# 单对、全部年份（约 2 秒）
python -c "
import sys; sys.path.insert(0,'scripts')
from g_ab_calc import compute_g_series
_, g = compute_g_series('attention mechanism', 'few shot', [2022,2023,2024,2025])
print(g)  # 期望 ≈ [0.21, 0.29, 0.48, 1.06]
"
# 批量（先小批量试跑）
python scripts/run_distance_batch.py --limit 5
```

> 注：可选的 LLM 映射表（`data/llm_mapping_*.json`）不随包提供。没有它，概念走
> 普通词典匹配解析——行为不变。

**结果怎么归一**：每年把每个原始 G 除以当年的最强对，于是最强对每年 = 1，
所有年份可比。

---

### 5. 交互 Web

**思路**：以上全部，在浏览器里点开就能用。

**动手**。

```bash
python web/explore.py --port 5010
```

- `/explore` —— 生成焦点图，点节点/边 → 用它的论文（FTS 排序，30/页）。
- `/list` —— 逐年画廊（static/speed/accel，1991-2025）。
- `/distance` —— 输入两个概念，看 G/R 时间序列，G 越阈值时预警。

画廊需要第 3 步的图产物（生成物，不随包）。先建一次：

```bash
for y in 1991 1992 1993 1994 1995; do
  python scripts/visualize.py --mode static --year $y
  python scripts/visualize.py --mode speed  --target $y --base $((y-1))
  python scripts/visualize.py --mode accel  --target $y --prev $((y-1)) --base $((y-2))
done
python scripts/build_visual_registry.py    # 生成 data/visual/registry.csv（画廊索引）
```

`web/gallery.py` 是纯函数库（无 Flask app）——所有路由都在 `web/explore.py`。
随包模板：`explore.html`、`gallery.html`、`distance.html`。

---

### 6. 快速自检

| 环节 | 命令 | 预期 |
|---|---|---|
| 配置 | `python -c "import sys;sys.path.insert(0,'scripts');import config;print(config.PAPERS_DIR)"` | 输出你的 papers_dir |
| key | `python scripts/key_loader.py` | `has_api_key = True/False` |
| scan | `python scripts/scan_year.py --year 1992` | `terms_1992_pipeline2.csv` 生成 |
| 标注 | `python scripts/annotate.py --years 1992 --normalize` | normalized parquet 生成 |
| G/R | 上面单对命令 | `[0.21, 0.29, 0.48, 1.06]` 量级 |
| web | `python web/explore.py --port 5010` | `http://localhost:5010` 200 |

---

### 7. 这个方法是什么，以及它走向哪里

- 它提供**历时宏观理解 + 概念耦合态势感知**，与动态嵌入文献可比
  （AUC 0.848–0.870 vs 0.87），但完全可解释、便宜约两个数量级。
- 科学突破本质上不可预测——统计信号看不见尚不存在之物。本方法提供的是
  **对可外推部分的嗅探手段**：客观、可解释的耦合度量（G/R、speed/accel），
  告诉你*哪些跨领域耦合在升温*。这就是它的价值。
- **面向未来的持续迭代方向**——这是一个长期演进、持续迭代的项目，后面有大量
  工作值得做：
  - 继续编纂优化 STOP 词表、学术短语白名单等，提高 scan 质量是本项目的核心
  - 开放问题标注（学术短语 Wiki）
  - 更细的时间切片、更多年份窗口
  - 排名感知（而非二元）的耦合预测
  - 基于 G/R 时间序列的交互式查询工具
