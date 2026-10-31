# -*- coding: utf-8 -*-
"""
试卷导入 Agent：试卷原文 → LLM 结构化标注 → 白名单校验 → 引擎可用的作答记录。

定位：在线路径的「导入」环节，把"上传试卷 → 错误画像 → 抽题"演示主线打通。
原则（与方案评估 v2 一致）：
  1. LLM 只做打标（结构化），不做任何计算与判分承诺；
  2. 知识点 / 错误机制必须落在 config 的白名单内，越界直接修复或丢弃；
  3. 无 API Key 时降级为规则标注（关键词匹配），现场演示永不中断；
  4. 输出与 engine.attempts / engine.originals 数据结构完全对齐。

依赖：pip install requests   （标准库 + requests，无其他依赖）
用法：
  export OPENAI_API_KEY=sk-...            # 可选；OpenAI 兼容接口均可
  export OPENAI_BASE_URL=https://...      # 可选，默认官方
  export OPENAI_MODEL=gpt-4o-mini         # 可选
  python paper_import_agent.py
"""
import os
import re
import json
import time
import uuid
import datetime
import requests

from config import (KP_ID, EM_ID, KNOWLEDGE_POINTS, ERROR_MECHANISMS, DIFFICULTY,
                    QUESTION_ROLE, SESSION_ID_UNASSIGNED)

TD = DIFFICULTY["tier_difficulty"]
KP_NAMES = list(KP_ID.keys())
EM_NAMES = list(EM_ID.keys())


# ============================================================
# ① 输入解析：试卷原文 + 考试日期 → 基础题目块
# ============================================================
SPLIT_RE = re.compile(r"\n\s*(?:\d{1,2})[.、．]\s*")

def parse_paper(text, exam_date=None, results=None):
    """
    text      : 试卷原文（含题干与学生作答，支持粘贴或 OCR 输出）
    exam_date : "2026-10-20" 或 "10-20" 等日期字符串；缺省按今天
    results   : 可选，["对","错",...] 或 ["√","×",...]，与题目顺序对应；
                缺省时交给 LLM 判断，仍缺的记 None → 由学生确认
    返回 [{text, days_ago, correct_or_None}]
    """
    if exam_date:
        # 支持 "MM-DD" 格式，自动补充当前年份
        if exam_date and '-' in exam_date and len(exam_date.split('-')) == 2:
            current_year = datetime.date.today().year
            exam_date = f"{current_year}-{exam_date}"
        exam = datetime.date.fromisoformat(exam_date)
    else:
        exam = datetime.date.today()
    days_ago = max(0, (datetime.date.today() - exam).days)
    chunks = [c.strip() for c in SPLIT_RE.split("\n" + text) if c.strip()]
    out = []
    for i, chunk in enumerate(chunks):
        correct = None
        if results and i < len(results):
            r = str(results[i])
            correct = True if r in ("对", "√", "✓", "正确", "T", "y") else \
                      False if r in ("错", "×", "✗", "X", "错误", "n") else None
        out.append({"text": chunk[:300], "days_ago": days_ago, "correct": correct})
    return out


# ============================================================
# ② LLM 打标（OpenAI 兼容接口；失败/无 Key → 规则兜底）
# ============================================================
PROMPT = """你是数学试卷标注助手。把下面这道初中/高中数学题打标，只输出 JSON：
{"kp": "知识点", "mech": "错误机制", "tier": 1, "is_correct": true/false/null}

约束：
- kp 必须从下表选一个：%s
- mech 必须从下表选一个：%s
- tier: 1=简单(单步套公式), 2=中等(两步需变形), 3=困难(新情境多知识点)
- is_correct: 若能判断学生对错给 true/false，判断不了给 null
- 若学生作答/得分信息不足，is_correct 给 null

题目与学生作答信息：
""" % ("、".join(KP_NAMES[:40]) + "……", "、".join(EM_NAMES))


def llm_annotate(items):
    """逐题调 LLM；返回与 items 等长的标注列表，失败位为 None。"""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return [None] * len(items)
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    out = []
    for it in items:
        try:
            r = requests.post(f"{base}/chat/completions", timeout=30, headers={
                "Authorization": f"Bearer {key}"}, json={
                "model": model, "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user",
                              "content": PROMPT + "\n" + it["text"]}]})
            m = json.loads(r.json()["choices"][0]["message"]["content"])
            out.append(m)
        except Exception:
            out.append(None)
    return out


