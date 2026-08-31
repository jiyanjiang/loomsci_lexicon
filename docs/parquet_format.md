# arXiv Parquet Fact-Layer Format · arXiv Parquet 事实层格式说明

---

## English (EN)

> This document explains where the **input data** (fact-layer parquet) of
> loomsci_lexicon comes from and how to build it. The project itself does **not**
> ship the Kaggle raw JSONL parser — export from the public snapshot per this doc.

---

### 1. Data source

- **Raw data**: Kaggle dataset `Cornell-University/arxiv`
  (arXiv metadata snapshot, `arxiv-metadata-oai-snapshot.json`, ~5 GB).
- Fields are the official arXiv OAI metadata, one JSON object per paper per line.

### 2. Correspondence (Kaggle field → Parquet column)

| Parquet column | Type | Kaggle field | Note |
|---|---|---|---|
| `arxiv_id` | VARCHAR | `id` | natural unique key (e.g. `hep-th/9308004`, 2020+ `2104.13087`) |
| `submitter` | VARCHAR | `submitter` | submitter nickname |
| `authors` | VARCHAR | `authors` | author string (comma-separated) |
| `title` | VARCHAR | `title` | title |
| `comments` | VARCHAR | `comments` | comments (pages/review status etc.) |
| `journal_ref` | VARCHAR | `journal-ref` | journal reference (if any) |
| `doi` | VARCHAR | `doi` | DOI (if any) |
| `report_no` | VARCHAR | `report-no` | report number (if any) |
| `categories` | VARCHAR | `categories` | space-separated arXiv categories |
| `license` | VARCHAR | `license` | license |
| `abstract` | VARCHAR | `abstract` | abstract |
| `authors_parsed` | VARCHAR | `authors_parsed` | structured author list (JSON) |
| `versions` | VARCHAR | `versions` | version list (JSON) |
| `update_date` | VARCHAR | `update_date` | last update date |
| `submission_date` | DATE | derived from `versions[0].created` | first-version submission date |
| `year` | BIGINT | derived from `submission_date` | year (Hive partition key) |
| `content_hash` | VARCHAR | derived | `sha1(normalize(title) + '\n' + normalize(abstract))` |

**Core constraint** (this project only relies on these three columns; the rest are
retained as info):

```
Every row must have: arxiv_id (unique key), title, abstract
Recommended derived:  submission_date (first version), year (partition)
```

### 3. Hive partition layout

```
{papers_dir}/
├── year=1986/xxx.parquet
├── year=1988/xxx.parquet
├── year=1989/xxx.parquet
├── ...
└── year=2025/xxx.parquet
```

- Every row must contain `arxiv_id`, `title`, `abstract`.
- `year` partition matches the in-row `year` column.
- Empty years (e.g. 1987) may have no directory.

### 4. Lossless

This fact layer loses **no** Kaggle raw information: every Kaggle field is retained
(three renames: `id`→`arxiv_id`, `journal-ref`→`journal_ref`, `report-no`→`report_no`),
only adding derived `submission_date`/`year`/`content_hash`.

### 5. Size reference

| Year | # papers |
|---|---|
| 1991 | 353 |
| 1992 | 3,190 |
| ... | ... |
| 2025 | ~284K |

Full (1991–2025) ≈ **2.84M papers**. This repo ships the first 5 years
(`data/parquet/papers/year={1991..1995}/`, ~33K papers) as sample data for quick
reproduction.

### 6. Checklist

```python
import duckdb
con = duckdb.connect(":memory:")
# three-column existence
for col in ("arxiv_id", "title", "abstract"):
    print(col, con.execute(
        f"SELECT count(*) FROM read_parquet('{papers_dir}/year=1992/*.parquet') "
        f"WHERE {col} IS NOT NULL").fetchone()[0])
# row count matches expectation
print(con.execute(
    f"SELECT count(*) FROM read_parquet('{papers_dir}/year=1992/*.parquet')").fetchone()[0])
```

---

## 中文 (CN)

