#!/usr/bin/env python3
# =====================================================================
# FocusView 2.0 · 封闭版本（FROZEN 2026-08-11）
# 本文件为最终版，冻结不再改动；新功能开发（PaperExplore 3.0）
# 请使用独立文件 visualize_v3.py，勿在本文件上修改。
# 封闭依据：docs/focus_view_v3_plan.md §1 + docs/focusview_2.0_manifest.md
# =====================================================================
"""网络可视化（pipeline2，版本 v2.1，2026-08-08）。

版本：pipeline2_可视化_v2.1（当前可工作版本）
  - v1 = 基线（已固化 backups/visualize_pipeline2_v1_20260808.py）：
    静态/速度/加速度 + Top-N 边选择 + 95 分位渐变配色 + 焦点子图（--focuson/--nobackground）
    + 白名单 off（--no-whitelist）。
  - v2 = PMI 选边（--pmi）领域平衡：排序键换成 PMI 混合分数
    score = λ·PMI + (1-λ)·log2(w+1)，λ=0.2，min_cooc=5。
    抵消高频词（LLM×real world）对 Top-N 的垄断。CS 占比 84%→71.4%。
  - v2.1 = 当前版本（2026-08-08）：v2 + P1 补边（--pmi 时自动）——
    PMI 把物理/数学打成 2-10 节点小分量（252 分量中 249 个小分量），
    _patch_fragments 用原始共现边（min_cooc 放宽到 3）把小分量缝合回主干。
    解决碎片化；不改动已入选的 PMI 高质量边。
    v1 完全兼容（不加 --pmi 行为与 v1 一致）；产物文件名加 _pmi 后缀并存。
  - 后续版本（v3+）：分层配额（strata quota）、统计口径（--category cs|basic|all）、
    表格报告。每项改造保留旧版本，不做破坏性覆盖。

数据源：归一化标注（pipeline2/data/annotation/normalized/year={year}/part-0.parquet）

静态版（--mode static）：单年，节点+边统一蓝色。
  节点大小 ∝ doc_freq，边粗细 ∝ 共现权重。

速度版（--mode speed --target 2022 --base 2021）：两年，红=增长 蓝=萎缩。
  份额归一化（加0.5平滑）→ log2FC → 节点/边按 log2FC 着色。

加速度版（--mode accel --target 2023 --prev 2022 --base 2021）：三年，橙=加速 绿=减速。
  二阶差 a = p(focal) - 2·p(prev) + p(base)。
"""
import os, sys, argparse, math, statistics
from collections import Counter
import duckdb
from pyvis.network import Network

# 对外分享版：路径统一从 config.py 读取（config.yaml 配置）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
ANN = config.ANN_NORM
VISUAL_DIR = config.VISUAL_DIR
NUMBER_NORMALIZE = config.NUMBER_NORMALIZE
MANUAL_WHITELIST = config.MANUAL_WHITELIST

# ============================================================
# 主题体系（theme）：决定背景色 + 配色方案（静态/速度/加速度）
# 每个 theme 是一套完整配色，供 export_* 按 ui+theme 取用。
# ============================================================
THEMES = {
    # 简洁 / 典雅（白底，对齐旧 pipeline 配色）
    "light": {
        "name": "经典·白底",
        "bgcolor": "#ffffff",
        "text_color": "#1a1a1a",          # 页面字体色（白底黑字）
        "node_label_color": "#1a1a1a",    # 节点标签字色（白底黑字）
        "bar_bg": "#f5f5f5",              # 主题栏背景
        "node_base": (238, 238, 238),     # 渐变起点（浅灰）
        "node_fixed": "#1976d2",          # 静态图节点固定色（蓝）
        "edge_fixed": "#1976d2",          # 静态图边固定色（蓝）
        "speed_pos": (211, 47, 47),       # 速度 正=增长（红）
        "speed_neg": (21, 101, 194),      # 速度 负=萎缩（蓝）
        "accel_pos": (245, 124, 0),       # 加速度 正=加速（橙）
        "accel_neg": (46, 125, 50),       # 加速度 负=减速（绿）
        # 浅色端点（色阶起点，fc≈0 也呈现纯色系浅端，绝非灰褐）
        "speed_pos_light": (255, 205, 210),
        "speed_neg_light": (187, 222, 251),
        "accel_pos_light": (255, 224, 178),
        "accel_neg_light": (200, 230, 201),
        "focus": {"h0": "e63946", "h1": "457b9d", "h2": "a8dadc"},  # 焦点/1-hop/2-hop
    },
    # 星空主题：深蓝黑背景，节点如星点亮——"知识的领域由节点（概念）照亮"
    "starry": {
        "name": "星空·深蓝底",
        "bgcolor": "#0a0e27",
        "text_color": "#ffffff",          # 页面字体色（深底白字，对比更强）
        "node_label_color": "#ffffff",    # 节点标签字色（深底白字）
        "bar_bg": "#121838",              # 主题栏背景
        "node_base": (34, 40, 74),        # 渐变起点（深蓝灰）
        "node_fixed": "#8fd3ff",          # 静态图节点固定色（星蓝）
        "edge_fixed": "#4a5a9e",          # 静态图边固定色（暗蓝）
        "speed_pos": (255, 140, 90),      # 速度 正=增长（暖橙红）
        "speed_neg": (110, 170, 255),     # 速度 负=萎缩（冷蓝）
        "accel_pos": (255, 210, 70),      # 加速度 正=加速（金黄）
        "accel_neg": (130, 230, 130),     # 加速度 负=减速（浅绿）
        "speed_pos_light": (255, 210, 200),
        "speed_neg_light": (180, 215, 255),
        "accel_pos_light": (255, 235, 180),
        "accel_neg_light": (195, 240, 195),
        "focus": {"h0": "ff5a5f", "h1": "8fd3ff", "h2": "3a4a7a"},  # 星空：亮红焦点/星蓝1hop/深蓝2hop
    },
    # 暗色主题：深灰黑背景，低亮度护眼，学术专注
    "dark": {
        "name": "暗色·深灰底",
        "bgcolor": "#121212",
        "text_color": "#f0f0f0",          # 页面字体色（深底浅灰白字，对比更强）
        "node_label_color": "#f0f0f0",    # 节点标签字色（深底浅灰白字）
        "bar_bg": "#1e1e1e",
        "node_base": (45, 45, 55),        # 渐变起点（深灰）
        "node_fixed": "#4da6ff",          # 静态图节点固定色（亮蓝）
        "edge_fixed": "#3a6ea5",
        "speed_pos": (255, 100, 100),     # 速度 正=增长（红）
        "speed_neg": (100, 160, 255),     # 速度 负=萎缩（蓝）
        "accel_pos": (255, 190, 80),      # 加速度 正=加速（橙）
        "accel_neg": (120, 220, 120),     # 加速度 负=减速（绿）
        "speed_pos_light": (90, 40, 40),
        "speed_neg_light": (40, 60, 100),
        "accel_pos_light": (90, 70, 35),
        "accel_neg_light": (45, 85, 45),
        "focus": {"h0": "ff6b6b", "h1": "4da6ff", "h2": "3a6ea5"},  # 暗色：亮红焦点/亮蓝1hop/暗蓝2hop
    },
}

# ============================================================
# UI 档位：控制节点大小公式 / 边宽公式 / 物理引擎 / 自适应
# small  = 当前 v1（紧凑：节点小边细，力学易平衡）
# standard = 旧 pipeline（美观：节点大边粗，动态摆动有探索欲，密集自适应）
# ============================================================
UI_PRESETS = {
    "small": {
        "node_size_min": 8,
        "node_size_max": 50.0,
        "edge_width": "small",        # 边宽 1+4*min(1,w/200) 固定饱和
        "physics": "stabilized",      # barnesHut + 稳定迭代300（快速收敛停摆）
        "adaptive": False,            # 不做密集自适应缩小
    },
    "standard": {
        "node_size_min": 8,
        "node_size_max": 50.0,
        "edge_width": "norm",         # 边宽 0.5+4.5*(w-wmin)/(wmax-wmin) 全局归一
        "physics": "stabilized",      # 与 small 一致：barnesHut + 稳定迭代（用户要求 stable 也稳定）
        "adaptive": True,             # 密集时 size_max=50*sqrt(1500/N) 自适应缩小
    },
}


def get_theme(name):
    """取主题配置；缺省回退 starry（用户 2026-08-13 定稿：星空·深蓝为缺省）。"""
    return THEMES.get(name, THEMES["starry"])


def get_ui(name):
    """取 UI 档位配置；缺省回退 small（当前 v1）。"""
    return UI_PRESETS.get(name, UI_PRESETS["small"])


def percentile(vals, q):
    """计算百分位（对齐旧实现，q=95 用于渐变 cap）。"""
    vals = [v for v in vals if v is not None]
    if not vals:
        return 0
    if len(vals) < 2:
        return vals[0]
    qs = statistics.quantiles(vals, n=100)
    return qs[min(q, 99) - 1]


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def load_phrases(year):
    """读取单年归一化标注的短语列表。返回 list of list。"""
    fp = os.path.join(ANN, f"year={year}", "part-0.parquet")
    if not os.path.exists(fp):
        print(f"[err] 无标注数据: {fp}")
        return []
    con = duckdb.connect(database=":memory:")
    rows = con.execute(f"SELECT phrases FROM read_parquet('{fp}')").fetchall()
    return [set(ph) for (ph,) in rows]


def doc_freq(phrase_lists):
    """统计每个短语的出现文章数 doc_freq。"""
    df = Counter()
    for s in phrase_lists:
        for p in s:
            df[p] += 1
    return df


def log2fc(c1, tot1, c0, tot0):
    return math.log2(((c1 + 0.5) / tot1) / ((c0 + 0.5) / tot0))


def speed_color(v, pos_cap, neg_cap, theme):
    """速度配色：正=增长(红系)，负=萎缩(蓝系)。色阶起点=该色系浅端，
    fc≈0 也是浅红/浅蓝（绝无灰褐），随 |fc| 加深至纯色。"""
    if v >= 0:
        t = min(1.0, v / pos_cap) if pos_cap > 0 else 0.0
        c = lerp(theme["speed_pos_light"], theme["speed_pos"], t)
    else:
        t = min(1.0, (-v) / neg_cap) if neg_cap > 0 else 0.0
        c = lerp(theme["speed_neg_light"], theme["speed_neg"], t)
    return "#%02x%02x%02x" % c


