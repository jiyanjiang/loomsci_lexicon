#!/usr/bin/env python3
# =====================================================================
# FocusView 2.0 · 纯函数库（S1，2026-08-13 收敛）
# 原独立 Flask app 的 4 个路由（/ /visual /focus /focus POST）与
# explore.py 完全重复且从未挂载（部署入口是 explore.py），已消除双份。
# 本文件只保留纯函数，由 explore.py 统一调用：
#   - load_registry / _year_modes / focus_map
#   - YEAR_PARAMS / YEAR_THEME（数据表，供 visualize 与 registry 构建）
# 画廊页面与焦点图路由均在 explore.py（/list /visual /focus /focus POST）。
# =====================================================================
"""FocusView 2.0 纯函数库：逐年画廊 registry 与焦点图生成。

被 explore.py 调用：
  - /list 路由  → load_registry()（画廊列表）
  - /focus POST → focus_map()（V2 焦点图全套三渲染）
"""
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import config
PIPELINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VISUAL_DIR = config.VISUAL_FULL_DIR                     # 全年图产物
ANNOT_DIR = config.ANN_NORM
FOCUS_OUT = os.path.join(config.VISUAL_DIR, "focus")    # 焦点图产物

# 逐年参数表（与 docs/visualization_params.md 一致）
YEAR_PARAMS = {
    1991: {"e": 5, "w_cs": 1, "topn": 1500},
    1992: {"e": 10, "w_cs": 1, "topn": 1500},
    1993: {"e": 20, "w_cs": 1, "topn": 1500},
}

# 年度主题（基于各年 Top 术语 + 新术语统计，2026-08-10 归纳）
YEAR_THEME = {
    1991: "弦理论",
    1992: "弦理论 · 标准模型",
    1993: "二维物理 · 规范场论",
    1994: "标准模型确立",
    1995: "标准模型主导",
    1996: "标准模型 · 非线性动力学",
    1997: "M 理论 · 矩阵理论",
    1998: "超对称黄金期",
    1999: "真空能 · 宇宙学",
    2000: "二维材料酝酿",
    2001: "凝聚态相变",
    2002: "场论 · 凝聚态",
    2003: "宇宙学精确化 (WMAP)",
    2004: "磁学 · 二维材料",
    2005: "星系质量 · 星系团",
    2006: "星系演化启动",
    2007: "二维材料",
    2008: "二维材料 · LHC 前夜",
    2009: "复杂系统 · 石墨烯",
    2010: "石墨烯诺奖年",
    2011: "量子信息 · LHC",
    2012: "算法萌芽 · Higgs",
    2013: "Higgs 确认年",
    2014: "网络科学",
    2015: "量子科技",
    2016: "机器学习 · 拓扑诺奖",
    2017: "Transformer 前夜",
    2018: "深度学习登顶",
    2019: "表征学习",
    2020: "ML 全面主导",
    2021: "生成式 AI",
    2022: "LLM 元年",
    2023: "ChatGPT 效应",
    2024: "LLM 全面主导",
    2025: "LLM 深化 · 智能体",
}
# 2006-2016 w_cs=1 延续（用户 2026-08-10 定：2017 再决定是否引入 0.3）
for _y in range(2006, 2017):
    YEAR_PARAMS[_y] = {"e": 20, "w_cs": 1, "topn": 1500}
# 2017 起待定（暂 w_cs=1 占位，用户将决定）
for _y in range(2017, 2026):
    YEAR_PARAMS[_y] = {"e": 20, "w_cs": 1, "topn": 1500, "pending_w": True}
# 1994-1996 已验证 e20 w_cs=1（2026-08-10 前拓）
for _y in range(1994, 1997):
    YEAR_PARAMS[_y] = {"e": 20, "w_cs": 1, "topn": 1500}
# 1997-2005 e20 w_cs=1 已定稿（2026-08-10 前拓验证，117-415 节点健康）
for _y in range(1997, 2006):
    YEAR_PARAMS[_y] = {"e": 20, "w_cs": 1, "topn": 1500}


def load_registry():
    """读 registry.csv（可视化注册表：年份/参数/各图文件名）。
    画廊挂载一律以此为准，文件名与参数天然一致。"""
    reg_path = os.path.join(ROOT, "data", "visual", "registry.csv")
    out = {}
    if not os.path.exists(reg_path):
        return out
    with open(reg_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[str(r["year"])] = {
                "e": r["e"], "w_cs": r["w_cs"], "topn": r["topn"],
                "theme": r["theme"],
                "static": r["static_file"], "speed": r["speed_file"],
                "accel": r["accel_file"],
            }
    return out


def _year_modes(year):
    """按数据可用性返回该年可生成的焦点图模式（含年段参数）。
    1991(创始)→static；1992→static+speed；1993+→static+speed+accel。"""
    if year <= 1991:
        return [{"mode": "static", "year": year}]
    if year == 1992:
        return [{"mode": "static", "year": year},
                {"mode": "speed", "target": year, "base": year - 1}]
    return [{"mode": "static", "year": year},
            {"mode": "speed", "target": year, "base": year - 1},
            {"mode": "accel", "target": year, "prev": year - 1, "base": year - 2}]


def focus_map(focus_words, year):
    """生成焦点图全套（static/speed/accel，按可用性）。返回 (success, result)。

    2026-08-11 优化：单子图三渲染——进程内 import visualize 一次构建
    YYYY 焦点子图，再依次渲染 static/speed/accel（三图结构绝对一致），
    取代旧的 3 次独立 subprocess（每次重建相同子图）。

    result = {files: {mode: html文件名}, year: y, focus: str}
    """
    os.makedirs(FOCUS_OUT, exist_ok=True)
    focus_str = ", ".join(focus_words)
    e = YEAR_PARAMS.get(year, {}).get("e", 5)
    topn = YEAR_PARAMS.get(year, {}).get("topn", 1500)
    modes = [spec["mode"] for spec in _year_modes(year)]
    try:
        _SCRIPTS = os.path.join(PIPELINE, "scripts")
        if _SCRIPTS not in sys.path:
            sys.path.insert(0, _SCRIPTS)
        import visualize as V
        V.ANN = ANNOT_DIR
        paths = V.export_focus_series(year, focus_str, min_edge=e, top_edges=topn,
                                      ui_name="standard", theme_name="starry",
                                      modes=modes)
    except Exception as exc:
        return False, f"焦点图生成异常: {exc}"
    if not paths:
        return False, "无焦点图生成（焦点词可能无效或无篇目）"
    files = {}
    for mode, src in paths.items():
        base_fn = os.path.basename(src)
        dst = os.path.join(FOCUS_OUT, base_fn)
        if os.path.abspath(src) != os.path.abspath(dst):
            os.system(f"cp '{src}' '{dst}'")
        files[mode] = base_fn
    return True, {"files": files, "year": year, "focus": focus_str}
