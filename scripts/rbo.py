"""Rank-Biased Overlap (RBO) 核心 —— 移植自 sci365/src/pipeline/rbo.py（逐行一致）。

RBO 用于桥接「不等长」的有序关键词序列：
  - 查询侧：LLM 编排的 ≤5 个学术短语（重要性降序，第一个最重要）
  - 文章侧：raw 标注的 phrases[]（标题+摘要命中顺序，均值 10-17 词）
特点：
  - 长度不齐天然兼容（持久化参数 p 按排名加权）。
  - 纯集合运算，不依赖任何向量 / embedding。
  - 确定性、可解释、可复现。
  - 命中顺序语义：标题先于摘要、先说的比后说的重要——顺序即重要度。

与 sci365 的差异：查询 7 词 → 本本项目 ≤5 短语；文章侧固定 50 词 → 本项目
直接用 raw 标注全文（均值 10-17 词，不截断）。p=0.9 沿用（头部敏感）。
"""
from __future__ import annotations


def rbo(list_a: list[str], list_b: list[str], p: float = 0.9) -> float:
    """Rank-Biased Overlap，返回 0..1 的相似度。

    list_a / list_b: 按排名排序的关键词序列（index 0 = 第 1 名）。
                     允许不等长；字符串可含多词短语（如 'large language model'）。
    p: 持久化参数，越大越看重头部排名，必须在 (0, 1)。

    公式（Webber et al. 2010）：
        RBO = (1-p) * Σ_{k=1..d} p^{k-1} * (|A_k ∩ B_k| / k)
    其中 d = max(len_a, len_b)，A_k = set(list_a[:k])（超出长度则取全集）。

    注意：RBO 对「等长且完全相同」的序列返回 1 - p^d（非 1.0），
    因其显式建模「截断尾部可能继续分歧」的不确定性。
    本项目真实场景是 ≤5 短语查询 vs 10-17 词文章：完美命中时
    RBO ≈ 该长度组合理论最大值（非 1.0），因文章尾部非查询词稀释了 agreement。
    RBO 仅用于排序，相对大小即相关度；若需 0-1 直观分用 max_rbo 归一化。
    """
    if not (0.0 < p < 1.0):
        raise ValueError(f"p 必须在 (0,1)，收到 {p}")
    d = max(len(list_a), len(list_b))
    if d == 0:
        # 两序列皆空：视为完全一致的退化情形
        return 1.0
    total = 0.0
    for k in range(1, d + 1):
        ak = set(list_a[:k])
        bk = set(list_b[:k])
        agreement = len(ak & bk) / k
        total += (p ** (k - 1)) * agreement
    return (1.0 - p) * total


def max_rbo(len_a: int, len_b: int, p: float = 0.9) -> float:
    """RBO 在「查询词全落在文章头部、尾部全为干扰词」时的理论最大值。

    用于把 raw RBO 分归一化到 0..1 直观显示：
        normalized = rbo(query, article) / max_rbo(len(query), len(article), p)
    排序仍用 raw RBO（不受归一化影响）。
    """
    if not (0.0 < p < 1.0):
        raise ValueError(f"p 必须在 (0,1)，收到 {p}")
    d = max(len_a, len_b)
    if d == 0:
        return 1.0
    total = 0.0
    for k in range(1, d + 1):
        total += (p ** (k - 1)) * (min(k, len_a) / k)
    return (1.0 - p) * total


def _self_test() -> None:
    # 1) 等长完全相同 → 1 - p^d（RBO 标准性质）
    ident = rbo(["a", "b", "c"], ["a", "b", "c"])
    assert abs(ident - (1 - 0.9 ** 3)) < 1e-9, ident
    # 2) 完全不交 → 0.0
    assert rbo(["a", "b"], ["x", "y", "z"]) == 0.0
    # 3) 头部重叠应高于尾部重叠（p=0.9 头部敏感）
    head = rbo(["a", "b", "c"], ["a", "x", "y"])
    tail = rbo(["a", "b", "c"], ["x", "y", "a"])
    assert head > tail, f"头部敏感失效：head={head} tail={tail}"
    # 4) 真实场景 query(5) vs article(15)：完美命中 = 理论最大值
    article = ["q1", "q2", "q3", "q4", "q5"] + [f"w{i}" for i in range(10)]
    query5 = ["q1", "q2", "q3", "q4", "q5"]
    perfect = rbo(query5, article)
    assert abs(perfect - max_rbo(5, 15, 0.9)) < 1e-9, perfect
    # 查询词全沉到文章尾部 → 明显更低但仍 > 0
    degraded = rbo(query5, [f"w{i}" for i in range(10)] + query5)
    assert perfect > degraded > 0.0, f"降级应更低：perfect={perfect} degraded={degraded}"
    # 5) 不等长部分命中 → 0<score<1
    score = rbo(["protein", "diffusion"], ["protein", "fold", "diffusion", "model", "x"])
    assert 0.0 < score < 1.0, f"不等长桥接异常：{score}"
    # 6) 多词短语按整体匹配（不应被拆开）
    single = rbo(["large language model"], ["large language model"])
    assert abs(single - (1 - 0.9)) < 1e-9, single
    assert rbo(["large language model"], ["large", "language", "model"]) == 0.0
    # 7) 命中顺序语义：标题先出现（前位）> 摘要后出现（后位）
    front = rbo(["black hole"], ["black hole", "gravity", "field"])
    back = rbo(["black hole"], ["gravity", "field", "black hole"])
    assert front > back, f"命中顺序敏感失效：front={front} back={back}"
    # 8) p 边界保护
    try:
        rbo(["a"], ["b"], p=1.0)
        raise AssertionError("p=1.0 应报错")
    except ValueError:
        pass
    print(f"OK rbo 单测通过：等长相同={ident:.3f} 不交=0.0 头部敏感=T "
          f"5vs15完美={perfect:.3f}(=理论最大) 降级={degraded:.3f} "
          f"顺序敏感=T 短语整体=T")


if __name__ == "__main__":
    _self_test()
