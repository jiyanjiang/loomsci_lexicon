# METHODS · Methodology of loomsci_lexicon

> Formal description of the scientific method implemented in this repository:
> data pipeline, concept-network construction, conductance G(AB), and the
> "AB-zoo" prediction framework (level + volatility dual-signal).
> Written 2026-08-15, reflecting the latest confirmed results (N=2000).




**Project Title: Effective Conductance in Diachronic Academic-Phrase Networks for Predicting Research Directions**

---

## English (EN)

### 1. Overview

`loomsci_lexicon` builds a **diachronic concept network** from the full arXiv
corpus (1991–2025, ~2.9M papers) and uses it to (a) visualize how scientific
concepts interact over time, and (b) **predict which not-yet-co-occurring
concept pairs (AB) will first co-occur in the future** — the "AB-zoo"
prediction framework.

The core quantitative object is the **effective conductance G(AB,t)** between
two concepts A and B at year t, defined even when A and B have never
co-occurred directly (N(AB)=0), via indirect paths A–C–B.

### 2. Data pipeline

| Stage | Script | Output |
|---|---|---|
| 0. Corpus | arXiv metadata (Kaggle snapshot) | `data/parquet/papers/` (Hive-partitioned by year) |
| 1. Phrase lexicon | `scan_year.py` → `build_lexicon.py` | academic phrase dictionary (RAW→BLACKLIST→WHITELIST→NEW-YEAR) |
| 2. Annotation | `annotate.py` | per-paper phrase lists → `data/annotation/normalized/year=YYYY/` |
| 3. Concept network | `build_visual_registry.py` | nodes = phrases, edges = co-occurrence counts |
| 4. G/R computation | `g_ab_calc.py` / `sci_g_series_fast.py` | G(AB,t) = 1/R_eff via Laplacian pseudoinverse |
| 5. Visualization | `visualize.py` | static/speed/accel focus graphs |
| 6. Prediction | `sci_*.py` (see §4) | candidate AB pairs + dual-signal scoring |

### 3. Effective conductance G(AB,t)

**Definition (circuit analogy).** Treat the concept network as a circuit board:
nodes = concepts, edges = co-occurrence in the same paper, edge weight =
co-occurrence count. Even when A and B share no direct edge, there exist
indirect paths A–C–B; multiple paths act as parallel resistors, so the
effective resistance R_eff(A,B) is finite and the **effective conductance**

    G(AB,t) = 1 / R_eff(A,B)

is well-defined. Computed via the graph Laplacian pseudoinverse:

    R_eff = L⁺[A,A] + L⁺[B,B] − 2·L⁺[A,B]        (L = degree − adjacency)

where L⁺ is the Moore–Penrose pseudoinverse of L. Neighborhood is truncated
to TOP_K = 20 per node per year. G is **non-normalized** raw conductance
(an "Ohm's law + parallel" quantity, not a correlation).

**Why G ≠ N.** N(AB,t) counts direct co-occurrences; G aggregates all
indirect paths. For N(AB)=0 pairs, G remains defined — this is G's unique
value: it quantifies *latent coupling* before any direct link.

### 4. Prediction framework (AB-zoo)

**Candidate generation (2-hop sampling).** Instead of enumerating all
N(AB)=0 pairs (combinatorially explosive), sample candidates as
seed → bridge w → candidate v, where seed is a balanced set of 147 core
concepts covering arXiv's 47 top-level categories (AI ≤ 5%), and v has not
co-occurred with seed in the prediction window (double-checked against the
2010–2014 window). Two-hop strength I = min(w(seed,w), w(w,v)) is recorded.

**Dual-signal scoring (confirmed 2026-08-15, N=2000).**

1. **Level signal.** G(2015) descending rank: Top-100 hit rate **9.0%**
   vs 4.3% baseline (≈2×). This is a *right-tail effect*: high G is a
   sufficient precondition for near-future linking, but low G does not
   preclude linking.