# ============================================================
# ③ 规则兜底标注（无 Key / LLM 失败时的降级路径）
# ============================================================
KP_RULES = [("韦达定理", ["两根之和", "两根之积", "根之和", "根之积"]),
            ("二次函数", ["二次函数", "顶点", "对称轴", "抛物线"]),
            ("分式", ["分式", "化简"]),
            ("因式分解", ["分解因式", "因式分解"]),
            ("勾股定理", ["勾股", "直角边", "斜边"]),
            ("相似三角形", ["相似", "∽"]),
            ("全等三角形", ["全等"]),
            ("圆的基本性质", ["圆", "切线"]),
            ("一次函数", ["一次函数", "正比例"]),
            ("等差数列", ["等差"]),
            ("等比数列", ["等比"]),
            ("一元二次方程", ["一元二次", "解方程", "增长率", "降价", "售价", "利润", "百分率",
                              "配方法", "公式法", "根的判别", "根与系数", "韦达", "x²", "x^2", "方程"])]
EM_RULES = [("漏解", ["解集", "所有根", "全部"]),
            ("计算失误", ["计算", "算错"]),
            ("漏条件", ["定义域", "分母", "判别式"]),
            ("方法选择错误", ["方法", "思路"]),
            ("审题遗漏", ["审题"]),
            ("待确认", [])]


def rule_annotate(item):
    t = item["text"]
    kp = next((k for k, pats in KP_RULES if any(p in t for p in pats)), None)
    mech = next((m for m, pats in EM_RULES if any(p in t for p in pats)), "待确认")
    tier = 2  # 规则兜底不判难度，按最常见档
    return {"kp": kp, "mech": mech, "tier": tier,
            "is_correct": item["correct"]}


# ============================================================
# ④ 白名单校验 + 结构转换 → engine 数据结构
# ============================================================
def _fix_kp(name):
    if name in KP_ID:
        return KP_ID[name]
    best = next((n for n in KP_NAMES if n and name and (n in name or name in n)), None)
    return KP_ID[best] if best else None      # 修不回白名单 → 丢弃该字段

def _fix_mech(name):
    if name in EM_ID:
        return EM_ID[name]
    return EM_ID["待确认"]

def build_records(items, annotations, qid_start=1, session_id=None, import_id=None):
    """校验并转换为 engine.recommend 直接可用的 attempts / originals。

    Attempt 三字段在这里一次写齐（UI 规格 §8 / 看板 R13、P1-5），下游不必再猜：
      session_id         —— 一次导入 = 一轮。调用方不传就写 SESSION_ID_UNASSIGNED，
                            由 engine.norm_attempts() 按 import_id / 日期统一编号
      question_role      —— original：导入的都是学生自己原卷上的题
      error_mechanism_id —— 与 mech 同值，供只认新字段名的下游使用
    """
    attempts, originals, dropped = [], [], []
    # 一次导入 = 一批：import_id 让同一天导两张卷也不会被并成同一轮
    import_id = import_id or f"imp{uuid.uuid4().hex[:8]}"
    qid = qid_start
    for it, ann in zip(items, annotations):
        ann = ann or rule_annotate(it)
        kid = _fix_kp(ann.get("kp"))
        if kid is None:
            dropped.append(it["text"])
            continue
        eid = _fix_mech(ann.get("mech"))
        tier = ann.get("tier") if ann.get("tier") in (1, 2, 3) else 2
        correct = it["correct"] if it["correct"] is not None else ann.get("is_correct")
        rec = {"qid": qid, "kp": kid, "mech": eid, "tier": tier,
               "tier_difficulty": TD[tier], "text": it["text"],
               "days_ago": it["days_ago"],
               "correct": True if correct is True else False,
               "session_id": session_id if session_id is not None else SESSION_ID_UNASSIGNED,
               "question_role": QUESTION_ROLE["original"],
               "error_mechanism_id": eid,
               "import_id": import_id,
               "source": "import",
               "my_answer": it.get("my_answer", ""),
               "correct_answer": it.get("correct_answer", ""),
               "bbox": it.get("bbox", [])}
        if correct is None:                   # 待学生确认，先按错记入（保守策略）
            rec["needs_confirm"] = True
        attempts.append(rec)
        originals.append(dict(rec))           # 学生错题原题进原题池（独立副本，便于各自改字段）
        qid += 1
    return attempts, originals, dropped