def accel_color(v, pos_cap, neg_cap, theme):
    """加速度配色：正=加速(橙系)，负=减速(绿系)。色阶起点=该色系浅端，
    fc≈0 也是浅橙/浅绿（绝无灰褐），随 |fc| 加深至纯色。"""
    if v >= 0:
        t = min(1.0, v / pos_cap) if pos_cap > 0 else 0.0
        c = lerp(theme["accel_pos_light"], theme["accel_pos"], t)
    else:
        t = min(1.0, (-v) / neg_cap) if neg_cap > 0 else 0.0
        c = lerp(theme["accel_neg_light"], theme["accel_neg"], t)
    return "#%02x%02x%02x" % c


def _net(ui, theme, focus_mode=False):
    """按 ui + theme 背景色创建网络。所有档位均用稳定物理（barnesHut + 稳定迭代，
    快速收敛停摆），保证可浏览与截图，用户要求 standard 也稳定。

    focus_mode=True（焦点子图）：节点少，用更强的中心引力 + 更大 springLength，
    配合焦点词 mass=10，自然形成「焦点居中、1-hop 围圈、2-hop 外围」的放射布局。"""
    net = Network(notebook=False, directed=False, height="1000px", width="100%",
                  bgcolor=theme["bgcolor"])
    label_col = theme.get("node_label_color", "#1a1a1a")
    phys = {
        "gravitationalConstant": -8000 if focus_mode else -20000,
        "centralGravity": 0.3 if focus_mode else 0.05,
        "springLength": 220 if focus_mode else 120,
        "springConstant": 0.04 if focus_mode else 0.02,
        "damping": 0.9, "avoidOverlap": 0.8,
    }
    net.set_options("""{
        "physics": {
            "solver": "barnesHut",
            "stabilization": {"enabled": true, "iterations": 300, "fit": true},
            "barnesHut": {
                "gravitationalConstant": %G%, "centralGravity": %C%,
                "springLength": %S%, "springConstant": %K%,
                "damping": 0.9, "avoidOverlap": 0.8
            }
        },
        "nodes": {
            "font": {"color": "%LABEL_COL%", "size": 12}
        }
    }""".replace("%LABEL_COL%", label_col)
         .replace("%G%", str(phys["gravitationalConstant"]))
         .replace("%C%", str(phys["centralGravity"]))
         .replace("%S%", str(phys["springLength"]))
         .replace("%K%", str(phys["springConstant"])))
    return net


def _adaptive_size_max(n_nodes, n_edges, ui):
    """standard 档密集自适应：节点最大尺寸随 节点+边 总数收缩，保证可浏览。
    small 档不做自适应（返回固定 size_max）。"""
    if not ui["adaptive"]:
        return ui["node_size_max"]
    N = n_nodes + n_edges
    return max(6.0, min(ui["node_size_max"], ui["node_size_max"] * math.sqrt(1500.0 / max(N, 1))))


def _make_geom(node_set, df, edges, ui):
    """返回 (node_size_fn, edge_width_fn, dmax, dmin, wmax, wmin, size_max)。
    按 ui 档位决定节点大小/边宽公式。"""
    dmax = max((df[n] for n in node_set), default=1)
    dmin = min((df[n] for n in node_set), default=1)
    wmax = max((w for _, _, w in edges), default=1)
    wmin = min((w for _, _, w in edges), default=1)
    nmin = ui["node_size_min"]
    size_max = _adaptive_size_max(len(node_set), len(edges), ui)

    def node_size(d):
        if dmax == dmin:
            return size_max * 0.4
        return nmin + (size_max - nmin) * (d - dmin) / (dmax - dmin)

    def edge_width(w):
        if ui["edge_width"] == "norm":
            # standard：全局归一 0.5 + 4.5*(w-wmin)/(wmax-wmin)
            return 1.5 if wmax == wmin else 0.5 + 4.5 * (w - wmin) / (wmax - wmin)
        # small：固定饱和 1 + 4*min(1, w/200)
        return 1.0 + 4.0 * min(1.0, w / 200.0)

    return node_size, edge_width, size_max


# ============================================================
# 前端主题切换：注入 JS 到 pyvis 生成的 HTML
# 原理：节点/边在 Python 端已存 val 数值字段；JS 按当前主题用 lerp 重算颜色，
#       不重跑布局 → 切换即时（纯内存 update，毫秒级）。
# 依赖：节点/边 add 时必须带 val 字段（speed/accel=份额变化值，static=doc_freq）。
# ============================================================
THEME_JS = r"""
<script type="text/javascript">
// ---- 主题表（由 Python 注入）----
var THEMES = %THEMES_JSON%;
var CURRENT_THEME = "%CURRENT_THEME%";
var MODE = "%MODE%";
var POS_CAP = %POS_CAP%;
var NEG_CAP = %NEG_CAP%;

function rgb2hex(arr) {
    return '#' + arr.map(v=>Math.max(0,Math.min(255,Math.round(v))).toString(16).padStart(2,'0')).join('');
}
function lerp(a, b, t) {
    return [a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t, a[2]+(b[2]-a[2])*t];
}
// 按主题重算一个值 v 的颜色。色阶起点 = 该色系的「浅色端点」（非灰色），
// 保证 fc≈0 的节点/边也呈现浅红/浅蓝/浅橙/浅绿，绝不出现灰褐。
// theme.speed_pos_light / speed_neg_light / accel_pos_light / accel_neg_light 为 RGB 数组。
function valColor(v, theme) {
    if (MODE === 'static') return theme.node_fixed;
    var pLight, pDark, nLight, nDark;
    if (MODE === 'accel') {
        pLight = theme.accel_pos_light; pDark = theme.accel_pos;
        nLight = theme.accel_neg_light; nDark = theme.accel_neg;
    } else {
        pLight = theme.speed_pos_light; pDark = theme.speed_pos;
        nLight = theme.speed_neg_light; nDark = theme.speed_neg;
    }
    if (v >= 0) {
        var t = Math.min(1.0, v / POS_CAP);
        return rgb2hex(lerp(pLight, pDark, t));
    } else {
        var tn = Math.min(1.0, (-v) / NEG_CAP);
        return rgb2hex(lerp(nLight, nDark, tn));
    }
}
// 应用主题：改背景、字体、所有节点/边颜色
function applyTheme(name) {
    var th = THEMES[name];
    CURRENT_THEME = name;
    // 背景 + 字体
    document.body.style.backgroundColor = th.bgcolor;
    document.body.style.color = th.text_color;
    document.querySelector('#themeBar') && (document.querySelector('#themeBar').style.background = th.bar_bg);
    document.querySelector('#themeBar') && (document.querySelector('#themeBar').style.color = th.text_color);
    // 关键：pyvis 的 #mynetwork 容器背景是硬编码 #ffffff，会盖住 body 背景，
    // 必须同步改容器背景，否则图区仍是白底。
    var netBox = document.getElementById('mynetwork');
    if (netBox) netBox.style.backgroundColor = th.bgcolor;
    // 更新节点标签字色（深底白字/浅底黑字，增强对比）
    if (window.__net && th.node_label_color) {
        window.__net.setOptions({nodes: {font: {color: th.node_label_color}}});
    }
    // 通过 vis 实例的 DataSet 批量更新节点/边颜色（window.__net 在 network 创建后注入）
    var net = window.__net;
    if (!net || !net.body || !net.body.data) return;
    var nset = net.body.data.nodes;
    var ids = nset.getIds();
    var nupdates = [];
    for (var i=0;i<ids.length;i++) {
        var id = ids[i];
        var node = nset.get(id);
        nupdates.push({id: id, color: valColor(node.val, th)});
    }
    nset.update(nupdates);   // 批量 update，一次触发重绘，避免逐条导致卡顿
    var eset = net.body.data.edges;
    var eids = eset.getIds();
    var eupdates = [];
    for (var j=0;j<eids.length;j++) {
        var eid = eids[j];
        var edge = eset.get(eid);
        eupdates.push({id: eid, color: valColor(edge.val, th)});
    }
    eset.update(eupdates);
    // 刷新按钮选中态
    var btns = document.querySelectorAll('#themeBar .tbtn');
    for (var k=0;k<btns.length;k++) {
        btns[k].style.background = btns[k].getAttribute('data-t') === name ? 'rgba(255,255,255,0.35)' : 'transparent';
        btns[k].style.border = btns[k].getAttribute('data-t') === name ? '1px solid ' + th.text_color : '1px solid transparent';
    }
}
</script>
<div id="themeBar" style="position:fixed;top:10px;right:10px;z-index:999;padding:6px 10px;border-radius:8px;font-family:sans-serif;font-size:13px;background:%BAR_BG%;color:%TEXT_COLOR%;box-shadow:0 1px 4px rgba(0,0,0,0.2)">
  主题:
  %THEME_BUTTONS%
</div>
<script type="text/javascript">
window.addEventListener('load', function() {
    // 等 vis 网络实例（window.__net）就绪后应用当前主题
    var tries = 0;
    function tryTheme() {
        tries++;
        try {
            if (typeof window.__net !== 'undefined' && window.__net && window.__net.body && window.__net.body.data) {
                applyTheme(CURRENT_THEME);
            } else if (tries < 300) {
                setTimeout(tryTheme, 100);
            }
        } catch(e) { if (tries < 300) setTimeout(tryTheme, 100); }
    }
    tryTheme();
});
function switchTheme(name) { applyTheme(name); }
</script>
"""


# 布局修复 JS（仅 iframe 内预览生效，整页/新窗口打开保持 pyvis 原始布局）：
#   - 在 iframe 里（window.self !== window.top）：容器铺满 iframe 并 fit 居中，
#     解决「图偏下/空白」问题，窗口大小本身不变。
#   - 整页打开（window.self === window.top）：不做任何改动（1000px 原布局无问题）。
FOCUS_LAYOUT_JS = """
<script type="text/javascript">
(function() {
    if (window.self === window.top) return;  // 整页/新窗口打开：保持原样
    function fixIframe() {
        var box = document.getElementById('mynetwork');
        if (!box) return;
        var d = document.documentElement, b = document.body;
        d.style.height = '100%';
        b.style.height = '100%';
        b.style.overflow = 'hidden';
        box.style.width = '100%';
        box.style.height = '100%';
        if (window.__net && window.__net.fit) {
            try {
                window.__net.fit({animation: false});
                window.__net.redraw();
            } catch (e) {}
        }
    }
    if (document.readyState === 'complete' || document.readyState === 'interactive') {
        setTimeout(fixIframe, 80);
    } else {
        window.addEventListener('DOMContentLoaded', function() { setTimeout(fixIframe, 80); });
    }
    window.addEventListener('load', function() { setTimeout(fixIframe, 150); });
    window.addEventListener('resize', function() { setTimeout(fixIframe, 30); });
})();
</script>
"""


