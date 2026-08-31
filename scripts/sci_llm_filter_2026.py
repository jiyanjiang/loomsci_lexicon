# -*- coding: utf-8 -*-
"""2026 预判 LLM 筛选与点评（用户 2026-08-15）

1. 100 候选（按 G(AB,2025) 降序）→ DeepSeek v4 pro 筛选 → 50（2 选 1）
2. 对 50 个写一句话推荐语 + 三挡（看好/中立/不看好）+ 理由
3. HTML 输出（25 并发）
"""
import json, os, sys, time, re
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dns_patch  # noqa: F401
from key_loader import get_api_key

BASE_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-v4-pro"
OUT = "data/sci/discovery/predict2026"
N_CONC = 25

SELECT_PROMPT = """你是科学趋势研判专家。以下是从 arXiv 2025 年数据中通过"两跳共同邻居采样 + G(有效电导)波动性筛选"得到的 100 个候选学科交叉对（按 G(AB,2025) 降序排列）。这些对当前(2025)尚未直连（未共同出现），但通过中间概念存在间接路径，G 波动性处于预示"将直连"的中等区间。

你的任务：从中选出 50 个最有希望在未来成为真实研究方向的对（2 选 1）。
筛选标准：
1. 两个概念都是真实、有实质内涵的学术概念（排除套话/垃圾对）
2. 交叉有真实的学科价值（不是生拉硬凑）
3. 两个领域有可见的融合迹象（via 桥说明已有间接连接）
4. 排除"XX×大语言模型/LLM"这类 AI 热词凑热闹对（除非有真实具体价值）

输出：JSON 数组，只包含你选中的 50 个的序号（原列表中的 # 号），如 [1, 3, 5, ...]。不要输出其他内容。"""

VERDICT_PROMPT = """你是科学趋势研判专家。以下是候选学科交叉对（均来自 arXiv 数据挖掘，当前尚未直连但存在间接路径）。请对每个对给出三挡推荐并附理由。

三挡定义：
- 看好：概念真实、交叉价值高、有可见融合迹象，最可能成为未来研究方向
- 中立：概念真实但交叉价值一般，或融合迹象尚弱
- 不看好：概念牵强、套话、或纯 AI 热词凑热闹

对每个对输出一行 JSON：{"A": "...", "B": "...", "verdict": "看好|中立|不看好", "reason": "一句话理由（中文）"}

候选列表（# 序号 · 概念A × 概念B · 领域 · 桥接概念 via · G2025 值 · G序列(2015-2025)）："""