def import_paper(text, exam_date=None, results=None, session_id=None):
    """主入口：一段试卷原文 → (attempts, originals, dropped, n_llm)"""
    items = parse_paper(text, exam_date, results)
    anns = llm_annotate(items)
    n_llm = sum(1 for a in anns if a is not None)
    return build_records(items, anns, session_id=session_id) + (n_llm,)


# ============================================================
# Demo：一张样卷走通「导入 → 画像 → 抽题」
# ============================================================
SAMPLE_PAPER = """1. 解方程 x² − 7x + 12 = 0（学生只写了 x=3）
2. 分解因式 3a² − 27
3. 已知直角三角形两直角边 6 和 8，求斜边长
4. 求 y = x² − 4x + 3 的顶点坐标
5. 化简 (x² − 9)/(x − 3)
6. △ABC∽△DEF，AB=4, DE=6, AC=5，求 DF 的长"""

if __name__ == "__main__":
    from engine import kp_profile, recommend, kp_name, em_name

    print("=" * 72)
    print("试卷导入 Agent 演示（样卷 6 题，考试日期 12 天前）")
    print("LLM 状态 :", "已启用" if os.environ.get("OPENAI_API_KEY") else "未配置 → 规则标注兜底")
    print("=" * 72)

    exam = (datetime.date.today() - datetime.timedelta(days=12)).isoformat()
    attempts, originals, dropped, n_llm = import_paper(
        SAMPLE_PAPER, exam_date=exam,
        results=["错", "对", "对", "错", "对", "错"])

    print(f"\n导入 {len(attempts)} 题，白名单外丢弃 {len(dropped)} 条，LLM 标注 {n_llm} 条")
    print(f"\n{'qid':<5}{'知识点':<14}{'错误机制':<12}{'档':<7}{'对错':<6}题干")
    for a in attempts:
        c = {True: "✓", False: "×"}.get(a["correct"], "?")
        flag = " (待确认)" if a.get("needs_confirm") else ""
        print(f"{a['qid']:<5}{kp_name(a['kp']):<14}{em_name(a['mech']):<12}"
              f"{ {1: '简单', 2: '中等', 3: '困难'}.get(a['tier'], a['tier']):<6}{c+flag:<7}{a['text'][:24]}")

    print("\n—— 导入数据直接驱动抽题引擎 ——")
    from engine import build_bank              # 演示用预置题库；线上为离线生成+sympy校验的真实题库
    bank = build_bank()
    res, prof, prior = recommend(attempts, bank, originals, 5, seed=42)
    print(f"全局先验错误率 = {prior:.3f}")
    for kid, p in sorted(prof.items(), key=lambda x: -x[1]["err"]):
        print(f"  {kp_name(kid):<12} 错误率 {p['err']:.0%}  样本 {p['n']}")
    print(f"\n推荐 5 题：")
    for i, r in enumerate(res, 1):
        print(f"  {i}. [{r['src']}] {r['q']['text'][:26]}")
    print("\n演示主线闭环：上传试卷 → Agent 打标 → 画像 → 抽题 ✓")