def _inject_theme_switch(html, mode, theme_name, pos_cap, neg_cap):
    """注入主题切换 JS + 主题栏 + 布局修复到 pyvis HTML。"""
    import json as _json
    # 只注入主题栏需要的字段（去掉内部 name 等）
    themes = {}
    for k, th in THEMES.items():
        themes[k] = {
            "bgcolor": th["bgcolor"],
            "text_color": th.get("text_color", "#000000"),
            "node_label_color": th.get("node_label_color", "#1a1a1a"),
            "bar_bg": th.get("bar_bg", "#ffffff"),
            "node_base": th["node_base"],
            "node_fixed": th["node_fixed"],
            "edge_fixed": th["edge_fixed"],
            "speed_pos": th["speed_pos"],
            "speed_neg": th["speed_neg"],
            "accel_pos": th["accel_pos"],
            "accel_neg": th["accel_neg"],
            "speed_pos_light": th.get("speed_pos_light", (255, 205, 210)),
            "speed_neg_light": th.get("speed_neg_light", (187, 222, 251)),
            "accel_pos_light": th.get("accel_pos_light", (255, 224, 178)),
            "accel_neg_light": th.get("accel_neg_light", (200, 230, 201)),
        }
    cur = themes[theme_name]
    active = "rgba(255,255,255,0.35)"
    btns = []
    for k, v in THEMES.items():
        sel = k == theme_name
        bg = active if sel else "transparent"
        bd = "1px solid " + cur["text_color"] if sel else "1px solid transparent"
        btns.append(
            f'<button class="tbtn" data-t="{k}" onclick="switchTheme(\'{k}\')" '
            f'style="background:{bg};border:{bd};color:inherit;padding:2px 8px;'
            f'margin-left:4px;border-radius:4px;cursor:pointer">{v["name"]}</button>'
        )
    btns = "".join(btns)
    js = (THEME_JS
          .replace("%THEMES_JSON%", _json.dumps(themes, ensure_ascii=False))
          .replace("%CURRENT_THEME%", theme_name)
          .replace("%MODE%", mode)
          .replace("%POS_CAP%", f"{pos_cap:.6g}")
          .replace("%NEG_CAP%", f"{neg_cap:.6g}")
          .replace("%BAR_BG%", cur["bar_bg"])
          .replace("%TEXT_COLOR%", cur["text_color"])
          .replace("%THEME_BUTTONS%", btns))
    # 1) 在 network 创建后注入全局引用 window.__net（供切换 JS 访问 vis 实例）
    marker = "network = new vis.Network(container, data, options);"
    if marker in html:
        html = html.replace(marker, marker + "\nwindow.__net = network;", 1)
    # 2) 注入切换 JS + 主题栏 + 布局修复（仅 iframe 内生效，整页打开零改动）
    if "</body>" in html:
        html = html.replace("</body>", js + FOCUS_LAYOUT_JS + "</body>", 1)
    else:
        html = html + js + FOCUS_LAYOUT_JS
    return html


# 用户自定义白名单最多词数
MANUAL_WHITELIST_MAX = 5


def load_manual_whitelist(focuson=None, use_file=True):
    """读取用户自定义白名单，返回 tuple 集（最多 MANUAL_WHITELIST_MAX 个）。
    与标注阶段一致：每行一个词，tokenize 规范化；应用单复数归一映射
    （用户写 surface code，标注后节点名是 surface code(s)，映射后匹配）。

    来源二选一（--focuson 命令行注入优先）：
      - focuson: 逗号分隔字符串，如 "transmon, surface code"
      - 否则读 whitelist_manual.txt（每行一词，# 注释忽略）
    use_file=False（--no-whitelist）：普通图不读白名单文件，
    白名单只用于焦点子图（--focuson/--nobackground），反映用户个人问题。"""
    # 单复数归一映射（与标注一致）：surface code -> surface code(s)
    norm_map = {}
    npf = NUMBER_NORMALIZE
    if os.path.exists(npf):
        import csv as _csv
        for r in _csv.DictReader(open(npf)):
            s = (r.get("singular") or "").strip().lower()
            p = (r.get("plural") or "").strip().lower()
            m = (r.get("merged") or "").strip().lower()
            if s:
                norm_map.setdefault(s, m)
            if p:
                norm_map.setdefault(p, m)

    def _norm(t):
        t = norm_map.get(t, t)
        tt = tuple(t.split())
        return tt if tt else None

    terms = set()
    if focuson:
        for raw in focuson.split(","):
            t = raw.strip().lower()
            if not t:
                continue
            if len(terms) >= MANUAL_WHITELIST_MAX:
                break
            tt = _norm(t)
            if tt:
                terms.add(tt)
        return terms

    fp = MANUAL_WHITELIST
    if not use_file:
        return set()   # --no-whitelist：普通图不读白名单文件
    # 单复数归一映射（与标注一致）：surface code -> surface code(s)
    norm_map = {}
    npf = NUMBER_NORMALIZE
    if os.path.exists(npf):
        import csv as _csv
        for r in _csv.DictReader(open(npf)):
            s = (r.get("singular") or "").strip().lower()
            p = (r.get("plural") or "").strip().lower()
            m = (r.get("merged") or "").strip().lower()
            if s:
                norm_map.setdefault(s, m)
            if p:
                norm_map.setdefault(p, m)
    terms = set()
    if not os.path.exists(fp):
        return terms
    n = 0
    for line in open(fp):
        t = line.strip().lower()
        if not t or t.startswith("#"):
            continue
        if n >= 5:
            break
        # 应用单复数归一映射，使白名单词匹配标注后节点名
        t = norm_map.get(t, t)
        tt = tuple(t.split())
        if tt:
            terms.add(tt)
            n += 1
    return terms


# 用户自定义白名单的补边目标边数
MANUAL_WHITELIST_MIN_EDGES = 5


def _apply_manual_whitelist(manual_terms, edge_w, selected_edges, top_edges, min_edge):
    """用户自定义白名单的专门可视化处理：
    1. 强制包含：白名单词即使不在 Top-N 也要显示（补边到至少 MANUAL_WHITELIST_MIN_EDGES 条）。
    2. 补边：若白名单词的边 < 5，从真实共现数据 edge_w 按权重补齐到 5（带入新节点，新节点不扩展）。
    3. 无任何边 → 忽略（拉倒不显示）。
    返回 (新边列表, 白名单词串集合)。"""
    # 已选边里的节点-边关系：记录每个词在 selected_edges 里的边数
    sel_edges_set = set()
    node_edge_cnt = {}
    for a, b, w in selected_edges:
        sel_edges_set.add((a, b))
        node_edge_cnt[a] = node_edge_cnt.get(a, 0) + 1
        node_edge_cnt[b] = node_edge_cnt.get(b, 0) + 1

    # 白名单词的 token 串
    manual_strs = {" ".join(t) for t in manual_terms}
    added_edges = []
    new_nodes = set()

    for mt in manual_terms:
        mstr = " ".join(mt)
        # 白名单词在已选边里的边数
        cnt = node_edge_cnt.get(mstr, 0)
        if cnt >= MANUAL_WHITELIST_MIN_EDGES:
            continue  # 已足够，不补
        # 从全部共现里找该词的边（排除已选的），按权重取 top 补齐
        need = MANUAL_WHITELIST_MIN_EDGES - cnt
        cand = []
        for (a, b), w in edge_w.items():
            if a == mstr or b == mstr:
                if (a, b) not in sel_edges_set and (b, a) not in sel_edges_set:
                    cand.append((a, b, w))
        cand.sort(key=lambda x: -x[2])
        for a, b, w in cand[:need]:
            added_edges.append((a, b, w))
            sel_edges_set.add((a, b))
            # 新节点（边的另一端）只加入，不继续扩展
            other = b if a == mstr else a
            new_nodes.add(other)
            cnt += 1

    # 合并：已选边 + 补边
    merged = list(selected_edges) + added_edges
    return merged, manual_strs


# ============================================================
# PMI 选边（v2 领域平衡）：把边权换成 PMI 混合排序键，抵消高频词虚高
# ============================================================
PMI_MIN_COOC = 5    # PMI 前先剔除共现 < 此值的边（防低频噪声被 PMI 抬升）
PMI_LAMBDA = 0.2    # 混合系数：score = λ·PMI + (1-λ)·log2(w+1)
                    #   λ=1 纯 PMI（低频虚边多，实测 w<20 占 100%）
                    #   λ=0.2 最优平衡：CS 占比 84%→46%（2025 Top-1500 实测），
                    #     接近文库基线 45.3%，物理强关联边回归且保留高权边
PMI_SIG = 1         # 1=log2(w+1)（λ=0.2 甜点），2=log10(w+1)（更偏显著性）


def _select_edges_pmi(edge_w, df, n_total, min_cooc=PMI_MIN_COOC, top_edges=0,
                      lam=PMI_LAMBDA, sig=PMI_SIG):
    """PMI 混合选边：按 score 排序取 Top-N，抵消高频词（LLM×real world）虚高垄断。

    PMI(A,B) = log2( w·N / (df_A·df_B) )
      - w      = A,B 共现篇数（原边权）
      - N      = 总篇数
      - df_A   = 短语 A 的 doc_freq
    含义：实际共现 / 随机期望。>0 = 超出随机的强关联；≤0 = 弱于随机（剔除）。

    纯 PMI（λ=1）的缺陷：低频碎片对（w=5 且 df 极小）PMI 虚高，实测 Top-1500
    全是 w<20 的噪声边。故引入显著性混合项（LLM 调研 §3 建议）：
      score = λ·PMI + (1-λ)·log2(w+1)
    λ=0.2（2025 实测）：CS 占比 84%→46%，w≥50 高权边保留 63 条，
    物理术语对（lattice qcd×gauge theory）回归——既非纯 CS 垄断也非纯碎片。

    返回 (a, b, w) 三元组（w 保持原共现权重，仅排序键用 score；
    下游 edge_width/标题仍用真实 w，信息不丢）。
    """
    N = max(n_total, 1)
    cands = []
    for (a, b), w in edge_w.items():
        if w < min_cooc:
            continue
        fa = max(df.get(a, 0), 1)
        fb = max(df.get(b, 0), 1)
        pmi = math.log2((w * N) / (fa * fb))
        if pmi <= 0:
            continue
        if sig == 2:
            sig_term = math.log10(w + 1)
        else:
            sig_term = math.log2(w + 1)
        score = lam * pmi + (1 - lam) * sig_term
        cands.append((score, a, b, w))
    cands.sort(key=lambda x: -x[0])
    if top_edges > 0:
        cands = cands[:top_edges]
    return [(a, b, w) for _, a, b, w in cands]


