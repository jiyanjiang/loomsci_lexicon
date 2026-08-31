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
  python scripts/test_rbo.py                 # 全量（需 DeepSeek key，含真实 LLM 调用）
  python scripts/test_rbo.py --quick         # 跳过真实 LLM 调用（有 key 时仍测缓存）
  python scripts/test_rbo.py --query "..."   # 自定义查询跑全链路

无 key 时：T2/T3（LLM 编排与缓存）自动跳过并给出提示，T1/T4/T5/T6 照常执行。
项目本身不依赖 LLM —— 直接输入英文短语（逗号分隔）即可检索，详见 README §7。
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
from key_loader import has_api_key

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

    # T1 无需 key：直接输入英文短语（逗号分隔）即可检索。
    phrases = _t1_skip()

    # T2/T3 需要 DeepSeek key（LLM 把自然语言编排成短语）。
    # 无 key 时项目本身完全可用——直接用英文短语检索即可（见 README §7），
    # 因此这里跳过而非中断，否则 T4/T5/T6 永远跑不到。
    if not has_api_key():
        print("  [SKIP] T2/T3 LLM 编排与缓存（未配置 DeepSeek key）")
        print("         —— 不影响使用：无 key 时直接输入英文短语（逗号分隔）即可检索，")
        print("            例如 \"black hole, neutron star\"（详见 README §7）。")
        print("         —— 配置 key（env DEEPSEEK_API_KEY 或 config.yaml）后可跑全量测试。")
    elif args.quick:
        print("  [SKIP] T2 llm 模式（--quick）")
        _t3_cache()
    else:
        _t2_llm()
        _t3_cache()

    # T4/T5 测的是检索本身，必须用「样例年份真实存在」的概念。
    # 不可沿用 T1 的 transmon qubit / surface code —— 那是 2000 年代后的概念，
    # 在随包的 1991-1995 样例上零命中，会让 T4 必然 FAIL，
    # 给新用户造成"项目坏了"的错觉（实测：black hole/neutron star 命中 5 篇、50 组合）。
    sample_phrases = ["black hole", "neutron star"]
    _t4_correct(sample_phrases)
    _t5_speed(sample_phrases)
    _t6_robust()

    print("=" * 60)
    n_pass = sum(1 for _, s, _ in _results if s == PASS)
    n_fail = sum(1 for _, s, _ in _results if s == FAIL)
    print(f"结果: {n_pass} PASS / {n_fail} FAIL")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
