#!/usr/bin/env python3
"""自然语言 → ≤5 个有序学术短语（LLM 编排，供 RBO 检索 / 焦点可视化）。

设计（用户 2026-08-13 确认）：
  - LLM 是必需的：自然语言 → 学术短语（DeepSeek v4 flash，便宜快速）
  - 跳过判断：输入已是「纯英文 + 逗号合理分割」的短语串 → 跳过 LLM 直接拆分
    （如 "transmon qubit, surface code"），省时省 token
  - API 服务形态：LLM 编排由用户侧自己调用（用户自己的 LLM 生成关键词），
    本项目只做检索端；本模块服务于本地 CLI 与 Web 的便捷入口
  - 输出 ≤5 个有序短语：重要性降序，第一个最重要（对齐焦点可视化上限 5）

用法：
  python scripts/orchestrate_query.py --query "量子纠错与表面码相关研究"
  python scripts/orchestrate_query.py --query "transmon qubit, surface code"   # 跳过 LLM
  python scripts/orchestrate_query.py --query "..." --pretty
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import dns_patch                      # DNS 韧性（磁盘缓存 + 智能重试），先于 requests
from key_loader import get_api_key, get_deepseek_config

import requests

MAX_PHRASES = 5

# 跳过判断：纯英文（可含数字/连字符）+ 逗号分割
_SKIP_RE = re.compile(r"^[A-Za-z0-9\s\-,.:']+$")

# LLM 编排缓存（JSON 持久化，跨重启复用；用户可分析自己的历史查询）
# B3（2026-08-13）：上限 MAX_CACHE_ENTRIES=500 + LRU 淘汰（超限删最旧），
#   防止 JSON 无限增长拖慢读写（用户提问：1000/1万条会如何 → 设上限保证速度）。
_CACHE_PATH = os.path.join(config.OUTPUT_DIR, "rbo_cache.json")
MAX_CACHE_ENTRIES = 500
_cache: dict | None = None          # {query: {"phrases": [...], "ts": epoch}}


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(_CACHE_PATH, encoding="utf-8") as f:
                data = json.load(f)
            _cache = data if isinstance(data, dict) else {}
            # 兼容旧格式（无 ts 字段的直接短语列表）
            for k, v in list(_cache.items()):
                if isinstance(v, list):
                    _cache[k] = {"phrases": v, "ts": 0}
        except Exception:
            _cache = {}
    return _cache


def _save_cache():
    try:
        Path(_CACHE_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass   # 缓存写失败不阻塞检索（best-effort）


def _evict_if_needed():
    """LRU 淘汰：超过上限时删除最旧的条目（保证 JSON 读写速度）。"""
    c = _load_cache()
    if len(c) <= MAX_CACHE_ENTRIES:
        return
    # 按 ts 升序（最旧在前），删到上限
    sorted_keys = sorted(c.keys(), key=lambda k: c[k].get("ts", 0))
    over = len(c) - MAX_CACHE_ENTRIES
    for k in sorted_keys[:over]:
        del c[k]


def cache_get(query: str):
    """缓存命中 → 返回短语列表；未命中 → None。"""
    c = _load_cache()
    v = c.get(query)
    if isinstance(v, dict):
        return list(v.get("phrases", [])) if v.get("phrases") else None
    return list(v) if v else None


def cache_put(query: str, phrases: list[str]):
    import time as _t
    c = _load_cache()
    c[query] = {"phrases": phrases, "ts": _t.time()}
    _evict_if_needed()
    _save_cache()


def cache_stats() -> dict:
    """缓存统计（D2 基础）：条目数 / 上限 / 最旧时间。"""
    c = _load_cache()
    ts_list = [v.get("ts", 0) for v in c.values() if isinstance(v, dict)]
    return {
        "entries": len(c),
        "max_entries": MAX_CACHE_ENTRIES,
        "oldest_ts": min(ts_list) if ts_list else None,
        "newest_ts": max(ts_list) if ts_list else None,
    }


def cache_entries() -> list[dict]:
    """缓存条目列表（B2：GET /cache/llm）。每条含 query / phrases / ts（epoch）。"""
    c = _load_cache()
    out = []
    for q, v in sorted(c.items(), key=lambda kv: kv[1].get("ts", 0) if isinstance(kv[1], dict) else 0):
        if isinstance(v, dict):
            out.append({"query": q, "phrases": v.get("phrases", []), "ts": v.get("ts", 0)})
        else:   # 旧格式（无 ts 的短语列表）
            out.append({"query": q, "phrases": list(v) if v else [], "ts": 0})
    return out


def cache_clear() -> int:
    """清空全部缓存（B2：DELETE /cache/llm）。返回清除条数。"""
    c = _load_cache()
    n = len(c)
    if n:
        c.clear()
        _save_cache()
    return n


def cache_remove(query: str) -> bool:
    """删除指定缓存条目（B2：DELETE /cache/llm/{key}）。返回是否删除。"""
    c = _load_cache()
    if query in c:
        del c[query]
        _save_cache()
        return True
    return False


def parse_phrases(comma_str: str, max_n: int = MAX_PHRASES) -> list[str]:
    """逗号分割 → 清理 → 截断到 max_n。"""
    out = [p.strip().lower() for p in comma_str.split(",") if p.strip()]
    return out[:max_n]


def should_skip_llm(query: str) -> bool:
    """跳过判断：纯英文 + 逗号合理分割（已是最佳形态）。"""
    q = query.strip()
    if not q:
        return False
    if not _SKIP_RE.match(q):
        return False
    parts = [p.strip() for p in q.split(",") if p.strip()]
    return 1 <= len(parts) <= MAX_PHRASES


_SYSTEM = (
    "你是科学文献检索的查询编排助手。把用户的自然语言研究兴趣，提炼为"
    f"最多 {MAX_PHRASES} 个学术短语（学术短语=在 arXiv 标题/摘要中常见的多词或单词术语）。\n"
    "规则：\n"
    "1. 输出恰好一个 JSON 对象：{\"phrases\": [\"短语1\", \"短语2\", ...]}\n"
    "2. 短语按重要性降序排列：第一个最重要（研究兴趣的核心概念）\n"
    f"3. 短语个数 1-{MAX_PHRASES}，越少越精准；只输出术语本身，不加解释\n"
    "4. 优先使用标准学术术语（如 'surface code' 而非 '量子纠错表面码'）\n"
    "5. 不要输出 JSON 之外任何文字"
)


def orchestrate_llm(query: str, key: str, cfg: dict, max_n: int = MAX_PHRASES) -> list[str]:
    """调用 DeepSeek 编排。返回有序短语列表（≤max_n）。

    关键坑（照搬 sci365 translate_query.py，2026-08-13 修）：
      1. v4 系列必须显式 thinking=disabled，否则 JSON 进 reasoning_content 致 content 空
         → JSONDecodeError（用户实测复现）
      2. content 可能被 ```json 代码块包裹，须剥离后再 json.loads
      3. content 可能为空 → 抛明确错误（不静默）
    """
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": query},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
        "max_tokens": 500,
    }
    # DeepSeek v4 系列：thinking 显式关闭（否则 JSON 进 reasoning_content）
    if "v4" in cfg["model"]:
        payload["thinking"] = {"type": "disabled"}

    resp = requests.post(cfg["base_url"], headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    msg = data["choices"][0]["message"]
    content = (msg.get("content") or "").strip()
    # 容错：content 空时尝试 reasoning_content（极端情况下 thinking 未生效）
    if not content:
        content = (msg.get("reasoning_content") or "").strip()
    if not content:
        raise RuntimeError("DeepSeek 返回空 content（模型未产出 JSON）")
    # 容错：剥掉可能残留的 ```json 代码块包裹
    if content.startswith("```"):
        content = content.strip("`")
        if content.lstrip().startswith("json"):
            content = content.lstrip()[4:]
    content = content.strip()
    parsed = json.loads(content)
    phrases = [p.strip().lower() for p in parsed.get("phrases", []) if p.strip()]
    return phrases[:max_n]