# ============================================================
# P1 小网络挂接补边（v2.1 碎片缝合）：把小分量用原始共现缝合到大分量
# ============================================================
PATCH_SMALL_MAX = 10   # 节点数 ≤ 此值视为小分量（碎片）
PATCH_MIN_W = 3        # 挂接边的原始共现权重下限（放宽到 min_cooc=3）
PATCH_MAX_EDGES = 600  # 补边总数上限（防止补边过多稀释 Top-N）
PATCH_CS_PENALTY = 0.5 # 跨分量补边时对高频(CS)边的权重折扣，鼓励跨领域缝合


def _patch_fragments(edges, edge_w, small_max=PATCH_SMALL_MAX,
                     min_w=PATCH_MIN_W, max_edges=PATCH_MAX_EDGES):
    """把碎片小分量用原始共现边缝合到大分量（P1 补边）。

    背景：PMI 选边（--pmi）把物理/数学等低共现领域打成 2-10 节点小分量
    （2025 实测 252 分量中 249 个 ≤10 节点，最大分量 470 节点几乎全 CS）。
    补边利用原始共现（放宽 min_cooc 到 min_w=3）把小分量连回大分量，
    恢复"综合科学文库"的连通感，且不改动已入选的 PMI 高质量边。

    补边评分 = 原始共现权重 w × 领域折扣（避免总挂到高频 CS 边）。
    由于 edge_w 两端都是短语（无类别信息），用"边权相对端点的流行度"
    近似：对高频端点（doc_freq 大）打折，鼓励把碎片挂到"不那么高频"的
    主干节点，从而倾向跨领域缝合。

    流程：
      1. 由已选边建图，找连通分量；最大分量 = 主干。
      2. 对每个小分量（≤ small_max），在 edge_w 里找"小分量节点 ↔ 主干节点"
         的原始共现边（权重 ≥ min_w）。
      3. 按"折扣权重"降序补入，直到 max_edges 上限或所有小分量已并入主干。
    返回补边后的 (a, b, w) 列表（补边权重=原始共现 w，非 PMI 分数）。
    """
    from collections import defaultdict
    if not edges:
        return edges
    # 1. 建图 + 连通分量
    adj = defaultdict(set)
    for a, b, _ in edges:
        adj[a].add(b)
        adj[b].add(a)
    seen = set()
    comps = []
    for node in adj:
        if node in seen:
            continue
        stack = [node]
        seen.add(node)
        comp = []
        while stack:
            n = stack.pop()
            comp.append(n)
            for m in adj[n]:
                if m not in seen:
                    seen.add(m)
                    stack.append(m)
        comps.append(comp)
    if len(comps) <= 1:
        return edges  # 已经连成一片，无需补边
    comps.sort(key=lambda x: -len(x))
    main_nodes = set(comps[0])          # 最大分量（主干）
    small_comps = [c for c in comps[1:] if len(c) <= small_max]
    if not small_comps:
        return edges
    # 端点流行度（用于领域折扣）：在 edge_w 里的累计共现权重近似 doc_freq
    pop = defaultdict(int)
    for (a, b), w in edge_w.items():
        pop[a] += w
        pop[b] += w
    # 2. 候选挂接边：小分量 ↔ 主干，原始共现权重 ≥ min_w
    cands = []
    for comp in small_comps:
        for n in comp:
            for (a, b), w in edge_w.items():
                if w < min_w:
                    continue
                if (a == n and b in main_nodes) or (b == n and a in main_nodes):
                    # 领域折扣：主干端越流行折扣越大（抑制挂到 LLM 等 CS 高频词）
                    main_end = b if a == n else a
                    penalty = PATCH_CS_PENALTY ** (1.0 + math.log10(max(pop[main_end], 1)))
                    cands.append((w * penalty, w, a, b))
    # 3. 按折扣权重降序补入（每分量优先挂最强，总量受 max_edges 约束）
    cands.sort(key=lambda x: -x[0])
    added = set()
    result = list(edges)
    existing = set((a, b) if a < b else (b, a) for a, b, _ in edges)
    for _score, w, a, b in cands:
        if len(result) - len(edges) >= max_edges:
            break
        k = (a, b) if a < b else (b, a)
        if k in existing or k in added:
            continue
        result.append((a, b, w))
        added.add(k)
    print(f"[patch] 补边 {len(added)} 条 | 小分量 {len(small_comps)} 个待缝 "
          f"(权重≥{min_w}, 上限{max_edges})", flush=True)
    return result


# ============================================================
# 类别折扣选边（v2.2 领域平衡·源头折扣）：
# 利用数据已知的首要分类，在计数时给边权打折
# ============================================================
W_CS = 0.3     # cs 边权重（单参数，n_eff = w_cs·n_cs + n_oth）
W_OTH = 1.0    # 其他边权重（固定 1）


_EDGE_CS_CACHE = {}   # year -> (edge_cs, edge_oth) 每边的 n_cs/n_oth（模块级缓存）


def _build_edge_counts(year):
    """统计 year 每条共现边的 n_cs（cs 文章贡献数）/ n_oth（其他文章贡献数）。

    边=文章：每条边 (a,b) 由若干文章共同贡献，按文章首要分类二分 cs/其他。
    返回 (edge_cs, edge_oth) 两个 Counter。
    """
    if year in _EDGE_CS_CACHE:
        return _EDGE_CS_CACHE[year]
    import duckdb as _duck
    edge_cs, edge_oth = {}, {}
    try:
        con = _duck.connect(database=":memory:")
        papers_fp = os.path.join(ROOT, "data", "parquet", "papers", f"year={year}", "*.parquet")
        ann_fp = os.path.join(ANN, f"year={year}", "part-0.parquet")
        if os.path.exists(os.path.dirname(papers_fp)) and os.path.exists(os.path.dirname(ann_fp)):
            rows = con.execute(f"""
                SELECT a.phrases, p.categories
                FROM read_parquet('{ann_fp}') a
                JOIN read_parquet('{papers_fp}') p ON a.arxiv_id = p.arxiv_id
            """).fetchall()
            from collections import Counter as _Cnt
            ec = _Cnt()
            eo = _Cnt()
            for phrases, cat in rows:
                is_cs = (cat or "").split(" ")[0].startswith("cs.")
                pl = sorted(set(phrases))
                for i in range(len(pl)):
                    for j in range(i + 1, len(pl)):
                        a, b = pl[i], pl[j]
                        k = (a, b) if a < b else (b, a)
                        if is_cs:
                            ec[k] += 1
                        else:
                            eo[k] += 1
            edge_cs, edge_oth = ec, eo
    except Exception as e:
        print(f"[warn] 边类别统计失败（退化为普通 Top-N）: {e}", flush=True)
    _EDGE_CS_CACHE[year] = (edge_cs, edge_oth)
    return edge_cs, edge_oth


def _select_edges_discount(edge_w, df, year, top_edges=0, min_edge=20,
                           w_cs=W_CS, w_oth=W_OTH):
    """边加权选边（用户方案·边=文章）：n_eff = w_cs·n_cs + w_oth·n_oth。

    每条边 (a,b) 由若干文章贡献：n_cs（cs 首要分类文章）、n_oth（其他）。
    有效边数 n_eff = w_cs·n_cs + w_oth·n_oth（单参数 w_cs，w_oth=1）。
    按 n_eff 降序选 Top-N。返回的 w 是 raw 共现数（n_cs+n_oth），供可视化粗细。
    """
    edge_cs, edge_oth = _build_edge_counts(year)
    if not edge_cs and not edge_oth:
        edges = [(a, b, w) for (a, b), w in edge_w.items() if w >= min_edge]
        if top_edges > 0:
            edges.sort(key=lambda x: -x[2])
            edges = edges[:top_edges]
        return edges

    scored = []
    keys = set(edge_cs) | set(edge_oth)
    for k in keys:
        n_cs = edge_cs.get(k, 0)
        n_oth = edge_oth.get(k, 0)
        n_raw = n_cs + n_oth
        if n_raw < min_edge:
            continue
        n_eff = w_cs * n_cs + w_oth * n_oth
        scored.append((n_eff, n_raw, k[0], k[1]))
    scored.sort(key=lambda x: -x[0])
    if top_edges > 0:
        scored = scored[:top_edges]
    return [(a, b, w) for _, w, a, b in scored]


# 紫灰配色（v2.2 静态图类别着色）
COLOR_CS = "#9c27b0"       # CS 节点：紫色
COLOR_NON = "#9e9e9e"      # 非CS 节点：灰色
COLOR_CROSS_EDGE = "#ce93d8"  # 跨类边：淡紫


def _category_colors(edges, year):
    """给边加权选边的节点/边配色（紫灰二分，边=文章）。

    节点颜色：按节点参与的边的 cs 权重占比（n_cs/(n_cs+n_oth) 加权）判类。
    边颜色：该边的 cs 占比 >0.6 → 紫（CS 边）；<0.4 → 灰（其他边）；否则淡紫（跨类）。
    返回 (node_color_map, edge_color_map)。
    """
    from collections import defaultdict as _dd
    edge_cs, edge_oth = _build_edge_counts(year)
    node_colors = {}
    edge_colors = {}
    node_cs_w = _dd(float)   # 节点参与的 cs 边权累计
    node_all_w = _dd(float)
    for a, b, w in edges:
        k = (a, b) if a < b else (b, a)
        n_cs = edge_cs.get(k, 0)
        n_oth = edge_oth.get(k, 0)
        cs_r = n_cs / (n_cs + n_oth) if (n_cs + n_oth) else 0.5
        if cs_r > 0.6:
            edge_colors[(a, b)] = COLOR_CS
        elif cs_r < 0.4:
            edge_colors[(a, b)] = COLOR_NON
        else:
            edge_colors[(a, b)] = COLOR_CROSS_EDGE
        node_cs_w[a] += n_cs
        node_all_w[a] += (n_cs + n_oth)
        node_cs_w[b] += n_cs
        node_all_w[b] += (n_cs + n_oth)
    for n in node_all_w:
        r = node_cs_w[n] / node_all_w[n] if node_all_w[n] else 0.5
        node_colors[n] = COLOR_CS if r > 0.5 else COLOR_NON
    return node_colors, edge_colors


# 焦点子图参数（--nobackground 裁剪）
FOCUS_TOP_K = 10       # 每个焦点词的直连边上限（hop1）
FOCUS_MAX_HOP = 2      # 从焦点词出发最多走 2 步


