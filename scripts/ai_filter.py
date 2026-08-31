# -*- coding: utf-8 -*-
"""AI/机器学习短语判定模块（2026-08-17）

用途：新采样流程（sci_cand_pool_noai.py）在 seed 与 B 两端排除 AI/ML 节点，
     使候选池 AI 占比趋向 0（用户容忍度=0）。

表来源：
- 保守表 = sci_backtest_2016.py / sci_stage3_scale.py 的 AI_TERMS（28 词，2026-08-14 起用）
- 宽表   = 保守表 + 中等词（embedding/language model/convolutional 等，2026-08-17 扩展）
注意：不含 learning/neural 等泛词（会误伤 transfer learning、neural oscillations 等非 AI 用法）。

用法：
  python scripts/ai_filter.py --check "machine learning" "dark matter"   # 判定
  python scripts/ai_filter.py --list                                      # 展示词表
"""
import argparse

AI_TERMS_CONSERVATIVE = [
    # 2026-08-14 起用于回测的 28 词保守表
    'machine learning', 'deep learning', 'neural network', 'transformer',
    'large language model', 'artificial intelligence', 'llm', 'reinforcement learning',
    'computer vision', 'representation learning', 'generative', 'attention',
    'natural language processing', 'deep neural network', 'self supervised',
    'fine tuning', 'pre trained', 'pre training', 'zero shot', 'encoder',
    'decoder', 'foundation model', 'downstream task', 'vision language',
    'image generation', 'diffusion model', 'training data', 'prompt',
]

# 中等词（阶段 B 备选）：不碰 learning/neural 泛词
AI_TERMS_MID = [
    'embedding', 'language model', 'convolutional', 'image classification',
    'object detection', 'semantic segmentation', 'multi agent', 'attention mechanism',
    'generative model', 'pretrained', 'self attention',
]

AI_TERMS_WIDE = AI_TERMS_CONSERVATIVE + AI_TERMS_MID

# 泛化修饰词（2026-08-17 用户定：energy efficient 类当 AI 词处理）
# 这些词是"低信息修饰"，几乎可与任何学术词组合，命中平庸无判别力
LOW_INFO_TERMS = [
    'energy efficient', 'energy efficiency', 'energy consumption', 'high performance',
    'high fidelity', 'high accuracy', 'high precision', 'low complexity', 'low energy',
    'computational cost', 'theoretical analysis', 'experimental data', 'high dimensional',
    'resource allocation', 'large scale', 'real time', 'real world', 'high quality',
    'state of the art', 'novel', 'various', 'multiple', 'different', 'significant',
    'efficient', 'robust', 'improve', 'enhance', 'comprehensive', 'extensive',
]


def is_ai(phrase, terms=None):
    """子串匹配判定短语是否 AI/ML 相关。terms=None 用保守表。"""
    if terms is None:
        terms = AI_TERMS_CONSERVATIVE
    pl = phrase.lower()
    return any(t in pl for t in terms)


def is_low_info(phrase, terms=None):
    """判定短语是否泛化修饰词（低信息，命中平庸）。terms=None 用 LOW_INFO_TERMS。"""
    if terms is None:
        terms = LOW_INFO_TERMS
    pl = phrase.lower()
    return any(t in pl for t in terms)


def filter_no_ai(phrases, terms=None):
    """过滤掉 AI 短语，返回非 AI 列表。"""
    return [p for p in phrases if not is_ai(p, terms)]


def main():
    ap = argparse.ArgumentParser(description="AI 短语判定工具")
    ap.add_argument("--check", nargs="+", default=[], help="判定这些短语是否 AI")
    ap.add_argument("--list", action="store_true", help="展示词表")
    ap.add_argument("--wide", action="store_true", help="用宽表(含中等词)")
    args = ap.parse_args()

    terms = AI_TERMS_WIDE if args.wide else AI_TERMS_CONSERVATIVE

    if args.list:
        print(f"词表 {len(terms)} 词 ({'宽表' if args.wide else '保守表'}):")
        for i, t in enumerate(terms, 1):
            print(f"  {i:2d}. {t}")
        return

    for ph in args.check:
        print(f"  {'AI' if is_ai(ph, terms) else '非AI':4s} | {ph}")


if __name__ == "__main__":
    main()
