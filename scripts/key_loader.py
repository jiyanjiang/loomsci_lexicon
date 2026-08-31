#!/usr/bin/env python3
"""loomsci_lexicon 统一 API key 加载器（对外分享脱敏版）。

本项目核心流程（scan → 标注 → 可视化 → G/R）不依赖 LLM；
仅「LLM 盘点筛选 / 噪音信号审核 / 深度报告」环节需要 DeepSeek key。

key 来源（优先级从高到低）：
  1. 环境变量 DEEPSEEK_API_KEY
  2. config.yaml 的 deepseek_api_key（若存在）

用法：
  from key_loader import get_api_key, has_api_key
  key = get_api_key()          # 无 key 时返回 None（不抛异常）
"""
from __future__ import annotations

import os
from pathlib import Path


def _find_config_yaml():
    """从脚本目录向上找 config.yaml。"""
    d = Path(__file__).resolve().parent
    for _ in range(4):
        p = d / "config.yaml"
        if p.exists():
            return p
        d = d.parent
    return None


def _load_config() -> dict:
    """读取 config.yaml（极简键值解析，零第三方依赖）。缺失返回空 dict。"""
    path = _find_config_yaml()
    if path is None:
        return {}
    cfg: dict = {}
    try:
        with open(path, encoding="utf-8") as f:
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


def get_api_key() -> str | None:
    """返回 DeepSeek API key；未配置返回 None。"""
    key = os.getenv("DEEPSEEK_API_KEY")
    if key:
        return key.strip()
    cfg = _load_config()
    k = cfg.get("deepseek_api_key")
    return (k or "").strip() or None


def has_api_key() -> bool:
    return get_api_key() is not None


def get_deepseek_config() -> dict:
    """返回 DeepSeek 调用参数（base_url / model）。"""
    cfg = _load_config()
    return {
        "base_url": cfg.get("deepseek_base_url", "https://api.deepseek.com/chat/completions"),
        "model": cfg.get("deepseek_model", "deepseek-v4-pro"),
    }


if __name__ == "__main__":
    print(f"has_api_key = {has_api_key()}")
    if has_api_key():
        k = get_api_key()
        print(f"key = {k[:6]}...{k[-4:]} (已脱敏)")
    print(f"config = {get_deepseek_config()}")