def _build_focus_subgraph(year, manual_terms, top_k=FOCUS_TOP_K, max_hop=FOCUS_MAX_HOP):
    """duckDB 列查询提取「焦点子图」（--nobackground 裁剪）。

    原则：只查有限节点，不构建全量共现图。
      - hop1：查含焦点词的篇目，统计焦点词与其他词的共现 → 每焦点词取 top_k 边
      - hop2：查含 hop1 节点的篇目，统计 hop1 的邻居（排除已在子图内的）
      - 子图内所有边：两端都在子图内的篇目级共现，全部列出

    返回 (focus_edges, manual_strs, hop_map)：
      focus_edges = [(a,b,w), ...]（两端都在子图内的边，按权重降序）
      manual_strs = 焦点词字符串集
      hop_map     = {节点字符串: 0/1/2}
    """
    manual_strs = {" ".join(t) for t in manual_terms}
    fp = os.path.join(ANN, f"year={year}", "part-0.parquet")
    if not os.path.exists(fp):
        print(f"[err] 无标注数据: {fp}")
        return [], manual_strs, {}

    con = duckdb.connect(database=":memory:")
    # 焦点词必须存在：一次查询各焦点词的 doc_freq（批量 list 内联）
    df_focus = {}
    for f in manual_strs:
        df_focus[f] = con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{fp}') WHERE list_contains(phrases, ?)",
            [f]).fetchone()[0]
    manual_strs = {f for f in manual_strs if df_focus[f] > 0}
    for f, n in df_focus.items():
        if n == 0:
            print(f"[warn] 焦点词 '{f}' 在 {year} 无篇目，跳过", flush=True)
    if not manual_strs:
        print("[err] 所有焦点词均无篇目，无法生成焦点子图", flush=True)
        return [], manual_strs, {}

    # ---- 批量加载辅助：一次取出含 seeds 任一词的所有篇目短语 ----
    def _load_seed_phrases(seeds):
        if not seeds:
            return []
        ors = " OR ".join([f"list_contains(phrases, ?)" for _ in seeds])
        rows = con.execute(
            f"SELECT phrases FROM read_parquet('{fp}') WHERE {ors}",
            list(seeds)).fetchall()
        return [list(r[0]) for r in rows]

    def _neighbor_counts(plist, target, exclude):
        """在已加载篇目短语里统计 target 的邻居（排除 exclude 集），返回 {词:篇数}。"""
        cnt = Counter()
        for pl in plist:
            if target in pl:
                for w in pl:
                    if w != target and w not in exclude:
                        cnt[w] += 1
        return cnt

    # hop1：每焦点词全局 top-(top_k*2) 邻居 + 其他焦点词强制保留
    hop = {f: 0 for f in manual_strs}
    hop1_nodes = set()
    for f in manual_strs:
        plist = _load_seed_phrases({f})
        cnt = _neighbor_counts(plist, f, set())  # 全局邻居
        cand = list(cnt.items())
        cand.sort(key=lambda x: -x[1])
        for o in manual_strs - {f}:
            if o in cnt and (o, cnt[o]) not in cand[:top_k]:
                cand.append((o, cnt[o]))
        cand.sort(key=lambda x: -x[1])
        for nbr, _ in cand[:top_k]:
            if nbr not in hop:
                hop[nbr] = 1
            hop1_nodes.add(nbr)

    # hop2：一次加载含任一 hop1 的篇目，统计各 hop1 的 top_k 邻居（排除已在子图的）。
    # 记录每个 hop1 入选的 hop2 邻居（hop1_edges: {hop1: [(hop2, w)]}），
    # 子图边收集时只保留这些边，保证「每个 hop1 节点最多 top_k 条 hop2 边」。
    existing = set(hop)
    plist_h1 = _load_seed_phrases(hop1_nodes)
    hop1_edges = {}   # hop1 -> [(hop2, 权重)]
    for h1 in hop1_nodes:
        cnt = _neighbor_counts(plist_h1, h1, existing)
        top = sorted(cnt.items(), key=lambda x: -x[1])[:top_k]
        hop1_edges[h1] = top
        for nb2, _ in top:
            if nb2 not in existing:
                hop[nb2] = 2

    # 子图内边：只保留「从焦点词出发的路径边」——
    #   hop0-hop0（焦点词之间）、hop0-hop1、hop1-hop2（hop1 已入选的邻居）。
    # 不保留 hop1-hop1 / hop2-hop2 / hop0-hop2（非路径边，稠密噪声）。
    focus_nodes = set(hop)
    plist_f = _load_seed_phrases(focus_nodes)
    edges = Counter()
    for pl in plist_f:
        in_f = [w for w in pl if w in focus_nodes]
        for i in range(len(in_f)):
            for j in range(i + 1, len(in_f)):
                a, b = in_f[i], in_f[j]
                ha, hb = hop[a], hop[b]
                if ha == hb:
                    if ha != 0:      # hop1-hop1 / hop2-hop2 非路径边，跳过
                        continue
                elif abs(ha - hb) != 1:
                    continue          # hop0-hop2 层级差2，跳过
                # hop1-hop2 边：只在 hop1 的入选邻居列表内保留
                if ha == 1 and hb == 2 and b not in {x for x, _ in hop1_edges.get(a, [])}:
                    continue
                if hb == 1 and ha == 2 and a not in {x for x, _ in hop1_edges.get(b, [])}:
                    continue
                edges[(a, b) if a < b else (b, a)] += 1
    con.close()

    focus_edges = [(a, b, w) for (a, b), w in edges.items()]
    focus_edges.sort(key=lambda x: -x[2])
    return focus_edges, manual_strs, hop


def _slugify_focus(manual_strs):
    """把焦点词列表转成安全文件名段：空格->-, 保留字母数字-，多词用 _ 连接。"""
    parts = []
    for s in sorted(manual_strs):
        p = "".join(c if (c.isalnum() or c == "-") else "-" for c in s)
        p = "-".join(x for x in p.split("-") if x)
        parts.append(p)
    slug = "_".join(parts)[:100]
    return slug if slug else "focus"


# 焦点图渲染缓存：同一子图（node_set 相同）在 static/speed/accel 三次渲染间
# 重复查询同一年份的 doc_freq / 边权重，缓存消除重复（键含年份+节点集）。
_DOC_FREQ_CACHE = {}
_EDGE_W_CACHE = {}
_CACHE_MAX = 200


def _cache_trim():
    if len(_DOC_FREQ_CACHE) > _CACHE_MAX:
        _DOC_FREQ_CACHE.clear()
    if len(_EDGE_W_CACHE) > _CACHE_MAX:
        _EDGE_W_CACHE.clear()


