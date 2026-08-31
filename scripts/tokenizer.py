#!/usr/bin/env python3
"""统一分词器（pipeline2 全流程唯一分词源，v1, 2026-08-07）。

与 sci365/pipeline2/tokenizer.py 逐行一致（忠实移植，保证可复现）。

规则（2026-08-07 定稿，用户确认）：
| 符号 | 规则 | 示例 |
|---|---|---|
| 连字符 `-` | 任一侧含数字 → 保留原样；两侧皆字母 → 转空格 | 21-cm→21-cm；many-body→many body |
| 加号 `+` | 数字-数字 → 保留；其余 → 拆分 | 3+1→3+1；n+1→n 1 |
| 下划线 `_` | 去下划线直接合并 | T_c→tc；H_2O→h2o |
| 脱字符 `^` | 去脱字符直接合并 | H^eff→heff |
| 小数点 `.` | 整体含字母 → 保留原样；整体纯数字 → 拆后丢弃 | pm2.5→pm2.5；0.5→丢弃 |
| 斜杠 `/` | 拆分 | km/s→km s |
| 混排无符号 | 保留连写 | Bi2Se3→bi2se3；3D→3d |

- 纯数字 token 丢弃（纯数值非学术短语）。
- 全小写处理（大小写不区分语义）。
"""
import re

__version__ = "1.0.0"

# 希腊字母等特殊符号（判定用）
_GREEK = "σπγ⊕∞μλθφψαβδϵ"


def tokenize(s):
    """把一段文本（标题/摘要）切分为统一 token 列表。

    幂等：对同一输入始终返回相同 token 序列。scan 与标注共用。
    """
    if not s:
        return []
    s = s.lower()
    protected = {}

    def protect(m):
        ph = f"\x00{len(protected)}\x00"
        protected[ph] = m.group(0)
        return ph

    # 1. 保护连字符：任一侧含数字的 "-"
    s = re.sub(r"(?<=\d)-(?=\d|[a-z])|(?<=[a-z])-(?=\d)", protect, s)
    # 2. 保护加号：数字-数字 "3+1"
    s = re.sub(r"(?<=\d)\+(?=\d)", protect, s)
    # 3. 保护小数点：整体含字母的组合（pm2.5/2.5d/github.com/i.e）
    def protect_point(m):
        if re.search(r"[a-z]", m.group(0)):
            return protect(m)
        return m.group(0)
    s = re.sub(r"[a-z0-9]+\.[a-z0-9]+", protect_point, s)
    # 4. 下划线/脱字符：直接删除合并（T_c->tc, H^eff->heff）
    s = re.sub(r"[_^]", "", s)
    # 5. 剩余符号(未保护的- + . / ~) -> 空格
    s = re.sub(r"[-+./~]", " ", s)
    # 6. 恢复保护
    for ph, v in protected.items():
        s = s.replace(ph, v)
    # 7. 其他标点 -> 空格
    s = re.sub(r"[,;:()\"'\\${}\[\]%*]", " ", s)
    # 8. 过滤非法字符（保留 - + . 供被保护 token）
    s = re.sub(r"[^a-z0-9-+.]", " ", s)
    # 9. 丢弃纯数字 token（纯数值非学术）
    toks = [t for t in s.split() if not t.isdigit()]
    return toks


def tokenize_phrase(phrase):
    """把单个学术短语规范化为 token 序列（与 tokenize 同一套规则）。"""
    return tokenize(phrase)


if __name__ == "__main__":
    tests = [
        "21-cm line", "3-D reconstruction", "3-manifold", "1-form",
        "spin-1 particle", "covid-19", "gpt-3 model", "cifar-10",
        "2-dimensional", "6-dof", "1-1 correspondence", "3+1 decomposition",
        "many-body systems", "two-dimensional materials", "x-ray",
        "non-perturbative", "real-world", "state-of-the-art",
        "n+1", "au+au collision", "dft+u", "3+1",
        "T_c", "h_0 hamiltonian", "H_2O water", "lambda_c", "x_n",
        "0.5", "3.14 pi", "1.5 ev", "pm2.5", "2.5d", "i.e", "github.com",
        "and/or", "km/s", "1/2 spin",
        "Bi2Se3", "3D pose estimation", "Wigner 3j symbol", "H2O",
        "neutron star-black hole",
    ]
    for t in tests:
        print(f"{t!r:40} → {tokenize(t)}")
