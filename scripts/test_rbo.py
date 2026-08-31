#!/usr/bin/env python3
"""RBO 检索自动测试：可用性 + 速度基准（用户 2026-08-13 标准：>8s 视为不可用）。

测试矩阵：
  [T1] skip 模式：纯英文逗号串（不调 LLM）
  [T2] llm 模式：中文自然语言（真实调 DeepSeek）
  [T3] cache 模式：重复查询命中缓存（应 <0.5s）
  [T4] 检索正确性：命中论文可回退到 arxiv_id + 概念组合非空
  [T5] 速度基准：编排 + 检索全链路 < 8s
  [T6] 端点健壮性：空查询 / 非法年份 / 超长查询 不崩溃

用法：
  python scripts/test_rbo.py                 # 全量（含真实 LLM 调用）
  python scripts/test_rbo.py --quick         # 只测 T1/T3/T4/T5（跳过真实 LLM）
  python scripts/test_rbo.py --query "..."   # 自定义查询跑全链路
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import orchestrate_query as oq
import search_rbo

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, str, str]] = []
SPEED_LIMIT = 8.0   # 秒（用户标准）


def check(cid: str, name: str, ok: bool, detail: str = ""):
    status = PASS if ok else FAIL
    _results.append((cid, status, name))
    print(f"  [{status}] {cid} {name}" + (f"  ({detail})" if detail else ""))


def _t1_skip():
    t0 = time.time()
    phrases, mode = oq.orchestrate_query("transmon qubit, surface code")
    dt = time.time() - t0
    ok = mode == "skip" and len(phrases) == 2 and dt < 1.0
    check("T1", "skip 模式（纯英文逗号串）", ok, f"mode={mode} dt={dt:.2f}s")
    return phrases


def _t2_llm():
    t0 = time.time()
    phrases, mode = oq.orchestrate_query("玻尔兹曼方程的微观机制")
    dt = time.time() - t0
    ok = mode in ("llm", "cache") and len(phrases) >= 1 and dt < SPEED_LIMIT
    check("T2", "llm 模式（中文自然语言→学术短语）", ok,
          f"mode={mode} phrases={phrases} dt={dt:.2f}s")
    return phrases


def _t3_cache():
    # 自预热：确保缓存存在（quick 模式跳过 T2 时，首次可能走 LLM，二次必须 cache）
    oq.orchestrate_query("玻尔兹曼方程的微观机制")
    t0 = time.time()
    phrases, mode = oq.orchestrate_query("玻尔兹曼方程的微观机制")
    dt = time.time() - t0
    ok = mode == "cache" and dt < 0.5
    check("T3", "cache 模式（重复查询命中缓存）", ok, f"mode={mode} dt={dt:.3f}s")
    return phrases


def _t4_correct(phrases):
    if not phrases:
        check("T4", "检索正确性", False, "无短语")
        return
    # 全量年份检索：quick 模式用 skip 短语（transmon qubit）在 1991-1992 无数据，
    # 须全量检索才能命中（现代概念集中在中后期）。
    t0 = time.time()
    out = search_rbo.search_rbo(phrases, years=None, top_n=5, with_pairs=True)
    dt = time.time() - t0
    ok = len(out["results"]) >= 1 and len(out["pairs"]) >= 1
    check("T4", "检索正确性（命中论文+概念组合）", ok,
          f"results={len(out['results'])} pairs={len(out['pairs'])} dt={dt:.2f}s")
    return out


def _t5_speed(phrases):
    t0 = time.time()
    out = search_rbo.search_rbo(phrases, years=None, top_n=10, with_pairs=True)
    dt = time.time() - t0
    ok = dt < SPEED_LIMIT
    check("T5", f"速度基准（全年份检索 < {SPEED_LIMIT}s）", ok, f"dt={dt:.2f}s")
    return out


def _t6_robust():
    # 空查询 → should_skip_llm False，走 LLM → 无 key 时明确报错（不崩溃）
    try:
        oq.orchestrate_query("")
        ok0 = False
    except SystemExit:
        ok0 = True
    # 超长查询（>50 字符）→ 应走 LLM 或明确失败，不抛异常
    try:
        oq.should_skip_llm("a" * 100)
        ok1 = True
    except Exception:
        ok1 = False
    # 非法年份 → search_rbo 空结果不崩溃
    try:
        out = search_rbo.search_rbo(["x"], years=[9999], top_n=3)
        ok2 = isinstance(out, list)
    except Exception:
        ok2 = False
    check("T6", "端点健壮性（空/超长/非法年份）", ok0 and ok1 and ok2,
          f"empty_skip={ok0} long_skip={ok1} bad_year={ok2}")


def main():
    ap = argparse.ArgumentParser(description="RBO 自动测试（可用性+速度）")
    ap.add_argument("--quick", action="store_true", help="跳过真实 LLM 调用")
    ap.add_argument("--query", default=None, help="自定义查询（跑全链路）")
    args = ap.parse_args()

    print("=" * 60)
    print(f"RBO 自动测试（速度标准 < {SPEED_LIMIT}s）")
    print("=" * 60)

    if args.query:
        t0 = time.time()
        phrases, mode = oq.orchestrate_query(args.query)
        dt = time.time() - t0
        print(f"  查询: {args.query} | mode={mode} | phrases={phrases} | dt={dt:.2f}s")
        out = search_rbo.search_rbo(phrases, years=None, top_n=10, with_pairs=True)
        print(f"  results={len(out['results'])} pairs={len(out['pairs'])} bridges={len(out['bridges'])}")
        print(f"  top: {out['results'][0]['arxiv_id'] if out['results'] else '-'}")
        return

    _t1_skip()
    _t2_llm() if not args.quick else print("  [SKIP] T2 llm 模式（--quick）")
    _t3_cache()
    phrases = _t2_llm() if not args.quick else _t1_skip()
    _t4_correct(phrases)
    _t5_speed(phrases)
    _t6_robust()

    print("=" * 60)
    n_pass = sum(1 for _, s, _ in _results if s == PASS)
    n_fail = sum(1 for _, s, _ in _results if s == FAIL)
    print(f"结果: {n_pass} PASS / {n_fail} FAIL")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