def _render_focus(mode, year, target, prev, base, edges, manual_strs, hop_map,
                  ui, theme, ui_name, theme_name, min_edge, top_edges):
    """焦点子图统一渲染（static/speed/accel 共用）。

    mode 决定：子目录/文件名年份段/doc_freq 查询年/pos_cap,neg_cap。
    """
    node_set = set()
    for a, b, _ in edges:
        node_set.add(a)
        node_set.add(b)
    # doc_freq：按 mode 读对应年份（speed/accel 还需 base/prev 年算 fc 语义色）
    df_year = {"static": year, "speed": target, "accel": target}.get(mode, year)
    con = duckdb.connect(database=":memory:")
    df = {}

    def _doc_freq(yr):
        node_key = tuple(sorted(node_set))
        ck = (yr, node_key)
        if ck in _DOC_FREQ_CACHE:
            return _DOC_FREQ_CACHE[ck]
        fp = os.path.join(ANN, f"year={yr}", "part-0.parquet")
        out = {}
        if os.path.exists(fp) and node_set:
            ors = " OR ".join(["list_contains(phrases, ?)"] * len(node_set))
            rows = con.execute(
                f"SELECT phrases FROM read_parquet('{fp}') WHERE {ors}",
                list(node_set)).fetchall()
            cnt = Counter()
            for (pl,) in rows:
                for w in set(pl):
                    if w in node_set:
                        cnt[w] += 1
            out = dict(cnt)
        _DOC_FREQ_CACHE[ck] = out
        _cache_trim()
        return out

    df = _doc_freq(df_year)

    # 同批边的共现权重（YYYY 焦点图边集在指定年的权重）——边增减的归一基础。
    edge_pairs = [(a, b) for a, b, _ in edges]

    def _edge_weights(yr):
        """统计 YYYY 焦点图边集 edge_pairs 在 yr 年的共现权重（两端同篇出现次数）。
        只统计焦点子图已选中的边（edge_pairs），保证与 edges 同口径归一。"""
        node_key = tuple(sorted(node_set))
        edge_key = tuple(sorted(edge_pairs))
        ck = (yr, node_key, edge_key)
        if ck in _EDGE_W_CACHE:
            return _EDGE_W_CACHE[ck]
        fp = os.path.join(ANN, f"year={yr}", "part-0.parquet")
        out = {}
        if os.path.exists(fp) and edge_pairs:
            pair_set = set(edge_pairs)
            ors = " OR ".join(["list_contains(phrases, ?)"] * len(node_set))
            rows = con.execute(
                f"SELECT phrases FROM read_parquet('{fp}') WHERE {ors}",
                list(node_set)).fetchall()
            cnt = Counter()
            for (pl,) in rows:
                pls = set(pl)
                in_set = [w for w in pls if w in node_set]
                for i in range(len(in_set)):
                    for j in range(i + 1, len(in_set)):
                        a, b = in_set[i], in_set[j]
                        key = (a, b) if a < b else (b, a)
                        if key in pair_set:   # 只统计焦点子图边
                            cnt[key] += 1
            out = dict(cnt)
        _EDGE_W_CACHE[ck] = out
        _cache_trim()
        return out

    node_size, edge_width, size_max = _make_geom(node_set, df, edges, ui)
    manual_size = max(size_max * 1.8, ui["node_size_min"] * 4)

    # 焦点词（hop0）按 doc_freq 降序等差微降 5 档大小（用户定稿）：
    #   rank1=doc_freq 最高=manual_size(最大)，之后每档只小一点点
    #   （manual_size*0.08），最小档仍足够大（≈0.68*manual_size）。
    #   目的仅是提示"哪个焦点词更重要"的层级关系，不做算法级尺寸计算。
    focus_list = [n for n in node_set if hop_map.get(n, 2) == 0]
    focus_list.sort(key=lambda n: -df.get(n, 0))  # doc_freq 降序，稳定不依赖 set 顺序
    nf = len(focus_list)
    delta = manual_size * 0.08
    focus_sizes = {}
    for i, n in enumerate(focus_list):
        focus_sizes[n] = max(manual_size - i * delta, manual_size * 0.6)

    # speed/accel 语义色：fc = log2FC（speed 两年份额比）或二阶差（accel 三年）。
    # 连续色阶复用全年定义 speed_color / accel_color（红蓝/橙绿渐变）。
    # 节点 fc 与边 fc 分开计算：边颜色 = 边权重份额的增减（用户定稿），
    # 绝不借用节点 fc 拼凑。
    pos_vals, neg_vals = [], []
    node_fc = {}
    edge_fc = {}
    if mode == "speed":
        df_b = _doc_freq(base) if base else {}
        ew_b = _edge_weights(base) if base else {}
        tot_t = sum(df.values()) or 1
        tot_b = sum(df_b.values()) or 1
        tot_e = sum(w for _, _, w in edges) or 1
        tot_eb = sum(ew_b.values()) or 1
        for n in node_set:
            fc = log2fc(df.get(n, 0), tot_t, df_b.get(n, 0), tot_b)
            node_fc[n] = fc
            (pos_vals if fc >= 0 else neg_vals).append(abs(fc))
        for a, b, w in edges:
            key = (a, b) if a < b else (b, a)
            fc_e = log2fc(w, tot_e, ew_b.get(key, 0), tot_eb)
            edge_fc[(a, b)] = fc_e
            (pos_vals if fc_e >= 0 else neg_vals).append(abs(fc_e))
        pos_cap = percentile(pos_vals, 95) or 1.0
        neg_cap = percentile(neg_vals, 95) or 1.0
    elif mode == "accel":
        df_p = _doc_freq(prev) if prev else {}
        df_b = _doc_freq(base) if base else {}
        ew_p = _edge_weights(prev) if prev else {}
        ew_b = _edge_weights(base) if base else {}
        tot_f = sum(df.values()) or 1
        tot_p = sum(df_p.values()) or 1
        tot_b = sum(df_b.values()) or 1
        tot_ef = sum(w for _, _, w in edges) or 1
        tot_ep = sum(ew_p.values()) or 1
        tot_eb = sum(ew_b.values()) or 1
        for n in node_set:
            p_f = df.get(n, 0) / tot_f
            p_p = df_p.get(n, 0) / tot_p
            p_b = df_b.get(n, 0) / tot_b
            a = p_f - 2 * p_p + p_b  # 二阶差（比例）
            node_fc[n] = a
            (pos_vals if a >= 0 else neg_vals).append(abs(a))
        for a, b, w in edges:
            key = (a, b) if a < b else (b, a)
            e_f = w / tot_ef
            e_p = ew_p.get(key, 0) / tot_ep
            e_b = ew_b.get(key, 0) / tot_eb
            a_e = e_f - 2 * e_p + e_b  # 边权重份额二阶差
            edge_fc[(a, b)] = a_e
            (pos_vals if a_e >= 0 else neg_vals).append(abs(a_e))
        pos_cap = percentile(pos_vals, 95) or 1e-9
        neg_cap = percentile(neg_vals, 95) or 1e-9

    con.close()  # 所有 _doc_freq / _edge_weights 调用完毕后再关闭连接

    fg = theme["focus"]
    net = _net(ui, theme, focus_mode=True)

    def _node_color(n):
        """static=主题 hop 色；speed/accel=语义色（连续色阶）。"""
        if mode == "speed":
            return speed_color(node_fc.get(n, 0.0), pos_cap, neg_cap, theme)
        if mode == "accel":
            return accel_color(node_fc.get(n, 0.0), pos_cap, neg_cap, theme)
        return f"#{fg['h0' if hop_map.get(n, 2) == 0 else ('h1' if hop_map.get(n, 2) == 1 else 'h2')]}"

    # 节点 val：speed/accel 存 fc（供 JS 主题切换 valColor 正确上色），
    # static 存 doc_freq（JS static 分支固定 node_fixed，val 仅用于 tooltip 大小）。
    for n in node_set:
        h = hop_map.get(n, 2)
        col = _node_color(n)
        nval = node_fc.get(n, 0.0) if mode in ("speed", "accel") else df.get(n, 0)
        if h == 0:
            sz = focus_sizes.get(n, manual_size)
            net.add_node(n, label=n, size=sz, color=col, shape="star",
                         borderWidth=3, borderColor="#ffd700", mass=10, val=nval,
                         title=f"{n} | doc_freq={df.get(n,0)} | ★焦点词"
                               + (f" | log2FC={node_fc.get(n,0):+.2f}" if mode == "speed" else "")
                               + (f" | accel={node_fc.get(n,0):+.4f}" if mode == "accel" else ""))
        elif h == 1:
            net.add_node(n, label=n, size=node_size(df.get(n, 0)) * 1.3,
                         color=col, mass=3, val=nval,
                         title=f"{n} | doc_freq={df.get(n,0)} | hop1")
        else:
            net.add_node(n, label=n, size=node_size(df.get(n, 0)) * 0.8,
                         color=col, opacity=0.6, mass=1, val=nval,
                         title=f"{n} | doc_freq={df.get(n,0)} | hop2")
    for a, b, w in edges:
        # 边颜色 = 边自身权重份额的增减（用户定稿）：
        #   speed=log2FC(边权重份额) 红蓝；accel=边权重份额二阶差 橙绿。
        # static：焦点间边金色醒目，其余主题边色。
        key = (a, b) if a < b else (b, a)
        if mode == "speed":
            fc_e = edge_fc.get(key, 0.0)
            col = speed_color(fc_e, pos_cap, neg_cap, theme)
            edge_title = f"{a} × {b} = {w} | 边log2FC={fc_e:+.2f}"
        elif mode == "accel":
            fc_e = edge_fc.get(key, 0.0)
            col = accel_color(fc_e, pos_cap, neg_cap, theme)
            edge_title = f"{a} × {b} = {w} | 边accel={fc_e:+.4f}"
        elif hop_map.get(a) == 0 and hop_map.get(b) == 0:
            col = "#ffd700"
            edge_title = f"{a} × {b} = {w}"
        else:
            col = theme["edge_fixed"]
            edge_title = f"{a} × {b} = {w}"
        if hop_map.get(a) == 0 and hop_map.get(b) == 0:
            ew = max(edge_width(w), 3.0)
        else:
            ew = edge_width(w)
        # 边 val：speed/accel 存边 fc（供 JS valColor 正确上色），static 存权重。
        eval_ = edge_fc.get(key, 0.0) if mode in ("speed", "accel") else float(w)
        net.add_edge(a, b, width=ew, color=col, val=eval_,
                     title=edge_title)
    out_dir = os.path.join(VISUAL_DIR, mode)
    os.makedirs(out_dir, exist_ok=True)
    suf = f"_e{min_edge}" + (f"_t{top_edges}" if top_edges else "") + f"_{ui_name}_{theme_name}"
    focus_slug = _slugify_focus(manual_strs)
    focus_tag = f"_focus_{focus_slug}"
    if mode == "static":
        path = os.path.join(out_dir, f"pair_static_network_{year}{focus_tag}{suf}.html")
    elif mode == "speed":
        path = os.path.join(out_dir, f"pair_speed_network_{base}-{target}{focus_tag}{suf}.html")
    else:
        path = os.path.join(out_dir, f"pair_accel_network_{base}-{prev}-{target}{focus_tag}{suf}.html")
    _write_html_with_theme(net, path, mode, theme_name,
                           pos_cap if mode in ("speed", "accel") else 1.0,
                           neg_cap if mode in ("speed", "accel") else 1.0)
    print(f"[{mode}] {path} | nodes={len(node_set)} edges={len(edges)} 焦点={sorted(manual_strs)}")
    return path


def export_focus_series(year, focuson, min_edge=20, top_edges=1500, ui_name="standard",
                        theme_name="light", modes=None):
    """单子图三渲染（焦点图主交互路径，用户定稿方案）。

    一次构建 YYYY 焦点子图（_build_focus_subgraph 只依赖 focal 年），
    再按 modes 依次渲染 static / speed / accel：
      - static: 只用 YYYY 数据
      - speed : 同子图 + YYYY-1 同批节点/边份额 → 红蓝
      - accel : 同子图 + YYYY-1/YYYY-2 同批份额二阶差 → 橙绿
    三图基于完全相同的子图结构（node_set/edges/hop_map 绝对一致），
    对比无结构漂移；速度 ≈ 单次子图构建 + 各 mode 历史年查询（亚秒）。

    返回 {mode: html绝对路径}；焦点词无效/无篇目则返回 {}。
    """
    if modes is None:
        modes = ["static", "speed", "accel"]
    ui = get_ui(ui_name)
    theme = get_theme(theme_name)
    manual_terms = load_manual_whitelist(focuson=focuson, use_file=True)
    edges, manual_strs, hop_map = _build_focus_subgraph(year, manual_terms)
    if not edges:
        print("[err] 焦点子图为空", flush=True)
        return {}
    print(f"[focus] 焦点子图: 焦点词={sorted(manual_strs)} | nodes={len(set(hop_map))} "
          f"edges={len(edges)} (hop0={sum(1 for h in hop_map.values() if h==0)}, "
          f"hop1={sum(1 for h in hop_map.values() if h==1)}, hop2={sum(1 for h in hop_map.values() if h==2)})",
          flush=True)
    out = {}
    for mode in modes:
        try:
            if mode == "static":
                p = _render_focus("static", year, year, 0, 0, edges, manual_strs, hop_map,
                                  ui, theme, ui_name, theme_name, min_edge, top_edges)
            elif mode == "speed":
                p = _render_focus("speed", year, year, 0, year - 1, edges, manual_strs, hop_map,
                                  ui, theme, ui_name, theme_name, min_edge, top_edges)
            elif mode == "accel":
                p = _render_focus("accel", year, year, year - 1, year - 2, edges, manual_strs,
                                  hop_map, ui, theme, ui_name, theme_name, min_edge, top_edges)
            else:
                continue
            if p:
                out[mode] = p
        except Exception as exc:
            print(f"[warn] 焦点图 {mode} 渲染失败: {exc}", flush=True)
    return out