def ask_deepseek(messages, max_tokens=4000):
    key = get_api_key()
    if not key:
        return None
    payload = {
        "model": MODEL,
        "messages": messages,
        "thinking": {"type": "disabled"},
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    r = requests.post(BASE_URL, headers=headers, json=payload, timeout=600)
    r.raise_for_status()
    return r.json()["choices"][0]["message"].get("content", "")


def main():
    cands = json.load(open(f"{OUT}/gseries_2025_sorted.json"))
    print(f"候选: {len(cands)}", flush=True)

    # Step 1: LLM 筛选 100→50
    lines = []
    for i, c in enumerate(cands, 1):
        gs = ", ".join(f"{y}:{c['series'][str(y)]:.0f}" for y in range(2015, 2026)
                       if str(y) in c["series"])
        lines.append(f"#{i} · {c['A']} × {c['B']} · [{c['field']}] · via={c['via']} · G2025={c['g2025']:.1f} · 序列={gs}")
    cand_text = "\n".join(lines)
    print(f"[筛选] 发送 {len(cands)} 候选...", flush=True)
    t0 = time.time()
    resp = ask_deepseek([
        {"role": "system", "content": "你是科学趋势研判专家，只输出 JSON。"},
        {"role": "user", "content": f"{SELECT_PROMPT}\n\n候选列表：\n{cand_text}"},
    ], max_tokens=2000)
    print(f"[筛选] 返回 ({time.time()-t0:.0f}s): {resp[:200] if resp else 'None'}", flush=True)

    # 解析选中的序号
    nums = []
    if resp:
        m = re.search(r"\[[\d,\s]+\]", resp)
        if m:
            nums = [int(x) for x in re.findall(r"\d+", m.group())]
    if not nums:
        print("WARN: 筛选解析失败，退回按 G2025 取前 50")
        nums = list(range(1, 51))
    nums = [n for n in nums if 1 <= n <= 100][:50]
    print(f"选中 {len(nums)} 个: {nums[:20]}...", flush=True)

    selected = [cands[n - 1] for n in nums]
    json.dump(selected, open(f"{OUT}/selected50.json", "w"), ensure_ascii=False, indent=1)

    # Step 2: 25 并发点评
    print(f"[点评] 并发 {N_CONC} 点评 {len(selected)} 个...", flush=True)
    verdicts = {}

    def one_verdict(idx, c):
        gs = ", ".join(f"{y}:{c['series'][str(y)]:.0f}" for y in range(2015, 2026)
                       if str(y) in c["series"])
        prompt = f"{VERDICT_PROMPT}\n#{idx} · {c['A']} × {c['B']} · [{c['field']}] · via={c['via']} · G2025={c['g2025']:.1f} · 序列={gs}"
        try:
            r = ask_deepseek([
                {"role": "system", "content": "你只输出一行 JSON。"},
                {"role": "user", "content": prompt},
            ], max_tokens=300)
            m = re.search(r"\{.*\}", r, re.S) if r else None
            if m:
                return json.loads(m.group())
        except Exception as e:
            print(f"  #{idx} err: {e}", flush=True)
        return None

    t1 = time.time()
    with ThreadPoolExecutor(max_workers=N_CONC) as ex:
        futs = {ex.submit(one_verdict, i, c): (i, c) for i, c in enumerate(selected, 1)}
        for f in as_completed(futs):
            i, c = futs[f]
            v = f.result()
            if v:
                verdicts[i] = v
    print(f"[点评] 完成 {len(verdicts)}/{len(selected)} ({time.time()-t1:.0f}s)", flush=True)

    # Step 3: HTML 输出
    cards = ""
    for i, c in enumerate(selected, 1):
        v = verdicts.get(i, {})
        verdict = v.get("verdict", "?")
        reason = v.get("reason", "")
        color = {"看好": "#2e7d32", "中立": "#f9a825", "不看好": "#c62828"}.get(verdict, "#666")
        gs = " · ".join(f"{y}:{c['series'][str(y)]:.0f}" for y in range(2015, 2026) if str(y) in c["series"])
        cards += f"""<div class="card" style="border-left:5px solid {color}">
<div class="head"><b>#{i}</b> {c['A']} × {c['B']} <span class="field">[{c['field']}]</span>
<span class="verdict" style="background:{color}">{verdict}</span></div>
<div class="meta">via={c['via']} · I={c['I']} · cv={c['cv']:.2f} · G2025={c['g2025']:.1f}</div>
<div class="series">G序列(2015-25): {gs}</div>
<div class="reason">理由: {reason}</div></div>"""
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>2026 学科方向涌现预言 · LLM 筛选 50 + 三挡点评</title>
<style>
body {{ font-family:-apple-system,"PingFang SC",sans-serif; max-width:1200px; margin:0 auto; padding:20px; background:#fafafa; }}
h1 {{ border-bottom:3px solid #1f77b4; }}
.panel {{ background:#fff; border:1px solid #e0e0e0; border-radius:10px; padding:16px; margin:16px 0; }}
.analysis {{ background:#fff8e1; border-left:4px solid #f9a825; padding:12px 16px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(480px,1fr)); gap:12px; }}
.card {{ background:#fff; border:1px solid #ddd; border-radius:8px; padding:10px; }}
.head {{ font-size:13px; margin-bottom:4px; }}
.field {{ color:#1f77b4; font-size:11px; }}
.verdict {{ color:#fff; padding:1px 8px; border-radius:10px; font-size:11px; margin-left:6px; }}
.meta {{ font-size:11px; color:#888; }}
.series {{ font-size:10px; color:#666; font-family:monospace; margin:3px 0; }}
.reason {{ font-size:12px; color:#333; margin-top:4px; }}
</style></head><body>
<h1>2026 学科方向涌现预言 · Top 50（LLM 筛选 + 三挡点评）</h1>
<div class="panel"><div class="analysis">
<b>流程</b>：100 候选（G2025 降序）→ DeepSeek v4 pro 筛选 2 选 1 → 50 → 25 并发逐对点评（看好/中立/不看好 + 理由）。<br>
<b>数据</b>：arXiv 2025 单年两跳采样，G(AB,t) 2015-2025 非归一化有效电导。<br>
<b>预期</b>：基于 2015 回测命中率 5.8%，50 个中预期 2-3 个在 2026-2035 直连并增长。
</div></div>
<div class="grid">{cards}</div>
</body></html>"""
    with open(f"{OUT}/llm_top50.html", "w") as f:
        f.write(html)
    print(f"\n→ {OUT}/llm_top50.html")


if __name__ == "__main__":
    main()
