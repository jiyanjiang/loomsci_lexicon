#!/usr/bin/env python3
"""短语形态归一单一工具（S7，2026-08-13 收敛）。

消除散布在 g_ab_calc / search_rbo / explore 的重复形态枚举，统一为：
  - variants(phrase)：标注数据中短语可能出现的 4 种形态（原形/(s)/es/(es)）
  - strip_plural(phrase)：去尾复数（es/s）后的 loose 候选（单复数归并）

调用点（统一收敛）：
  - g_ab_calc._resolve_variant_in_ann / preload_variants / _get_variant
  - explore._concept_binary_cs
  - search_rbo.phrase_match_status（loose 判定）
  - search_rbo._expand_query_a（A 侧扩展）
"""
from __future__ import annotations


def variants(phrase: str) -> list[str]:
    """标注数据中短语的 4 种形态候选：原形 / 加(s) / 加es / 加(es)。

    以 g_ab_calc._get_variant 的枚举为准（用户 2026-08-13 指定）：
      phrase, phrase + "(s)", phrase + "es", phrase + "(es)"
    返回去重保序列表。
    """
    p = phrase.strip()
    out: list[str] = []
    for v in (p, p + "(s)", p + "es", p + "(es)"):
        if v not in out:
            out.append(v)
    return out


def strip_plural(phrase: str) -> str | None:
    """去尾复数（es/s）后的候选。返回 None 表示无复数后缀。

    与 g_ab_calc / search_rbo 旧逻辑一致：
      - 尾 'es' 且长度>3 → 去 'es'（如 nickelates→nickelate）
      - 尾 's'  且长度>2 → 去 's'  （如 code→code? 尾s情况）
    仅返回一个候选（原逻辑即为单候选）。
    """
    p = phrase.strip().lower()
    if p.endswith("es") and len(p) > 3:
        return p[:-2]
    if p.endswith("s") and len(p) > 2:
        return p[:-1]
    return None


def loose_candidates(phrase: str) -> list[str]:
    """单复数归并的宽松候选集：去尾复数 + 4 形态枚举（去重）。

    用于 A 侧约束 / 存在性判定的统一集合构造。
    """
    out: list[str] = []
    for v in variants(phrase):
        if v not in out:
            out.append(v)
    s = strip_plural(phrase)
    if s and s not in out:
        out.append(s)
    return out
