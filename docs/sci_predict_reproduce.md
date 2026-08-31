# G(AB,t) 科学方向涌现预测 · 完整复现指南

> 本模块（`scripts/sci_*.py`）从 arXiv 共现网络预测"未直连概念对的未来直连"。
> 本文档逐步说明每个脚本的输入、命令、输出与参数，确保可复现。

---

## 0. 前置数据（必须先准备）

| 数据 | 路径（相对包根） | 来源 |
|---|---|---|
| 逐年短语标注 | `data/annotation/normalized/year=YYYY/part-0.parquet` | 主链 `annotate.py` 产出 |
| 论文元数据 | `data/parquet/papers/year=YYYY/*.parquet` | 主链 parquet 管线产出 |

**说明**：分享包含 1991-1995 示例数据。完整复现需自行标注全量（主链 SOP）。
G 预测脚本读取 `data/annotation/normalized` 与 `data/parquet/papers`，
与主链共用数据目录，无需额外配置。

**运行环境**：Python 3.12+，依赖见 `requirements.txt`（duckdb / numpy / scipy /
matplotlib / requests）。LLM 环节需 DeepSeek key（`key_loader.py` 从环境变量
`DEEPSEEK_API_KEY` 读取）。

---

## 1. 平衡起点生成 `sci_seeds_balanced.py`

**作用**：按 arXiv 47 大类生成 147 个平衡 seed（AI 类仅 5%，跨学科去重 + 套话过滤）。

```bash
python scripts/sci_seeds_balanced.py
```

| 项 | 说明 |
|---|---|
| 输入 | `data/annotation/normalized/year=2015/part-0.parquet` + `data/parquet/papers/year=2015/*.parquet` |
| 输出 | `data/sci/discovery/randomwalk/seeds_balanced.json` |
| 关键参数 | 每学科取 top 2-4 个短语；频次 ≥20；AI 类限量 |

**产物示例**：`magnetic field(s)`（天体）、`superconductivity`（强关联）、
`string theory`（高能）、`qubit`（量子）、`chaos`（非线性）等 147 个。

---

## 2. 两跳采样 + 2015 回测 `sci_rw_sampler.py`

**作用**：从 147 个 seed 出发，两跳采样（seed→桥 w→候选 v，要求未直连），
算 G 波动性 CV，回测 2016-2025 是否直连且增长。

```bash
python scripts/sci_rw_sampler.py
```

| 项 | 说明 |
|---|---|
| 输入 | `seeds_balanced.json` + 2015 标注/论文数据 |
| 输出 | `data/sci/discovery/randomwalk/rw_candidates_2015.json`（600 候选 + 命中标记） |
| 命中定义 | 2016-2025 任一年 N≥5 且后段(2021-25)峰值 > 前段(2016-20)峰值 |
| 关键参数 | 桥过滤 `BRIDGE_STOP`（套话/停用词）；两跳强度 I = min(边权) |

**已知结果**：命中率 5.8%（41/230），CV≥0.6 子集 27%，I 5-40 最优。

---

## 3. 历史回测（真前验）`sci_backtest_2016.py`

**作用**：更严格的历史模拟——假装现在是 2016 年，只用 2010-2016 的 G 波动性
预测 2017-2025 是否直连。无法作弊（实际结果已存在）。

```bash
python scripts/sci_backtest_2016.py
```

| 项 | 说明 |
|---|---|
| 输入 | 2016 年标注 + 2010-2016 论文数据 |
| 输出 | `data/sci/discovery/backtest/backtest_2016.json`（400 候选 + 实际命中） |
| 关键参数 | `T0=2016`（假装时刻）、`PRED_WINDOW=(2017,2025)` |

**已知结果**：CV top 100 严格命中 33%（基线 12.8%，2.6 倍增益）；非 AI 内部 AUC=0.696。

---

## 4. 2026 预判 `sci_predict2026_v3.py`

**作用**：当前时刻 = 2025 底，用 2025 单年网络两跳采样 + G 波动性筛选 → Top 100。

```bash
python scripts/sci_predict2026_v3.py
```

| 项 | 说明 |
|---|---|
| 输入 | `seeds_balanced.json` + 2025 标注/论文数据 + 2010-2024（双重检查未直连） |
| 输出 | `data/sci/discovery/predict2026/predict2026_top100.json` |
| 关键参数 | CV 筛选 0.3-1.0（修正规律：中等最优）；I 筛选 5-40；每 seed 限 7 候选 |

**注意**：第 135 行 G 计算窗口须为 2010-2025（CV 需多时间点），网络用 2025 单年。

---

## 5. G 序列重排 `sci_g_series_fast.py`（被 4 调用）

**作用**：快速 G(AB,t) 序列（一次查询全取，避免逐年全表扫描）。

```python
from sci_g_series_fast import g_raw_series_fast
G = g_raw_series_fast("dark energy", "large language models (llms)", 2010, 2025)
# → {2010: ..., 2015: 841.7, ..., 2025: 1556.2}
```

| 参数 | 说明 |
|---|---|
| `A, B` | 概念短语（须在 `data/annotation/normalized` 的 phrases 中） |
| `y0, y1` | 年份窗口 |
| 返回 | `{year: G}`，G = 非归一化有效电导（拉普拉斯伪逆） |