> 本文档说明 loomsci_lexicon 的**输入数据**（事实层 parquet）从何处来、如何构建。
> 项目本身**不包含** Kaggle 原始 JSONL 的解析程序——按本说明从公开快照导出即可。

---

### 1. 数据来源

- **原始数据**：Kaggle 数据集 `Cornell-University/arxiv`
  （arXiv 元数据快照，`arxiv-metadata-oai-snapshot.json`，约 5 GB）。
- 字段为 arXiv 官方 OAI 元数据，每行一篇论文的 JSON。

### 2. 对应关系（Kaggle 字段 → Parquet 列）

| Parquet 列 | 类型 | Kaggle 原始字段 | 说明 |
|---|---|---|---|
| `arxiv_id` | VARCHAR | `id` | 天然唯一键（如 `hep-th/9308004`，2020+ 为 `2104.13087`）|
| `submitter` | VARCHAR | `submitter` | 提交者昵称 |
| `authors` | VARCHAR | `authors` | 作者字符串（逗号分隔）|
| `title` | VARCHAR | `title` | 标题 |
| `comments` | VARCHAR | `comments` | 备注（页数/审稿状态等）|
| `journal_ref` | VARCHAR | `journal-ref` | 期刊引用（若有）|
| `doi` | VARCHAR | `doi` | DOI（若有）|
| `report_no` | VARCHAR | `report-no` | 报告编号（若有）|
| `categories` | VARCHAR | `categories` | 空格分隔的 arXiv 分类 |
| `license` | VARCHAR | `license` | 许可 |
| `abstract` | VARCHAR | `abstract` | 摘要 |
| `authors_parsed` | VARCHAR | `authors_parsed` | 结构化作者列表（JSON）|
| `versions` | VARCHAR | `versions` | 版本列表（JSON）|
| `update_date` | VARCHAR | `update_date` | 最近更新日期 |
| `submission_date` | DATE | 由 `versions[0].created` 派生 | 首版提交日期 |
| `year` | BIGINT | 由 `submission_date` 派生 | 年份（Hive 分区键）|
| `content_hash` | VARCHAR | 派生 | `sha1(normalize(title) + '\n' + normalize(abstract))` |

**核心约束**（本项目只依赖以下三列，其余为保留信息）：

```
每行必须含：arxiv_id（唯一键）、title、abstract
推荐派生：  submission_date（首版提交）、year（分区）
```

### 3. Hive 分区布局

```
{papers_dir}/
├── year=1986/xxx.parquet
├── year=1988/xxx.parquet
├── year=1989/xxx.parquet
├── ...
└── year=2025/xxx.parquet
```

- 每行必须含 `arxiv_id`, `title`, `abstract`。
- `year` 分区与行内 `year` 列一致。
- 空年（如 1987）可无目录。

### 4. 信息无损

本事实层**不损失 Kaggle 原始信息**：所有 Kaggle 字段均保留（`id`→`arxiv_id`，
`journal-ref`→`journal_ref`，`report-no`→`report_no` 三处改名），
仅新增派生的 `submission_date`/`year`/`content_hash`。

### 5. 数据规模参考

| 年份 | 论文数 |
|---|---|
| 1991 | 353 |
| 1992 | 3,190 |
| ... | ... |
| 2025 | 约 28.4 万 |

全量（1991–2025）约 **284 万篇**。本仓库 `data/parquet/papers/year={1991..1995}/`
提供前 5 年小样本（约 3.3 万篇）供快速复现。

### 6. 检查清单

```python
import duckdb
con = duckdb.connect(":memory:")
# 三列存在性
for col in ("arxiv_id", "title", "abstract"):
    print(col, con.execute(
        f"SELECT count(*) FROM read_parquet('{papers_dir}/year=1992/*.parquet') "
        f"WHERE {col} IS NOT NULL").fetchone()[0])
# 行数与预期一致
print(con.execute(
    f"SELECT count(*) FROM read_parquet('{papers_dir}/year=1992/*.parquet')").fetchone()[0])
```