2. **Volatility signal.** CV = std(G)/mean(G) over the pre-link window.
   After controlling for G_mean, CV remains discriminative
   (AUC 0.687, p = 0.0001 in the G<20 bucket; overall AUC 0.612).
   **Noise control:** CV correlates *positively* with G_mean
   (Spearman rho = +0.135), not negatively as a Poisson 1/√N fingerprint
   would predict — G is an aggregated conductance, not a raw count, so its
   fluctuation mechanism differs from Poisson shot noise. CV is therefore a
   confirmed signal, not a statistical artifact.

The two signals are complementary: **G level = "how strong already"**,
**G volatility = "whether it is changing"** (indirect paths restructuring =
in preparation). A high-G & high-CV pair is the top-priority candidate.

**Two-hop strength I is NOT a ranking signal** — it is anti-correlated
(Top-100 by I hit only 1.5%; strong bridges are old hotspots).

### 5. Backtests

| Test | Window | Result |
|---|---|---|
| Random-walk 2015 (600) | 2015 → 2016-25 | 5.8% hit (linked & growing), random control 0% |
| True-prospective 2016 (400) | 2016 → 2017-25 | CV top-100 33% strict (baseline 12.8%, 2.6×) |
| Rank experiment (2000) | 2015 → 2016-25 | G(2015) Top-100 9.0% (baseline 4.3%) |

Hit definition (uniform): N(AB,y) ≥ 5 in some year 2021–2025 **and**
post-2021 peak > pre-2021 peak (linked & growing).

### 6. Honest limits

- 5.8% is the **first-pass pool hit rate**; in a real workflow humans/LLMs
  continue to filter the pool, raising the effective rate.
- The method exploits existing knowledge structure (low-development
  connections); it cannot predict paradigm revolutions beyond its
  representation ("exploration vs exploitation" boundary).
- G volatility is confirmed against Poisson noise, but its AUC (~0.61–0.69)
  is moderate; it should be combined with the level signal, not used alone.

---

## 中文 (CN)

**项目名称：历时学术短语网络的有效电导与新研究方向预判**

### 1. 项目概览

`loomsci_lexicon` 从全量 arXiv（1991–2025，约 290 万篇）构建**历时概念网络**，
用于：(a) 可视化科学概念随时间的相互作用；(b) **预测尚未共现的概念对 (AB)
未来是否会首次共现**——即"AB 动物园"预测框架。

核心定量对象是概念 A、B 在年份 t 的**有效电导 G(AB,t)**。即便 A、B 从未直接
共现（N(AB)=0），G 仍通过间接路径 A–C–B 有定义——这正是 G 的独特价值：
量化"尚未直连之前的潜在耦合"。

### 2. 数据管线

| 阶段 | 脚本 | 产物 |
|---|---|---|
| 0. 语料 | arXiv 元数据（Kaggle 快照） | `data/parquet/papers/`（按年 Hive 分区） |
| 1. 短语词典 | `scan_year.py` → `build_lexicon.py` | 学术短语词典（RAW→BLACKLIST→WHITELIST→NEW-YEAR） |
| 2. 标注 | `annotate.py` | 每篇短语列表 → `data/annotation/normalized/year=YYYY/` |
| 3. 概念网络 | `build_visual_registry.py` | 节点=短语，边=共现次数 |
| 4. G/R 计算 | `g_ab_calc.py` / `sci_g_series_fast.py` | G(AB,t) = 1/有效电阻（拉普拉斯伪逆） |
| 5. 可视化 | `visualize.py` | 静态/速度/加速度焦点图 |
| 6. 预测 | `sci_*.py`（见 §4） | 候选 AB 对 + 双信号打分 |

### 3. 有效电导 G(AB,t)

**定义（电路类比）**：把概念网络当作电路板——节点=概念，边=同篇共现，
边权=共现次数。即便 A、B 之间没有直接边，也存在 A–C–B 间接路径；多条
间接路径相当于并联电阻，因此有效电阻 R_eff(A,B) 有限，**有效电导**

    G(AB,t) = 1 / R_eff(A,B)