---

## 6. LLM 筛选 + 三挡点评 `sci_llm_filter_2026.py`

**作用**：Top 100 → DeepSeek v4 pro 筛选 2 选 1 → 50；25 并发逐对点评（看好/中立/不看好）。

```bash
python scripts/sci_llm_filter_2026.py
```

| 项 | 说明 |
|---|---|
| 输入 | `data/sci/discovery/predict2026/gseries_2025_sorted.json`（需先运行 `sci_predict2026_gseries.py` 生成） |
| 输出 | `selected50.json` + `verdicts.json` + `llm_top50.html` |
| 需要 | `DEEPSEEK_API_KEY` 环境变量 |
| 关键参数 | `N_CONC=25`（并发数）；`temperature=0.3` |

> **前置步骤**：`sci_predict2026_v3.py` 输出 Top 100 后，需先计算 G(2015-2025) 序列
> 并按 G(2025) 降序（对应 `sci_predict2026_gseries.py` 逻辑），生成
> `gseries_2025_sorted.json` 才能喂给 LLM。

---

## 7. 图 + 形态分类 `sci_gplots_50.py`

**作用**：50 候选的 G(AB,t) 图 + 形态分类（单调上升/先升后降/波动）。

```bash
python scripts/sci_gplots_50.py
```

| 项 | 说明 |
|---|---|
| 输入 | `selected50.json` + `verdicts.json` + 各对 G 序列（内部重算） |
| 输出 | `data/sci/discovery/predict2026/gfigs/g01-g50.png` + `gfigs_50.html` |
| 分类 | 单调上升 / 先升后降(峰) / 波动 / 数据不足 |

---

## 8. 排序法则实验（2000 案例）`sci_rank_experiment.py`

**作用**：验证"G 降序排名 → 命中率"的核心判断算法，多种排序法则对比 + 形状定量化。

```bash
# 分批跑（每批 1000 对，约 4-8 分钟；>5 分钟阈值，分 2 批）
python scripts/sci_rank_experiment.py 0     # 前 1000 对 → rank_batch0.json
python scripts/sci_rank_experiment.py 1     # 后 1000 对 → rank_batch1.json
# 合并 + 画图（全英文注释）
python scripts/sci_rank_figs.py             # → rankfigs/*.png + rank_report.html
```

| 项 | 说明 |
|---|---|
| 输入 | `data/sci/discovery/randomwalk/cands_2000.json`（两跳采样候选） |
| 输出 | `rank_2000.json`（G 序列 + 形状特征 + 命中）+ `rankfigs/` |
| 排序法则 | G(2015) / G 均值 / G 峰值 / 尾部斜率 / G 波动 CV / 两跳 I |
| 形状特征 | FFT 主频（振荡周期）、线性增长 R²、峰位置、CV |
| 命中 | 2016-2025 直连且增长（后段峰值≥5 且后段>前段） |

**核心结果**：G(2015) Top100 命中 9.0%（基线 4.3%，≈2 倍）；G 波动 CV 是唯一显著
特征（AUC 0.612, p=0.0002），控制 G_mean 后仍显著（AUC 0.687, p=0.0001）；
两跳 I 反向（Top100 仅 1.5%）。

**对等回测（对照）** `sci_backtest_fair.py`：随机配对 + 同过滤，对照组 0% 命中
（600 对无一"直连且增长"）——两跳采样的领域交集先验是全部增益来源。

---

## 9. 完整流水线（一键）

```bash
# ① seed（一次性）
python scripts/sci_seeds_balanced.py

# ② 方法验证（可选，回测历史）
python scripts/sci_rw_sampler.py            # 2015 两跳采样 + 回测
python scripts/sci_backtest_2016.py         # 2016 历史模拟
python scripts/sci_backtest_fair.py         # 对等回测（随机对照）

# ③ 当前预判
python scripts/sci_predict2026_v3.py        # Top 100
python scripts/sci_predict2026_gseries.py   # G 序列 + G2025 降序（若未在 v3 内置）
python scripts/sci_llm_filter_2026.py       # LLM 筛选 50 + 三挡
python scripts/sci_gplots_50.py             # 50 图

# ④ 方法学验证（2000 案例排序实验）
python scripts/sci_rank_experiment.py 0     # 分批（0 和 1）
python scripts/sci_rank_experiment.py 1
python scripts/sci_rank_figs.py             # 排序实验图
```

---

## 10. 关键参数速查（回测验证）

| 参数 | 值 | 依据 |
|---|---|---|
| G 波动性 CV | 0.3-1.0（中等） | ✅ 已确认（2026-08-15 噪声对照：CV 与 G_mean 正相关 rho=+0.135，不遵循 1/√N 泊松指纹；控制 G_mean 后 AUC 0.687, p=0.0001） |
| 两跳强度 I | 5-40（弱-中最优） | 回测：>40 仅 2%（强桥=旧热点） |
| 命中定义 | N≥5 且后段峰>前段峰 | 用户 2026-08-15 定 |
| 平衡 seed | 147 个，AI 5% | 避免 AI+anything 平庸对主导 |
| 桥过滤 | BRIDGE_STOP 套话表 | 防垃圾对 + 防 G 虚高 |