def export_static(year, min_edge=20, top_edges=0, ui_name="small", theme_name="light",
                  focuson=None, nobackground=False, no_whitelist=False, pmi=False,
                  category_discount=None):
    ui = get_ui(ui_name)
    theme = get_theme(theme_name)

    # 焦点子图快速路径：duckDB 列查询，只查有限节点，不加载全量短语/共现
    manual_terms = load_manual_whitelist(focuson=focuson, use_file=not no_whitelist)
    if nobackground:
        edges, manual_strs, hop_map = _build_focus_subgraph(year, manual_terms)
        if not edges:
            print("[err] 焦点子图为空", flush=True)
            return
        print(f"[focus] 焦点子图: 焦点词={sorted(manual_strs)} | nodes={len(set(hop_map))} "
              f"edges={len(edges)} (hop0={sum(1 for h in hop_map.values() if h==0)}, "
              f"hop1={sum(1 for h in hop_map.values() if h==1)}, hop2={sum(1 for h in hop_map.values() if h==2)})",
              flush=True)
        _render_focus("static", year, year, 0, 0, edges, manual_strs, hop_map,
                      ui, theme, ui_name, theme_name, min_edge, top_edges)
        return

    phrase_lists = load_phrases(year)
    if not phrase_lists:
        return
    df = doc_freq(phrase_lists)
    # 共现对
    edge_w = Counter()
    for s in phrase_lists:
        pl = list(s)
        for i in range(len(pl)):
            for j in range(i + 1, len(pl)):
                a, b = pl[i], pl[j]
                edge_w[(a, b) if a < b else (b, a)] += 1
    edges = None
    if category_discount is not None:
        # v2.2 边加权选边（用户方案）：n_eff = w_cs·n_cs + w_oth·n_oth
        w_cs = category_discount
        edges = _select_edges_discount(edge_w, df, year, top_edges=top_edges,
                                       min_edge=min_edge, w_cs=w_cs, w_oth=W_OTH)
        # v2.2 + P1 补边：把小分量缝合回主干（边加权版碎片少，速度快）
        edges = _patch_fragments(edges, edge_w, max_edges=200)
    elif pmi:
        # v2 PMI 选边：排序键换成 PMI，w 保留原共现权重
        edges = _select_edges_pmi(edge_w, df, len(phrase_lists), top_edges=top_edges)
        # v2.1 P1 补边：把小分量缝合回主干（利用原始共现）
        edges = _patch_fragments(edges, edge_w)
    else:
        edges = [(a, b, w) for (a, b), w in edge_w.items() if w >= min_edge]
        if top_edges > 0:
            edges.sort(key=lambda x: -x[2])
            edges = edges[:top_edges]
    # 用户自定义白名单：强制包含 + 补边（普通模式，非焦点子图）。
    # --no-whitelist：白名单只用于焦点子图，普通图不读文件。
    manual_terms = load_manual_whitelist(focuson=focuson, use_file=not no_whitelist)
    if manual_terms:
        edges, manual_strs = _apply_manual_whitelist(
            manual_terms, edge_w, edges, top_edges, min_edge)
    else:
        manual_strs = set()
    node_set = set()
    for a, b, _ in edges:
        node_set.add(a)
        node_set.add(b)
    node_size, edge_width, size_max = _make_geom(node_set, df, edges, ui)
    # 白名单定位：size 强制放大到超越普通节点上限（size_max*1.8 且保底 nmin*4），
    # 图里鹤立鸡群一眼可见（金边只是装饰，定位靠大尺寸反差）。
    manual_size = max(size_max * 1.8, ui["node_size_min"] * 4)
    fixed_col = theme["node_fixed"]
    edge_col = theme["edge_fixed"]

    # 紫灰配色（边加权模式）：CS 紫 / 非CS 灰 / 跨类边淡紫
    disc_node_col, disc_edge_col = {}, {}
    if category_discount is not None:
        disc_node_col, disc_edge_col = _category_colors(edges, year)

    net = _net(ui, theme)
    for n in node_set:
        gold = n in manual_strs  # 白名单词：大尺寸 + 金边标记
        sz = manual_size if gold else node_size(df[n])
        if category_discount is not None and n in disc_node_col:
            col = disc_node_col[n]
            node_color = {"background": col, "border": "#ffd700",
                          "highlight": {"border": "#ffd700"}} if gold else col
        else:
            node_color = {"background": fixed_col, "border": "#ffd700",
                          "highlight": {"border": "#ffd700"}} if gold else fixed_col
        tag = ""
        if category_discount is not None and n in disc_node_col:
            tag = " | CS" if disc_node_col[n] == COLOR_CS else " | 非CS"
        net.add_node(n, label=n, size=sz, color=node_color, val=float(df[n]),
                     borderWidth=3 if gold else None,
                     title=f"{n} | doc_freq={df[n]}" + tag + (" | ★用户白名单" if gold else ""))
    for a, b, w in edges:
        if category_discount is not None and (a, b) in disc_edge_col:
            ec = disc_edge_col[(a, b)]
        else:
            ec = edge_col
        net.add_edge(a, b, width=edge_width(w), color=ec, val=float(w),
                     title=f"{a} × {b} = {w}")
    out_dir = os.path.join(VISUAL_DIR, "static")
    os.makedirs(out_dir, exist_ok=True)
    suf = f"_e{min_edge}" + (f"_t{top_edges}" if top_edges else "") + f"_{ui_name}_{theme_name}"
    if pmi:
        suf += "_pmi"
    if category_discount is not None:
        suf += "_disc"
    path = os.path.join(out_dir, f"pair_static_network_{year}{suf}.html")
    _write_html_with_theme(net, path, "static", theme_name, 1.0, 1.0)
    print(f"[static] {path} | nodes={len(node_set)} edges={len(edges)} 白名单={sorted(manual_strs)}" + (" PMI" if pmi else "") + (" DISCOUNT" if category_discount is not None else ""))


def _write_html_with_theme(net, path, mode, theme_name, pos_cap, neg_cap):
    """写 HTML，并注入主题切换 JS。pyvis 要求输出文件名以 .html 结尾，
    故先用 .html 结尾的临时文件写，读取注入后再写最终文件。
    临时文件写入系统 temp 目录，避免在产物目录残留。"""
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(),
                       os.path.basename(path)[:-5] + "_theme_tmp.html")
    net.write_html(tmp)
    with open(tmp, "r", encoding="utf-8") as fh:
        html = fh.read()
    html = _inject_theme_switch(html, mode, theme_name, pos_cap, neg_cap)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    if os.path.exists(tmp):
        os.remove(tmp)


def export_speed(target, base, min_edge=20, top_edges=0, ui_name="small", theme_name="light",
                 focuson=None, nobackground=False, no_whitelist=False, pmi=False,
                 category_discount=None):
    """速度图：target(如2022) vs base(如2021)，红=增长 蓝=萎缩。"""
    ui = get_ui(ui_name)
    theme = get_theme(theme_name)
    # 焦点子图快速路径：duckDB 列查询，只查 target 年有限节点
    manual_terms = load_manual_whitelist(focuson=focuson, use_file=not no_whitelist)
    if nobackground:
        edges, manual_strs, hop_map = _build_focus_subgraph(target, manual_terms)
        if not edges:
            print("[err] 焦点子图为空", flush=True)
            return
        print(f"[focus] 焦点子图: 焦点词={sorted(manual_strs)} | nodes={len(set(hop_map))} "
              f"edges={len(edges)} (hop0={sum(1 for h in hop_map.values() if h==0)}, "
              f"hop1={sum(1 for h in hop_map.values() if h==1)}, hop2={sum(1 for h in hop_map.values() if h==2)})",
              flush=True)
        _render_focus("speed", target, target, 0, base, edges, manual_strs, hop_map,
                      ui, theme, ui_name, theme_name, min_edge, top_edges)
        return
    t_list = load_phrases(target)
    b_list = load_phrases(base)
    if not t_list or not b_list:
        return
    df_t = doc_freq(t_list)
    df_b = doc_freq(b_list)
    tot_t = sum(df_t.values())
    tot_b = sum(df_b.values())
    # 两年都出现的节点 + 边界（新增/消失单独）
    common = set(df_t) | set(df_b)
    # 共现（用 target 年）
    edge_w = Counter()
    for s in t_list:
        pl = list(s)
        for i in range(len(pl)):
            for j in range(i + 1, len(pl)):
                a, b = pl[i], pl[j]
                edge_w[(a, b) if a < b else (b, a)] += 1
    edges = None
    if category_discount is not None:
        # v2.2 边加权选边：n_eff = w_cs·n_cs + w_oth·n_oth
        w_cs = category_discount
        edges = _select_edges_discount(edge_w, df_t, target, top_edges=top_edges,
                                       min_edge=min_edge, w_cs=w_cs, w_oth=W_OTH)
        edges = _patch_fragments(edges, edge_w, max_edges=200)
    elif pmi:
        # v2 PMI 选边：排序键换成 PMI，w 保留原共现权重
        edges = _select_edges_pmi(edge_w, df_t, len(t_list), top_edges=top_edges)
        # v2.1 P1 补边：把小分量缝合回主干（利用原始共现）
        edges = _patch_fragments(edges, edge_w)
    else:
        edges = [(a, b, w) for (a, b), w in edge_w.items() if w >= min_edge]
        if top_edges > 0:
            edges.sort(key=lambda x: -x[2])
            edges = edges[:top_edges]
    # 用户自定义白名单：强制包含 + 补边（普通模式）。
    # --no-whitelist：白名单只用于焦点子图，普通图不读文件。
    manual_terms = load_manual_whitelist(focuson=focuson, use_file=not no_whitelist)
    if manual_terms:
        edges, manual_strs = _apply_manual_whitelist(
            manual_terms, edge_w, edges, top_edges, min_edge)
    else:
        manual_strs = set()
    node_set = set()
    for a, b, _ in edges:
        node_set.add(a)
        node_set.add(b)
    # 份额变化 + 颜色（pos/neg_cap 用 95 分位，而非 max，渐变更饱满）
    node_fc = {}
    pos_vals = []
    neg_vals = []
    for n in node_set:
        c1 = df_t.get(n, 0)
        c0 = df_b.get(n, 0)
        fc = log2fc(c1, tot_t, c0, tot_b)
        node_fc[n] = fc
        if fc >= 0:
            pos_vals.append(fc)
        else:
            neg_vals.append(-fc)
    pos_cap = percentile(pos_vals, 95) if pos_vals else 1.0
    neg_cap = percentile(neg_vals, 95) if neg_vals else 1.0
    if pos_cap == 0:
        pos_cap = 1.0
    if neg_cap == 0:
        neg_cap = 1.0
    node_size, edge_width, size_max = _make_geom(node_set, df_t, edges, ui)
    manual_size = max(size_max * 1.8, ui["node_size_min"] * 4)  # 白名单定位：大尺寸反差

    net = _net(ui, theme)
    for n in node_set:
        fc = node_fc[n]
        gold = n in manual_strs
        col = speed_color(fc, pos_cap, neg_cap, theme)
        node_color = {"background": col, "border": "#ffd700",
                      "highlight": {"border": "#ffd700"}} if gold else col
        sz = manual_size if gold else node_size(df_t.get(n, 0))
        net.add_node(n, label=n, size=sz, color=node_color, val=float(fc),
                     borderWidth=3 if gold else None,
                     title=f"{n} | doc_freq={df_t.get(n,0)} | log2FC={fc:+.2f}"
                           + (" | ★用户白名单" if gold else ""))
    for a, b, w in edges:
        fc = log2fc(df_t.get(a, 0) + df_t.get(b, 0), tot_t, df_b.get(a, 0) + df_b.get(b, 0), tot_b)
        col = speed_color(fc, pos_cap, neg_cap, theme)
        net.add_edge(a, b, width=edge_width(w), color=col, val=float(fc),
                     title=f"{a} × {b} = {w} | log2FC={fc:+.2f}")
    out_dir = os.path.join(VISUAL_DIR, "speed")
    os.makedirs(out_dir, exist_ok=True)
    suf = f"_e{min_edge}" + (f"_t{top_edges}" if top_edges else "") + f"_{ui_name}_{theme_name}"
    if pmi:
        suf += "_pmi"
    if category_discount is not None:
        suf += "_disc"
    path = os.path.join(out_dir, f"pair_speed_network_{base}-{target}{suf}.html")
    _write_html_with_theme(net, path, "speed", theme_name, pos_cap, neg_cap)
    print(f"[speed] {path} | nodes={len(node_set)} edges={len(edges)} 白名单={sorted(manual_strs)}" + (" PMI" if pmi else "") + (" DISCOUNT" if category_discount is not None else ""))