# ============================================================
# ⑤ 照片导入：OCR 题干 + 颜色检测批改标记（照片 → attempts）
#    - 题干：HSV 滤出近黑印刷字 → tesseract chi_sim（TSV 带行坐标）
#    - 批改：红色像素聚类；× 小而对称(~21px)，√ 大且左重右轻(~32px)
#    - 生产建议换多模态 LLM（视觉）做整页结构化；本实现为零依赖兜底
# ============================================================
def _ocr_printed_lines(img):
    """滤出黑色印刷字并 OCR，返回 [(行文本, 行y中心)]。"""
    import subprocess
    import tempfile
    import numpy as np
    from PIL import Image
    a = np.asarray(img.convert("HSV"), dtype=int)
    black = (a[..., 1] < 60) & (a[..., 2] < 120)          # 低饱和+低亮度=印刷字
    mask = Image.fromarray(np.where(black, 0, 255).astype("uint8"))

    # 使用tempfile创建唯一的临时文件
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp_png = f.name
    tmp_base = tmp_png[:-4]  # 去掉.png扩展名

    try:
        mask.save(tmp_png)
        result = subprocess.run(["tesseract", tmp_png, tmp_base, "-l", "chi_sim", "--psm", "6", "tsv"],
                       capture_output=True)
        if result.returncode != 0:
            stderr_msg = result.stderr.decode('utf-8', errors='replace') if result.stderr else ''
            raise RuntimeError(f"Tesseract OCR failed: {stderr_msg}")
        lines = {}
        with open(tmp_base + ".tsv", encoding="utf-8", errors="replace") as f:
            rows = [l.rstrip("\n").split("\t") for l in f]
    finally:
        # 清理临时文件
        import os
        for ext in [".png", ".tsv"]:
            try:
                if ext == ".png":
                    os.unlink(tmp_png)
                else:
                    os.unlink(tmp_base + ext)
            except:
                pass
    hdr = rows[0]
    for row in rows[1:]:
        if len(row) != len(hdr):
            continue
        d = dict(zip(hdr, row))
        text = d.get("text", "").strip()
        if not text or float(d.get("conf", -1)) < 30:
            continue
        key = (d["block_num"], d["par_num"], d["line_num"])
        y0, h0 = int(d["top"]), int(d["height"])
        e = lines.setdefault(key, {"t": "", "y": y0 + h0 / 2})
        e["t"] += text
        e["y"] = min(e["y"], y0 + h0 / 2) if False else e["y"]
    out = []
    for e in lines.values():
        t = e["t"].strip()
        if re.match(r"^\d{1,2}\s*[^\d]", t):           # 题号行：1 xxx / 2.xxx / 7，xxx
            out.append((t[:300], e["y"]))
    return out