有定义。用图拉普拉斯伪逆计算：

    R_eff = L⁺[A,A] + L⁺[B,B] − 2·L⁺[A,B]        (L = 度矩阵 − 邻接矩阵)

L⁺ 是 L 的 Moore–Penrose 伪逆。每年每节点邻居截断为 TOP_K = 20。G 是
**非归一化**原始电导（"欧姆定律 + 并联"的物理量，不是相关性）。

**为什么 G ≠ N**：N(AB,t) 数直接共现；G 聚合全部间接路径。对 N(AB)=0 的
对，G 仍有定义——这是 G 的独特价值：在出现任何直连之前量化"潜在耦合"。

### 4. 预测框架（AB 动物园）

**候选生成（两跳采样）**：不穷举所有 N(AB)=0 对（组合爆炸），而是采样
seed → 桥 w → 候选 v。seed 是 147 个平衡核心概念（覆盖 arXiv 47 个大类，
AI 占比 ≤5%），v 与 seed 在预测窗口未共现（并用 2010–2014 窗口双重检查）。
记录两跳强度 I = min(边权(seed,w), 边权(w,v))。

**双信号打分（2026-08-15 确认，N=2000）**：

1. **水平信号**：G(2015) 降序排名，Top-100 命中率 **9.0%**（基线 4.3%，
   ≈2 倍）。这是**右尾效应**：G 高是将直连的充分前置条件，但 G 低不排除直连。

2. **波动信号**：CV = std(G)/mean(G)（直连前窗口）。控制 G_mean 后仍有判别力
   （G<20 档 AUC 0.687, p=0.0001；整体 AUC 0.612）。**噪声对照**：CV 与 G_mean
   **正相关**（Spearman rho=+0.135），而非泊松 1/√N 指纹所预期的负相关——G 是
   聚合电导而非原始计数，涨落机制不同于泊松散粒噪声。CV 确认为真实信号，
   非统计伪影。

两信号互补：**G 水平 = "已有多强"**，**G 波动 = "是否在变"**（间接路径在
重构 = 正在酝酿）。高 G 且高 CV 的对 = 最优先候选。

**两跳强度 I 不可用作排序信号**——反相关（按 I 排序 Top-100 仅命中 1.5%；
强桥是旧热点）。

### 5. 回测

| 测试 | 窗口 | 结果 |
|---|---|---|
| 随机行走 2015（600 对） | 2015 → 2016-25 | 命中 5.8%（直连且增长），随机对照 0% |
| 真前验 2016（400 对） | 2016 → 2017-25 | CV top-100 严格命中 33%（基线 12.8%，2.6 倍） |
| 排序实验（2000 对） | 2015 → 2016-25 | G(2015) Top-100 命中 9.0%（基线 4.3%） |

命中定义（统一）：2021–2025 某年 N(AB,y) ≥ 5 **且** 2021 后峰值 > 2021 前峰值
（直连且增长）。

### 6. 诚实边界

- 5.8% 是**第一轮筛选池**的命中率；真实工作流中人与 LLM 会继续过滤该池，
  有效成功率更高。
- 方法利用已有知识结构（低开发度的连接）；无法预测超越其表征的范式革命
  （"探索 vs 开发"边界）。
- G 波动性已排除泊松噪声，但其 AUC（约 0.61–0.69）中等；应与水平信号联合
  使用，不单独使用。

---

### 版本记录 (Changelog)

| 日期 | 变更 |
|---|---|
| 2026-08-15 | 双信号确认（2000 案例）：CV 经噪声对照实验确认非 1/√N 伪影；新增排序法则实验（`sci_rank_experiment.py`/`sci_rank_figs.py`/`sci_backtest_fair.py`）；METHODS.md 首次撰写 |
