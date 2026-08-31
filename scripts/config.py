"""
Single-point configuration for loomsci_lexicon.

路径从 config.yaml 读取（分享版）；若 config.yaml 不存在则回退默认值。
所有其他参数保持文档化默认值，除非你知道在做什么。

对外分享：config.yaml 不含个人路径（空模板见 config.example.yaml）。
核心流程（scan/标注/可视化/G/R）不依赖 API key。

配置格式：每行 "key: value"（键值间冒号+空格），支持 # 注释。
无需第三方 yaml 库（零依赖，便于分享环境）。
"""
import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_cfg() -> dict:
    """读取仓库根 config.yaml（极简键值解析）。缺失/失败返回空 dict。"""
    p = _REPO_ROOT / "config.yaml"
    if not p.exists():
        return {}
    cfg: dict = {}
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" not in line:
                    continue
                k, _, v = line.partition(":")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k:
                    cfg[k] = v
    except Exception:
        return {}
    return cfg


_CFG = _load_cfg()


def _path(key: str, default: str) -> str:
    """路径字段：config.yaml 优先；相对路径基于仓库根解析。"""
    v = _CFG.get(key)
    if not v:
        return default
    if not os.path.isabs(v):
        v = str(_REPO_ROOT / v)
    return v


# ============================================================
# PATHS - 从 config.yaml 读取（对外分享版）
# ============================================================

# arXiv parquet 事实层（Hive 按年分区：{PAPERS_DIR}/year=YYYY/*.parquet）
# 每行至少含 arxiv_id, title, abstract。获取方式见 docs/parquet_format.md
PAPERS_DIR = _path("papers_dir", str(_REPO_ROOT / "data" / "parquet" / "papers"))

# scan 产物（by_year/ -> terms_YYYY_pipeline2.csv）
SCAN_DIR = _path("scan_dir", str(_REPO_ROOT / "data" / "by_year"))

# 标注产物（raw / normalized）
ANN_RAW = _path("annotation_raw", str(_REPO_ROOT / "data" / "annotation" / "raw"))
ANN_NORM = _path("annotation_norm", str(_REPO_ROOT / "data" / "annotation" / "normalized"))

# 单复数/缩写跟随归一化表（随仓库提供）
NUMBER_NORMALIZE = _path("number_normalize", str(_REPO_ROOT / "data" / "number_normalize.csv"))
ABBREV_FOLLOW = _path("abbrev_follow", str(_REPO_ROOT / "data" / "abbrev_follow.csv"))

# 词典（G/R 计算依赖 data/cumulative/lexicon_*.csv）
LEXICON_DIR = _path("lexicon_dir", str(_REPO_ROOT / "data" / "cumulative"))

# LLM 单 token 白名单（标注可选依赖；无则只用多词短语）
ARXIV_TERMS_DIR = _path("arxiv_terms_dir", str(_REPO_ROOT / "data" / "arxiv_terms"))

# 手动白名单 / 后置黑名单（随仓库提供）
MANUAL_WHITELIST = _path("manual_whitelist", str(_REPO_ROOT / "data" / "whitelist_manual.txt"))
BLACKLIST_MANUAL = _path("blacklist_manual", str(_REPO_ROOT / "data" / "blacklist_manual.txt"))

# 可视化产物（visualize.py 输出三图目录）
VISUAL_DIR = _path("visual_dir", str(_REPO_ROOT / "data" / "visual"))
# 全年图产物（FocusView 画廊读取；可指向外部已生成全年图目录，留空回退 VISUAL_DIR）
VISUAL_FULL_DIR = _path("visual_full_dir", VISUAL_DIR)
# 预印本检索库（BM25 FTS，可选；空串则检索功能关闭）
FTS_DB = (_CFG.get("fts_db") or "").strip()

# 领域归一源库（build_category_map.py 一次性重建用；本机为 sci365 主库，分享模板留空）
CATEGORY_MAP_SOURCE_DB = (_CFG.get("category_map_source_db") or "").strip()

# 输出根目录（历史字段，保留兼容）
OUTPUT_DIR = str(_REPO_ROOT / "data")

# ============================================================
# STOP-WORD FILES（复现数据必须保持一致，勿改）
# ============================================================
STOP1_FILE = str(_REPO_ROOT / "data" / "stop" / "stop_list_1tok_dynB_v1.txt")
STOP2_FILE = str(_REPO_ROOT / "data" / "stop" / "stop_list_2tok_simple1000_v2.txt")
STOP3_FILE = str(_REPO_ROOT / "data" / "stop" / "stop_list_3tok_v2.txt")
BLACKLIST_FILE = str(_REPO_ROOT / "data" / "stop" / "blacklist_manual.txt")

# ============================================================
# SCAN PARAMETERS（保持已发布值，勿改）
# ============================================================
THETA = 0.3            # junction approval: adj/co ratio threshold
FREQ_MIN = 5           # minimum frequency floor (junction)
T_MERGE = 3            # candidate minimum co-occurrence count
MAX_ITER = 10          # max merge iterations
MAX_MERGE_LEN = 6      # max tokens in a merged phrase
N_KEYWORDS = 50        # seed tokens per paper (top-N by freq)

# ============================================================
# FOUNDING PERIOD (Year 0)
# ============================================================
FOUNDING_START = 1986
FOUNDING_END = 1991

# ============================================================
# DERIVED PATHS (do not edit)
# ============================================================
BY_YEAR_DIR = Path(SCAN_DIR)
CUMULATIVE_DIR = Path(LEXICON_DIR)
DELTA_DIR = Path(OUTPUT_DIR) / "delta"

# 创建输出目录（导入即建；只读目录不存在时也尝试建，避免 scan 报错）
for _d in (Path(BY_YEAR_DIR), Path(CUMULATIVE_DIR), Path(DELTA_DIR)):
    _d.mkdir(parents=True, exist_ok=True)

# ============================================================
# DATA-DRIVEN YEAR BOUNDS (S6, 2026-08-13)
# 年份边界一律从标注数据目录实测推导，消灭 1991/2025/2024/2026 硬编码。
# ============================================================


def annotation_years() -> list[int]:
    """扫描 normalized 标注目录（Hive year=YYYY 分区），返回升序年份列表。

    标注年份 = 数据驱动的统一事实源（G/R、焦点图、检索均依赖标注）。
    """
    years: list[int] = []
    ann = Path(ANN_NORM)
    if ann.is_dir():
        for d in ann.glob("year=*"):
            try:
                years.append(int(d.name.split("=", 1)[1]))
            except (ValueError, IndexError):
                continue
    return sorted(years)


def annotation_year_range() -> tuple[int, int] | None:
    """标注年份范围 (min, max)。无标注返回 None。"""
    ys = annotation_years()
    return (ys[0], ys[-1]) if ys else None


def latest_annotation_year() -> int | None:
    """最新标注年份（如 2025）。无标注返回 None。"""
    ys = annotation_years()
    return ys[-1] if ys else None