def export_accel(focal, prev, base, min_edge=20, top_edges=0, ui_name="small", theme_name="light",
                 focuson=None, nobackground=False, no_whitelist=False, pmi=False,
                 category_discount=None):
    """加速度图：二阶差 a = p(focal) - 2·p(prev) + p(base)，橙=加速 绿=减速。
    需 3 年数据：focal(T1)、prev(T0)、base(T-2)。"""
    ui = get_ui(ui_name)
    theme = get_theme(theme_name)
    # 焦点子图快速路径：duckDB 列查询，只查 focal 年有限节点
    manual_terms = load_manual_whitelist(focuson=focuson, use_file=not no_whitelist)
    if nobackground:
        edges, manual_strs, hop_map = _build_focus_subgraph(focal, manual_terms)
        if not edges:
            print("[err] 焦点子图为空", flush=True)
            return
        print(f"[focus] 焦点子图: 焦点词={sorted(manual_strs)} | nodes={len(set(hop_map))} "
              f"edges={len(edges)} (hop0={sum(1 for h in hop_map.values() if h==0)}, "
              f"hop1={sum(1 for h in hop_map.values() if h==1)}, hop2={sum(1 for h in hop_map.values() if h==2)})",
              flush=True)
        _render_focus("accel", focal, focal, prev, base, edges, manual_strs, hop_map,
                      ui, theme, ui_name, theme_name, min_edge, top_edges)
        return
    fl = load_phrases(focal)
    pl = load_phrases(prev)
    bl = load_phrases(base)
    if not fl or not pl or not bl:
        print("[err] 加速度需 3 年标注数据")
        return
    df_f = doc_freq(fl)
    df_p = doc_freq(pl)
    df_b = doc_freq(bl)
    tot_f = sum(df_f.values())
    tot_p = sum(df_p.values())
    tot_b = sum(df_b.values())
    # 共现（focal 年）
    edge_w = Counter()
    for s in fl:
        s_list = list(s)
        for i in range(len(s_list)):
            for j in range(i + 1, len(s_list)):
                a, b = s_list[i], s_list[j]
                edge_w[(a, b) if a < b else (b, a)] += 1
    edges = None
    if category_discount is not None:
        # v2.2 边加权选边：n_eff = w_cs·n_cs + w_oth·n_oth
        w_cs = category_discount
        edges = _select_edges_discount(edge_w, df_f, focal, top_edges=top_edges,
                                       min_edge=min_edge, w_cs=w_cs, w_oth=W_OTH)
        edges = _patch_fragments(edges, edge_w, max_edges=200)
    elif pmi:
        # v2 PMI 选边：排序键换成 PMI，w 保留原共现权重
        edges = _select_edges_pmi(edge_w, df_f, len(fl), top_edges=top_edges)
        # v2.1 P1 补边：把小分量缝合回主干（利用原始共现）
        edges = _patch_fragments(edges, edge_w)
    else:
        edges = [(a, b, w) for (a, b), w in edge_w.items() if w >= min_edge]
        if top_edges > 0:
            edges.sort(key=lambda x: -x[2])
            edges = edges[:top_edges]
    # 焦点词（--focuson 命令行注入 或 白名单文件）。
    # --no-whitelist：白名单只用于焦点子图，普通图不读文件。
    manual_terms = load_manual_whitelist(focuson=focuson, use_file=not no_whitelist)
    if manual_terms:
        edges, manual_strs = _apply_manual_whitelist(
            manual_terms, edge_w, edges, top_edges, min_edge)
    else:
        manual_strs = set()
    node_set = set()
    for a, b, _ in edges:
        node_set.add(a)
        node_set.add(b)
    # 二阶差 + 颜色 cap（95 分位）
    node_acc = {}
    pos_vals, neg_vals = [], []
    for n in node_set:
        pf = df_f.get(n, 0) / tot_f
        pp = df_p.get(n, 0) / tot_p
        pb = df_b.get(n, 0) / tot_b
        a = pf - 2 * pp + pb
        node_acc[n] = a
        if a >= 0:
            pos_vals.append(a)
        else:
            neg_vals.append(-a)
    pos_cap = percentile(pos_vals, 95) if pos_vals else 1.0
    neg_cap = percentile(neg_vals, 95) if neg_vals else 1.0
    if pos_cap == 0:
        pos_cap = 1.0
    if neg_cap == 0:
        neg_cap = 1.0
    node_size, edge_width, size_max = _make_geom(node_set, df_f, edges, ui)
    manual_size = max(size_max * 1.8, ui["node_size_min"] * 4)  # 白名单定位：大尺寸反差

    net = _net(ui, theme)
    for n in node_set:
        a = node_acc[n]
        gold = n in manual_strs
        col = accel_color(a, pos_cap, neg_cap, theme)
        node_color = {"background": col, "border": "#ffd700",
                      "highlight": {"border": "#ffd700"}} if gold else col
        sz = manual_size if gold else node_size(df_f.get(n, 0))
        net.add_node(n, label=n, size=sz,
                     color=node_color, val=float(a), borderWidth=3 if gold else None,
                     title=f"{n} | doc_freq={df_f.get(n,0)} | a={a:+.2e}"
                           + (" | ★用户白名单" if gold else ""))
    for a, b, w in edges:
        pf = (df_f.get(a, 0) + df_f.get(b, 0)) / tot_f
        pp = (df_p.get(a, 0) + df_p.get(b, 0)) / tot_p
        pb = (df_b.get(a, 0) + df_b.get(b, 0)) / tot_b
        av = pf - 2 * pp + pb
        net.add_edge(a, b, width=edge_width(w),
                     color=accel_color(av, pos_cap, neg_cap, theme), val=float(av),
                     title=f"{a} × {b} = {w} | a={av:+.2e}")
    out_dir = os.path.join(VISUAL_DIR, "accel")
    os.makedirs(out_dir, exist_ok=True)
    suf = f"_e{min_edge}" + (f"_t{top_edges}" if top_edges else "") + f"_{ui_name}_{theme_name}"
    if pmi:
        suf += "_pmi"
    if category_discount is not None:
        suf += "_disc"
    path = os.path.join(out_dir, f"pair_accel_network_{base}-{prev}-{focal}{suf}.html")
    _write_html_with_theme(net, path, "accel", theme_name, pos_cap, neg_cap)
    print(f"[accel] {path} | nodes={len(node_set)} edges={len(edges)} 白名单={sorted(manual_strs)}" + (" PMI" if pmi else "") + (" DISCOUNT" if category_discount is not None else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["static", "speed", "accel"], default="static")
    ap.add_argument("--year", type=int, default=2021)
    ap.add_argument("--target", type=int, default=2023)
    ap.add_argument("--prev", type=int, default=2022)
    ap.add_argument("--base", type=int, default=2021)
    ap.add_argument("--min-edge", type=int, default=20)
    ap.add_argument("--top-edges", type=int, default=0)
    ap.add_argument("--ui", choices=list(UI_PRESETS.keys()), default="small",
                    help="UI 档位：small=紧凑(力学易平衡)/standard=旧版美观(动态可拖拽,密集自适应)")
    ap.add_argument("--theme", choices=list(THEMES.keys()), default="light",
                    help="主题：light=简洁白底/starry=星空深蓝底/dark=暗色深灰底")
    ap.add_argument("--focuson", type=str, default=None,
                    help='焦点词（逗号分隔，最多5个），如 --focuson "transmon, surface code"。'
                         "优先于 whitelist_manual.txt。")
    ap.add_argument("--nobackground", action="store_true",
                    help="焦点子图模式：裁剪掉与焦点词无关的背景，只保留焦点词 2-hop 邻域"
                         "（每焦点词最多10条边）。")
    ap.add_argument("--no-whitelist", action="store_true",
                    help="普通图（静态/速度/加速度）跳过 whitelist_manual.txt。"
                         "白名单只用于焦点子图（--focuson/--nobackground），"
                         "反映用户个人问题，不污染全局图。")
    ap.add_argument("--pmi", action="store_true",
                    help="v2 领域平衡：用 PMI 排序选 Top-N 边，抵消高频词（LLM×real world）"
                         "对 Top-N 的垄断。边粗细仍按原共现权重。产物文件名加 _pmi 后缀。")
    ap.add_argument("--w-cs", type=float, default=None, metavar="W",
                    help="v2.2 边加权（用户方案·边=文章）：n_eff = w_cs·n_cs + n_oth。"
                         "单参数 W=cs 边权重（其他边恒 1），如 --w-cs 0.3。"
                         "按 n_eff 排 Top-N，可视化边粗细/节点大小用 raw 共现数。"
                         "与 --pmi 互斥（w-cs 优先）。产物文件名加 _disc 后缀。")
    ap.add_argument("--ann-dir", default=None,
                    help="归一化标注目录（默认 pipeline2/data/annotation/normalized；"
                         "可指向外部如 loomsci data/annotation/normalized）")
    args = ap.parse_args()
    global ANN
    if args.ann_dir:
        ANN = args.ann_dir
    disc = None
    if args.w_cs is not None:
        disc = float(args.w_cs)
    if args.mode == "static":
        export_static(args.year, args.min_edge, args.top_edges, args.ui, args.theme,
                      focuson=args.focuson, nobackground=args.nobackground,
                      no_whitelist=args.no_whitelist, pmi=args.pmi,
                      category_discount=disc)
    elif args.mode == "speed":
        export_speed(args.target, args.base, args.min_edge, args.top_edges, args.ui, args.theme,
                     focuson=args.focuson, nobackground=args.nobackground,
                     no_whitelist=args.no_whitelist, pmi=args.pmi,
                     category_discount=disc)
    else:
        export_accel(args.target, args.prev, args.base, args.min_edge, args.top_edges, args.ui,
                     args.theme, focuson=args.focuson, nobackground=args.nobackground,
                     no_whitelist=args.no_whitelist, pmi=args.pmi,
                     category_discount=disc)


if __name__ == "__main__":
    main()