def _detect_marks(img):
    """检测红色批改标记，返回 [(y_center, is_correct)]，并过滤批语等大块红字。"""
    import numpy as np
    a = np.asarray(img.convert("RGB"), dtype=int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    red = (r > 130) & (r - g > 55) & (r - b > 55)
    ys, xs = np.nonzero(red)
    if len(ys) == 0:
        return []
    order = np.argsort(ys)
    ys, xs = ys[order], xs[order]
    clusters, cur = [], [0]
    for k in range(1, len(ys)):
        if ys[k] - ys[k - 1] > 40:
            clusters.append(cur); cur = []
        cur.append(k)
    clusters.append(cur)
    marks = []
    for idx in clusters:
        if len(idx) < 30:
            continue
        x0, x1 = xs[idx].min(), xs[idx].max()
        if x1 - x0 > 200:                            # 批语等整行红字
            continue
        cy, cx = ys[idx].mean(), xs[idx].mean()
        w = x1 - x0
        ml = (xs[idx] < cx - 3).sum()
        mr = (xs[idx] > cx + 3).sum()
        # √：横向更宽(w>26) 且左半质量明显大于右半（长捺收笔在左下->右上）
        is_correct = w > 26 and ml > mr * 1.25
        marks.append((float(cy), bool(is_correct)))
    return marks


def import_paper_image(path, exam_date=None, prefer_vlm=True, session_id=None):
    """照片统一入口：有 OPENAI_API_KEY 走视觉 LLM（真实照片主路径），
    否则/失败时退回 OCR 兜底（适合排版规整的单栏卷）。"""
    if prefer_vlm and os.environ.get("OPENAI_API_KEY"):
        r = import_paper_image_vlm(path, exam_date, session_id=session_id)
        if r[0] is not None:
            return r[0], r[1], r[2]
    from PIL import Image
    img = Image.open(path).convert("RGB")
    lines = _ocr_printed_lines(img)
    marks = _detect_marks(img)
    days = 0
    if exam_date:
        # 支持 "MM-DD" 格式，自动补充当前年份
        if '-' in exam_date and len(exam_date.split('-')) == 2:
            current_year = datetime.date.today().year
            exam_date = f"{current_year}-{exam_date}"
        days = max(0, (datetime.date.today() -
                       datetime.date.fromisoformat(exam_date)).days)
    items = []
    if len(marks) == len(lines):                     # 常规批改：一题一标记，按 y 序配对
        pairs = list(zip(lines, sorted(marks, key=lambda m: m[0])))
    else:                                            # 数量不齐：近邻 140px 内取标记
        pairs = [(l, min(marks, key=lambda m: abs(m[0] - l[1]), default=None))
                 for l in lines]
    for (text, y), mk in pairs:
        correct = None
        if mk is not None and (len(marks) == len(lines) or abs(mk[0] - y) <= 140):
            correct = mk[1]
        items.append({"text": text, "days_ago": days, "correct": correct})
    return build_records(items, [None] * len(items), session_id=session_id)


# ============================================================
# ⑥ 视觉 LLM 路径：真实照片（多栏/手写/异形批改）的主路径
#    配置 OPENAI_API_KEY 后自动启用；无 Key 退回 ⑤ 的 OCR 兜底
# ============================================================
VLM_PROMPT = """你是数学试卷照片标注助手。图中是一张学生做过、老师批改过的数学试卷。
请读出图中每一道有明确题号的题目，识别学生的答案、正确答案、题目在图中的位置，判断学生该题的对错（依据老师的勾、叉、红字批注或得分），
只输出 JSON：{"questions":[{"text":"题干(截取前60字)","my_answer":"学生写的答案","correct_answer":"正确答案或老师批注的答案","bbox":[x,y,w,h],"kp":"知识点","mech":"错误机制","tier":1,"is_correct":true/false}]}

约束：
- text: 题目题干内容（截取前60字）
- my_answer: 学生在卷面上写的答案（手写部分），如果没有或看不清写"未作答"
- correct_answer: 正确答案。优先读取老师批注的红字答案，如果没有则根据题目推断正确答案，实在无法确定写空字符串""
- bbox: **关键**题目在图中的精确位置，格式为[x, y, w, h]
  - 坐标系说明：图片左上角为原点(0,0)，向右为x轴正方向，向下为y轴正方向
  - x: 题目区域左上角的x坐标（从图片左边缘到题目左边界的距离）
  - y: 题目区域左上角的y坐标（从图片顶部到题目顶部的距离）
  - w: 题目区域的宽度（从左边界到右边界）
  - h: 题目区域的高度（从顶部到底部）
  - 归一化：将实际像素坐标除以图片尺寸后乘以1000，例如图片宽2000px，题目左边界在400px处，则x=400/2000*1000=200
  - 必须包含：题号、题干、学生答案（手写）、老师批注（红笔）的完整区域，要留够上下左右的边距
  - 例如：如果图片宽2000px高3000px，某题从左边200px、顶部600px开始，宽1600px、高400px，则bbox=[100, 200, 800, 133]
- kp 必须从知识点表中选：%s
- mech（错误机制）**重要规则**：
  %s

  **如何判断错误机制**（按优先级从高到低）：
  1. 如果is_correct为true（老师判对了），mech必须填"待确认"
  2. 如果学生未作答或答案看不清，填"待确认"
  3. 如果老师有红笔批注指出具体错误（如"计算错误"、"审题不清"等），根据批注内容选择对应机制
  4. 如果学生答错且无批注，根据题目类型和学生答案分析：
     - 选择题答错 → 优先考虑"概念混淆"或"条件遗漏"
     - 计算题答错 → 优先考虑"运算错误"
     - 几何题答错 → 优先考虑"图形理解偏差"或"性质应用错"
     - 应用题答错 → 优先考虑"建模错误"或"题意理解偏差"
     - 代数变形错误 → 考虑"符号处理错"或"公式记忆错"
  5. 实在无法判断时才用"待确认"

- tier: 1=简单(单步套公式), 2=中等(两步需变形), 3=困难(新情境多知识点)
- is_correct: **必须明确判断**
  - 学生答案 = 正确答案（完全一致或等价） → true
  - 学生答案 ≠ 正确答案 → false
  - 只有在没有学生答案（空白/未作答）或学生答案完全看不清时才用 null
  - **选择题**：直接比较选项字母（A/B/C/D），一致为true，不一致为false
- 只输出 JSON，不要多余文字

**示例分析**：
- 题目：下列四个实数中，最小的数是  学生答：C  正确答案：C → is_correct=true, mech="待确认"
- 题目：计算(-a³)⁴  学生答：-a¹²  正确答案：a¹² → is_correct=false, mech="符号处理错"（负号处理错误）
- 题目：不等式kx+b<0的解集为x<2  学生答：A（图象过(2,0)向下）正确答案：B → is_correct=false, mech="概念混淆"（不等式与函数图象关系理解错误）
""" % ("、".join(KP_NAMES[:40]) + "……",
       "\n  ".join(f"{name}：{desc}" for name, desc in ERROR_MECHANISMS))


def _vlm_annotate_image(path):
    """视觉 LLM 整页结构化；返回 (题目列表标注, None) 或 (None, 错误)。"""
    import base64
    from PIL import Image
    import time

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None, "no_key"
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    # 压缩图片加速传输
    img = Image.open(path)
    if img.width > 2000 or img.height > 2000:
        ratio = min(2000 / img.width, 2000 / img.height)
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
        print(f"[VLM] 图片压缩: {img.width}x{img.height}")

    import io
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode()

    # 重试机制：最多3次，每次60秒超时
    max_retries = 3
    retry_delay = 2  # 重试前等待秒数

    for attempt in range(max_retries):
        try:
            if attempt > 0:
                print(f"[VLM] 第 {attempt + 1} 次重试...")
                time.sleep(retry_delay)

            print(f"\n[VLM] 调用模型: {model} (尝试 {attempt + 1}/{max_retries})")
            print(f"[VLM] API地址: {base}")

            r = requests.post(f"{base}/chat/completions", timeout=60, headers={
                "Authorization": f"Bearer {key}"}, json={
                "model": model, "temperature": 0, "max_tokens": 8000,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": VLM_PROMPT},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]})

            print(f"[VLM] HTTP状态: {r.status_code}")

            if r.status_code != 200:
                error_msg = f"HTTP {r.status_code}"
                try:
                    error_detail = r.json()
                    error_msg = str(error_detail.get('error', error_detail))
                except:
                    error_msg = r.text[:200]
                print(f"[VLM] API错误: {error_msg}")

                # 4xx错误不重试（认证、配额等问题）
                if 400 <= r.status_code < 500:
                    return None, error_msg
                # 5xx错误重试
                if attempt < max_retries - 1:
                    continue
                return None, error_msg

            resp_json = r.json()
            if "error" in resp_json:
                print(f"[VLM] API错误: {resp_json['error']}")
                return None, str(resp_json['error'])

            content = resp_json["choices"][0]["message"]["content"]
            print(f"[VLM] ========== AI完整返回 ==========")
            print(content)
            print(f"[VLM] ========== 返回结束 ==========")

            content = re.sub(r"^```(?:json)?|```$", "", content.strip(),
                             flags=re.MULTILINE).strip()
            parsed = json.loads(content)
            questions = parsed.get("questions", [])
            print(f"[VLM] 解析到 {len(questions)} 道题")

            for i, q in enumerate(questions):
                print(f"[VLM] 题{i+1}: text={q.get('text','')[:30]}, bbox={q.get('bbox')}, my_answer={q.get('my_answer','')[:20]}, correct_answer={q.get('correct_answer','')[:20]}")

            # 成功返回
            return questions, None

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            print(f"[VLM] 网络错误: {type(e).__name__} - {e}")
            if attempt < max_retries - 1:
                continue
            return None, f"网络超时，已重试{max_retries}次"

        except json.JSONDecodeError as e:
            print(f"[VLM] JSON解析错误: {e}")
            print(f"[VLM] 原始内容: {content[:500]}")
            if attempt < max_retries - 1:
                continue
            return None, "AI返回格式错误"

        except Exception as e:
            import traceback
            print(f"[VLM] 异常: {e}")
            print(traceback.format_exc())
            # 未知错误不重试
            return None, str(e)

    return None, "未知错误"


def _verify_bboxes(image_path, questions, key, base, model):
    """裁切图片后让AI验证bbox是否准确，如果不准确则修正"""
    from PIL import Image
    import base64
    import io

    img = Image.open(image_path)
    width, height = img.size
    print(f"[验证] 原图尺寸: {width}x{height}")

    verified = []
    for i, q in enumerate(questions):
        bbox = q.get("bbox", [])
        if not bbox or len(bbox) != 4:
            print(f"[验证] 题{i+1}: 无bbox，跳过")
            verified.append(q)
            continue

        # 转换归一化坐标到实际像素
        x, y, w, h = bbox
        actual_x = int(x * width / 1000)
        actual_y = int(y * height / 1000)
        actual_w = int(w * width / 1000)
        actual_h = int(h * height / 1000)

        # 边界检查
        actual_x = max(0, min(actual_x, width))
        actual_y = max(0, min(actual_y, height))
        actual_w = max(1, min(actual_w, width - actual_x))
        actual_h = max(1, min(actual_h, height - actual_y))

        # 裁切
        cropped = img.crop((actual_x, actual_y, actual_x + actual_w, actual_y + actual_h))

        # 转base64
        buf = io.BytesIO()
        cropped.save(buf, format="JPEG", quality=85)
        cropped_b64 = base64.b64encode(buf.getvalue()).decode()

        # 让AI验证
        verify_prompt = f"""这是从试卷中裁切出的一道题的区域。
原题文本应该是：{q.get('text', '')[:60]}

请检查这个裁切区域是否完整包含了：
1. 题号
2. 题干完整内容
3. 学生的手写答案
4. 老师的批改标记（红笔勾叉或批注）

只输出JSON：{{"is_correct": true/false, "reason": "判断理由", "suggested_bbox": [x,y,w,h]}}

- is_correct: true表示裁切准确，false表示需要调整
- reason: 说明裁切是否准确的理由
- suggested_bbox: 如果is_correct为false，给出建议的bbox（归一化坐标0-1000）；如果is_correct为true，返回null"""

        try:
            verify_resp = requests.post(f"{base}/chat/completions", timeout=60, headers={
                "Authorization": f"Bearer {key}"}, json={
                "model": model, "temperature": 0, "max_tokens": 500,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": verify_prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{cropped_b64}"}}]}]})

            verify_result = verify_resp.json()
            verify_content = verify_result["choices"][0]["message"]["content"]
            verify_data = json.loads(verify_content)

            is_correct = verify_data.get("is_correct", True)
            reason = verify_data.get("reason", "")
            suggested = verify_data.get("suggested_bbox")

            print(f"[验证] 题{i+1}: {'✓准确' if is_correct else '✗需调整'} - {reason}")

            # 如果不准确且有建议坐标，使用建议坐标
            if not is_correct and suggested and len(suggested) == 4:
                print(f"[验证] 题{i+1}: 原bbox{bbox} -> 修正bbox{suggested}")
                q["bbox"] = suggested

        except Exception as e:
            print(f"[验证] 题{i+1}: 验证失败 {e}，保留原bbox")

        verified.append(q)

    return verified

def import_paper_image_vlm(path, exam_date=None, session_id=None):
    """照片主路径（需 OPENAI_API_KEY）：→ (attempts, originals, dropped, 来源)"""
    anns, err = _vlm_annotate_image(path)
    if anns is None:
        return None, None, None, err
    days = 0
    if exam_date:
        # 支持 "MM-DD" 格式，自动补充当前年份
        if '-' in exam_date and len(exam_date.split('-')) == 2:
            current_year = datetime.date.today().year
            exam_date = f"{current_year}-{exam_date}"
        days = max(0, (datetime.date.today() -
                       datetime.date.fromisoformat(exam_date)).days)
    items = [{"text": a.get("text", "")[:300], "days_ago": days,
              "correct": None,
              "my_answer": a.get("my_answer", ""),
              "correct_answer": a.get("correct_answer", ""),
              "bbox": a.get("bbox", [])} for a in anns]
    attempts, originals, dropped = build_records(items, anns, session_id=session_id)
    return attempts, originals, dropped, "vlm"