def orchestrate_query(query: str, use_llm: bool = True) -> tuple[list[str], str]:
    """总入口：返回 (有序短语, 模式)。模式 ∈ {skip, llm, cache, error}。

    cache = LLM 编排结果命中本地缓存（JSON 持久化，跨重启复用）。
    """
    q = query.strip()
    if not q:
        raise SystemExit("错误: 查询为空")
    if should_skip_llm(q):
        return parse_phrases(q), "skip"
    if not use_llm:
        raise SystemExit("错误: 输入非纯英文逗号串，且已禁用 LLM（--no-llm）")
    # 缓存命中：直接复用（不调 LLM，省时省 token）
    hit = cache_get(q)
    if hit:
        return hit[:MAX_PHRASES], "cache"
    key = get_api_key()
    if not key:
        raise SystemExit("错误: 需要 DeepSeek key（config.yaml 的 deepseek_api_key 或环境变量 DEEPSEEK_API_KEY）")
    cfg = get_deepseek_config()
    try:
        phrases = orchestrate_llm(q, key, cfg)
        cache_put(q, phrases)
        return phrases, "llm"
    except Exception as e:
        return [], f"error: {type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser(description="NL → ≤5 有序学术短语（LLM 编排）")
    ap.add_argument("--query", required=True, help="自然语言查询")
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--no-llm", action="store_true", help="禁用 LLM（仅跳过模式可用）")
    args = ap.parse_args()

    t0 = time.time()
    phrases, mode = orchestrate_query(args.query, use_llm=not args.no_llm)
    elapsed = time.time() - t0
    print(json.dumps({"query": args.query, "mode": mode,
                      "phrases": phrases, "elapsed_s": round(elapsed, 2)},
                     ensure_ascii=False, indent=2 if args.pretty else None))
    print(f"\n[mode] {mode} | {len(phrases)} 短语 | {elapsed:.2f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
