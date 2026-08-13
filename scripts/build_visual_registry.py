#!/usr/bin/env python3
"""构建可视化注册表 registry.csv。

从 data/visual/{static,speed,accel}/ 扫描已生成的图，按「目标参数规则」为每
年选择正确参数版本的文件名，写入 registry.csv。画廊直接读此文件挂载，
不依赖文件名推导。

参数规则（与 docs/visualization_params.md 一致）：
    1991: e5  w_cs=1（纯原始，无 _disc）
    1992: e10 w_cs=1
    1993-2016: e20 w_cs=1
    2017-2025: e20 w_cs=1（2017 后是否转 0.3 待定，先统一 1）
选择文件：优先「无 _disc 后缀」的纯原始版（w_cs=1 语义），且 e 匹配。
"""
import csv
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import config
# 图产物在 data/visual/（visualize.py 输出目录，S2 已复制到本地）
VISUAL_DIR = config.VISUAL_DIR
REGISTRY = os.path.join(ROOT, "data", "visual", "registry.csv")

# 年度主题（与 gallery.py YEAR_THEME 一致）
YEAR_THEME = {
    1991: "弦理论", 1992: "弦理论 · 标准模型", 1993: "二维物理 · 规范场论",
    1994: "标准模型确立", 1995: "标准模型主导", 1996: "标准模型 · 非线性动力学",
    1997: "M 理论 · 矩阵理论", 1998: "超对称黄金期", 1999: "真空能 · 宇宙学",
    2000: "二维材料酝酿", 2001: "凝聚态相变", 2002: "场论 · 凝聚态",
    2003: "宇宙学精确化 (WMAP)", 2004: "磁学 · 二维材料",
    2005: "星系质量 · 星系团", 2006: "星系演化启动", 2007: "二维材料",
    2008: "二维材料 · LHC 前夜", 2009: "复杂系统 · 石墨烯",
    2010: "石墨烯诺奖年", 2011: "量子信息 · LHC", 2012: "算法萌芽 · Higgs",
    2013: "Higgs 确认年", 2014: "网络科学", 2015: "量子科技",
    2016: "机器学习 · 拓扑诺奖", 2017: "Transformer 前夜", 2018: "深度学习登顶",
    2019: "表征学习", 2020: "ML 全面主导", 2021: "生成式 AI",
    2022: "LLM 元年", 2023: "ChatGPT 效应", 2024: "LLM 全面主导",
    2025: "LLM 深化 · 智能体",
}


def e_for_year(y):
    if y == 1991:
        return 5
    if y == 1992:
        return 10
    return 20


def wcs_for_year(y):
    """w_cs 参数（2026-08-10 用户定稿）：
    1991-2016: w_cs=1（纯原始，不传 --w-cs，文件无 _disc）
    2017-2021: w_cs=0.5（加权 _disc）
    2022-2025: w_cs=0.3（加权 _disc）"""
    if y <= 2016:
        return 1.0
    if y <= 2021:
        return 0.5
    return 0.3


def list_files(mode):
    pat = os.path.join(VISUAL_DIR, mode, "*.html")
    return [os.path.basename(f) for f in glob.glob(pat)]


def pick(mode, year):
    """从 mode 目录选 (year, e) 匹配的文件，优先纯原始（无 _disc）。"""
    want_e = e_for_year(year)
    if mode == "static":
        seg = str(year)
    elif mode == "speed":
        seg = f"{year-1}-{year}"
    else:
        seg = f"{year-2}-{year-1}-{year}"
    candidates = []
    for fn in list_files(mode):
        m = re.search(rf"network_{re.escape(seg)}_e(\d+)_t(\d+)", fn)
        if not m:
            continue
        e, topn = int(m.group(1)), int(m.group(2))
        if e != want_e or topn != 1500:
            continue
        candidates.append(fn)
    if not candidates:
        return ""
    # 按该年 w_cs 选择：w_cs<1 用加权 _disc 版；w_cs=1 用纯原始版
    want_wcs = wcs_for_year(year)
    if want_wcs < 1.0:
        disc = [c for c in candidates if "_disc" in c]
        return (disc[0] if disc else candidates[0])
    pure = [c for c in candidates if "_disc" not in c]
    return (pure[0] if pure else candidates[0])


def main():
    rows = []
    for y in range(1991, 2026):
        rows.append({
            "year": y,
            "e": e_for_year(y),
            "w_cs": wcs_for_year(y),
            "topn": 1500,
            "theme": YEAR_THEME.get(y, ""),
            "static_file": pick("static", y),
            "speed_file": pick("speed", y) if y >= 1992 else "",
            "accel_file": pick("accel", y) if y >= 1993 else "",
        })
    with open(REGISTRY, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    # 汇报
    missing = [(r["year"], k) for r in rows
               for k in ("static_file", "speed_file", "accel_file")
               if r[k] == ""]
    print(f"registry.csv 已写入 {len(rows)} 行")
    if missing:
        print(f"缺失 {len(missing)} 个: {missing[:10]}...")
    else:
        print("全部 35 年三图文件齐全 ✅")


if __name__ == "__main__":
    main()
