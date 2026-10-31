# -*- coding: utf-8 -*-
"""
抽题引擎：学生决定做几题，系统决定做哪些题。

输入：作答历史 attempts、题库 bank、原题池 originals、选题量 N
输出：N 道题 + 难度分布

字段约定（与 UI 规格 §8、bank/ingest.py 数据契约对齐）：
  Question : qid, text, kp(名或 id) / kp_id, tier / difficulty_tier,
             dedup_key（独立去重键，sha1）, is_anchor（固定难度探针题）
  Attempt  : qid, kp, mech / error_mechanism_id, tier, tier_difficulty,
             correct, days_ago, session_id, question_role, is_anchor
两套字段名都可进引擎，入口处统一归一化（norm_bank / norm_attempts）。

运行：python engine.py
"""
import random
import math
from collections import defaultdict

from config import (WEIGHT, DIFFICULTY, DISPLAY, KP_ID, EM_ID,
                    kp_name, em_name, HIGH_FREQ_KP,
                    QUESTION_ROLE, ANCHOR, SESSION_ID_UNASSIGNED)


# ============================================================
# ⓪ 归一化：兼容 ingest.py 产出与引擎内部结构
# ============================================================
def q_kpid(q):
    """知识点主键 -> int id。兼容 kp_id / kp(中文名) / kp(int)"""
    if q.get("kp_id"):
        return int(q["kp_id"])
    kp = q.get("kp")
    if isinstance(kp, bool):
        return 0
    if isinstance(kp, int):
        return kp
    if isinstance(kp, str):
        return KP_ID.get(kp, 0)
    return 0


def q_tier(q):
    """难度档 -> 1/2/3。兼容 difficulty_tier（ingest）与 tier（引擎）"""
    t = q.get("tier") or q.get("difficulty_tier") or ANCHOR["fallback_tier"]
    try:
        t = int(t)
    except (TypeError, ValueError):
        return ANCHOR["fallback_tier"]
    return t if t in (1, 2, 3) else ANCHOR["fallback_tier"]


def q_dedup(q):
    """独立去重键：优先 dedup_key（sha1，非全文比对），回退题干全文"""
    return q.get("dedup_key") or q.get("text") or q.get("qid")


def norm_bank(bank):
    """题库归一化：补 kp(int) / tier / tier_difficulty / dedup_key / is_anchor"""
    out = []
    for q in bank or []:
        d = dict(q)
        d["kp"] = q_kpid(q)
        d["tier"] = q_tier(q)
        d["tier_difficulty"] = DIFFICULTY["tier_difficulty"][d["tier"]]
        d["dedup_key"] = q_dedup(q)
        d["is_anchor"] = bool(q.get("is_anchor"))
        out.append(d)
    return out


# Attempt 必备字段：写入前必须齐全（UI 规格 §8 / 看板 R13、P1-5）
ATTEMPT_FIELDS = ("qid", "kp", "mech", "error_mechanism_id", "tier",
                  "tier_difficulty", "correct", "days_ago",
                  "session_id", "question_role")


def _attempt_group(a):
    """未分配轮次的归组键：同一次导入 > 同一天"""
    if a.get("import_id") not in (None, ""):
        return ("import", str(a["import_id"]))
    return ("day", int(a.get("days_ago", 0)))


def norm_attempts(attempts):
    """
    Attempt 归一化：补齐三个字段（UI 规格 §8 / 看板 R13、P1-5）
      session_id        —— 第几轮练习（一轮 = 一张卷 / 一次练习）
      question_role     —— original（学生原卷原题）/ bank（题库题）
      error_mechanism_id—— 错误机制 id（与旧字段 mech 同值，双向兼容）

    session_id 补法：已分配的正整数原样保留；未分配的（缺失 / None /
    SESSION_ID_UNASSIGNED）按「同一次导入 import_id」优先、其次「同一天 days_ago」
    归组，接在已用最大编号之后顺次分配 —— 同一天导两张卷不会并成一轮。

    question_role 补法：缺失时按来源判定，source=import 记 original，其余记 bank。
    导入端已显式写 original，这里只兜老数据。
    """
    if not attempts:
        return []

    used = {a["session_id"] for a in attempts
            if isinstance(a.get("session_id"), int) and a["session_id"] > 0}
    next_sid = (max(used) + 1) if used else 1

    # 未分配轮次的组：按时间先后编号（days_ago 越大越早）
    pending = {}
    for a in attempts:
        sid = a.get("session_id")
        if isinstance(sid, int) and sid > 0:
            continue
        pending.setdefault(_attempt_group(a), None)

    def _order(key):
        kind, val = key
        if kind == "day":
            return (0, -val, "")
        # 导入批次：用组内最早的作答日（最大 days_ago）作时间坐标
        days = max(int(x.get("days_ago", 0)) for x in attempts
                   if _attempt_group(x) == key)
        return (0, -days, val)

    for key in sorted(pending, key=_order):
        pending[key] = next_sid
        next_sid += 1

    out = []
    for a in attempts:
        d = dict(a)
        d["kp"] = q_kpid(a) or a.get("kp")
        mech = a.get("mech")
        if mech is None:
            mech = a.get("error_mechanism_id")
        d["mech"] = 0 if mech is None else mech
        d["error_mechanism_id"] = d["mech"]
        d["tier"] = q_tier(a)
        d["tier_difficulty"] = a.get("tier_difficulty") or \
            DIFFICULTY["tier_difficulty"][d["tier"]]
        sid = a.get("session_id")
        if not (isinstance(sid, int) and sid > 0):
            sid = pending[_attempt_group(a)]
        d["session_id"] = sid
        role = a.get("question_role")
        if role not in QUESTION_ROLE.values():
            role = (QUESTION_ROLE["original"] if a.get("source") == "import"
                    else QUESTION_ROLE["bank"])
        d["question_role"] = role
        d["is_anchor"] = bool(a.get("is_anchor"))
        out.append(d)
    return out


def next_session_id(attempts):
    """下一轮的 session_id（一轮都没有时返回 1）；只认已分配的正整数"""
    ids = [a["session_id"] for a in (attempts or [])
           if isinstance(a.get("session_id"), int) and a["session_id"] > 0]
    return (max(ids) + 1) if ids else 1


def make_attempt(q, correct, days_ago=0, session_id=None, role="bank",
                 mech=None, source=None):
    """构造一条规范化的 Attempt（前端 / 导入端写记录统一走这里）

    session_id 不传时写 SESSION_ID_UNASSIGNED，交由 norm_attempts() 统一编号；
    旧实现用 next_session_id([]) 兜底，恒得 1 —— 所有记录挤成同一轮，等于没这字段。
    """
    eid = mech if mech is not None else q.get("mech")
    return {
        "qid": q.get("qid"),
        "kp": q_kpid(q),
        "mech": 0 if eid is None else eid,
        "error_mechanism_id": 0 if eid is None else eid,
        "tier": q_tier(q),
        "tier_difficulty": DIFFICULTY["tier_difficulty"][q_tier(q)],
        "correct": bool(correct),
        "days_ago": int(days_ago),
        "session_id": session_id if session_id is not None else SESSION_ID_UNASSIGNED,
        "question_role": role if role in QUESTION_ROLE.values() else QUESTION_ROLE["bank"],
        "is_anchor": bool(q.get("is_anchor")),
        **({"source": source} if source else {}),
    }


def check_attempts(attempts, require_session=True):
    """字段完整性自检（看板 P1-5 完成标准）：返回 (是否合规, 问题列表)

    require_session=False：允许 session_id 仍为未分配标记（导入端刚落盘、还没过
    norm_attempts 的中间态）；落盘前应传 True 或先跑一遍 norm_attempts。
    """
    probs = []
    for i, a in enumerate(attempts or []):
        for k in ATTEMPT_FIELDS:
            if k not in a:
                probs.append(f"#{i} 缺字段 {k}")
        if a.get("question_role") not in QUESTION_ROLE.values():
            probs.append(f"#{i} question_role 非法：{a.get('question_role')!r}")
        sid = a.get("session_id")
        if require_session and not (isinstance(sid, int) and sid > 0):
            probs.append(f"#{i} session_id 未分配：{sid!r}")
        if a.get("mech") != a.get("error_mechanism_id"):
            probs.append(f"#{i} error_mechanism_id 与 mech 不一致")
    return (not probs), probs


# ============================================================
# ① 知识点画像：近期错误率（时间衰减 + 贝叶斯平滑）
# ============================================================
def kp_profile(attempts):
    """返回 {kp_id: {err, days_since, n, alpha, window_acc}}"""
    W = WEIGHT
    by_kp = defaultdict(list)
    for a in attempts:
        by_kp[a["kp"]].append(a)

    # 全局先验：有数据用实测全局加权错误率，无数据用固定先验
    g_num = sum(W["lambda_decay"] ** a["days_ago"] * (0 if a["correct"] else 1)
                for a in attempts)
    g_den = sum(W["lambda_decay"] ** a["days_ago"] for a in attempts)
    prior = (g_num / g_den) if g_den > 0 else W["prior_err_rate"]
    if g_den > 0:   # 全局也做一次平滑，避免早期极端
        prior = (g_num + W["alpha_smooth"] * W["prior_err_rate"]) / (g_den + W["alpha_smooth"])

    prof = {}
    for kid, recs in by_kp.items():
        num = sum(W["lambda_decay"] ** a["days_ago"] * (0 if a["correct"] else 1)
                  for a in recs)
        den = sum(W["lambda_decay"] ** a["days_ago"] for a in recs)
        err = (num + W["alpha_smooth"] * prior) / (den + W["alpha_smooth"])

        days_since = min(a["days_ago"] for a in recs)

        # 展示用：滑动窗口内正确率（报告「近期正确率」，不参与 α 更新）
        win_all = sorted(recs, key=lambda a: a["days_ago"])[-DISPLAY["mastery_window"]:]
        acc = sum(1 for a in win_all if a["correct"]) / len(win_all)

        # 能力 α：只由固定难度参照（is_anchor=True，tier2 探针）的作答更新，
        # 主练习题不回写 α（R25，切断"推难题→正确率降→α 降"的自指循环）
        win = [a for a in win_all if a.get("is_anchor")]
        if not win:                    # 尚无参照作答（冷启动）：α 停在初值，不伪造
            alpha = DIFFICULTY["alpha_init"]
        else:
            dbar = sum(a["tier_difficulty"] for a in win) / len(win)
            aacc = sum(1 for a in win if a["correct"]) / len(win)
            alpha = max(DIFFICULTY["alpha_min"],
                        min(DIFFICULTY["alpha_max"], dbar + (aacc - 0.5) * DIFFICULTY["alpha_lr"] * 2))
        prof[kid] = {"err": err, "days_since": days_since, "n": len(recs),
                     "alpha": alpha, "window_acc": acc}
    return prof, prior


def mech_rate(attempts):
    """错误机制复发率（时间加权 + 平滑），返回 {em_id: rate}"""
    W = WEIGHT
    num = defaultdict(float)
    den = defaultdict(float)
    for a in attempts:
        w = W["lambda_decay"] ** a["days_ago"]
        den[a["mech"]] += w
        if not a["correct"]:
            num[a["mech"]] += w
    tot_den = sum(den.values()) or 1.0
    return {e: (num[e] + W["alpha_smooth"] * (sum(num.values()) / tot_den))
                / (den[e] + W["alpha_smooth"]) for e in den}


# ============================================================
# ② 难度：连续混合权重，不硬切档
# ============================================================
def tier_weights(alpha, sigma=None):
    sigma = sigma or DIFFICULTY["sigma"]
    td = DIFFICULTY["tier_difficulty"]
    w = {t: math.exp(-((td[t] - alpha) ** 2) / (2 * sigma ** 2)) for t in td}
    s = sum(w.values())
    return {t: x / s for t, x in w.items()}


# ============================================================
# ③ 抽题
# ============================================================
def recommend(attempts, bank, originals, N, recent_questions=None, seed=None,
              lock_kp=None, session_index=None):
    """
    attempts   : [{kp, mech, tier, tier_difficulty, correct, days_ago, qid,
                   session_id, question_role, error_mechanism_id, is_anchor}]
    bank       : [{qid, kp, mech, tier, text, answer, dedup_key, is_anchor}]
    originals  : 同结构，来自学生上传的错题
    N          : 学生选择的题量
    recent_questions: 近 3 轮出现过的 qid 集合
    lock_kp    : 锁定的知识点（kp 中文名 / int id / 列表），锁定后只从该知识点出题
    session_index   : 当前是第几轮（1-based），仅作外部记录用
    """
    rng = random.Random(seed)
    W = WEIGHT
    recent_questions = recent_questions or set()

    # 入口归一化：两套字段名都能进
    bank = norm_bank(bank)
    originals = norm_bank(originals)
    attempts = norm_attempts(attempts)

    prof, prior = kp_profile(attempts)
    mrate = mech_rate(attempts)

    # 知识点锁定：支持中文名 / int id / 列表
    lock_ids = None
    if lock_kp is not None:
        items = lock_kp if isinstance(lock_kp, (list, tuple, set)) else [lock_kp]
        lock_ids = {q_kpid({"kp": x}) for x in items}
        lock_ids.discard(0)

    # 「做对且隔 ≥ mastered_min_days 天」的题：重复抑制第二档（看板 P1-9）
    mastered_qids = {a["qid"] for a in attempts
                     if a["correct"] and a["days_ago"] >= W["repeat_penalty_mastered_min_days"]}

    def weight_of(q):
        kp = q["kp"]
        p = prof.get(kp)
        if p is None:                       # 无历史 -> 走先验，即"默认概率出题"
            err = prior
            days = 0
            alpha = DIFFICULTY["alpha_init"]
        else:
            err, days, alpha = p["err"], p["days_since"], p["alpha"]

        mr = mrate.get(q["mech"], 0.0)
        w = 1.0
        w *= (1 + W["w_err"] * err)
        w *= (1 + W["w_mech"] * mr)
        w *= (W["recency_floor"] + min(1.0, days / W["recency_cap_days"]))
        w *= tier_weights(alpha)[q["tier"]]

        # 重复抑制：近 3 轮出现过 ×0.2
        if q["qid"] in recent_questions:
            w *= W["repeat_penalty_recent"]
        # 重复抑制：曾经做对且已隔 ≥7 天 ×0.5（已掌握，不必反复练）
        if q["qid"] in mastered_qids:
            w *= W["repeat_penalty_mastered"]
        return w, err, mr, days, alpha

    # ---- 原题配额 ----
    n_org = min(len(originals), int(round(N * W["original_quota"])))
    picks = []

    # 去重按独立去重键 dedup_key（sha1，非题干全文比对），知识点配额防止扎堆
    cap_kp = max(1, int(math.ceil(N * W["max_kp_share"])))
    if lock_ids:                    # 锁定后整轮都给这一个知识点，配额不再生效
        cap_kp = max(cap_kp, N)

    def take(pool, k, chosen_keys, kp_count, use_weight=True, only_tier=None,
             must_anchor=False, ignore_lock=False, ignore_cap=False):
        """加权无放回抽样。dedup_key 去重 + 知识点配额 + 锁定过滤"""
        cand = []
        for q in pool:
            key = q["dedup_key"]
            if key in chosen_keys:
                continue
            if only_tier is not None and q["tier"] != only_tier:
                continue
            if must_anchor and not q["is_anchor"]:
                continue
            if lock_ids and not ignore_lock and q["kp"] not in lock_ids:
                continue
            if not ignore_cap and kp_count.get(q["kp"], 0) >= cap_kp:
                continue
            cand.append(q)
        if not cand or k <= 0:
            return []
        if len(cand) <= k:
            for q in cand:
                chosen_keys.add(q["dedup_key"])
                kp_count[q["kp"]] = kp_count.get(q["kp"], 0) + 1
            return cand
        ws = [weight_of(q)[0] for q in cand] if use_weight else [1.0] * len(cand)
        out = []
        for _ in range(k):
            tot = sum(ws)
            if tot <= 0:
                break
            r, acc = rng.random() * tot, 0.0
            idx = len(cand) - 1
            for i, x in enumerate(ws):
                acc += x
                if r <= acc:
                    idx = i
                    break
            q = cand[idx]
            out.append(q)
            chosen_keys.add(q["dedup_key"])
            kp_count[q["kp"]] = kp_count.get(q["kp"], 0) + 1
            # 清零：同一去重键的其他难度版本 + 已达配额的知识点
            for j, c in enumerate(cand):
                if c["dedup_key"] == q["dedup_key"] or \
                   (c["kp"] == q["kp"] and kp_count[c["kp"]] >= cap_kp):
                    ws[j] = 0.0
        return out

    chosen_keys, kp_count = set(), {}

    # ---- ① anchor 参照题：每轮必夹，作为跨轮可比基准（评估 §2 R2 / 看板 P1-6）----
    # 锁定时只在锁定知识点内取 anchor，锁定优先于"必夹"
    anchor_pool = [q for q in bank if q["is_anchor"]]
    if lock_ids:
        anchor_pool = [q for q in anchor_pool if q["kp"] in lock_ids]
    n_anchor = min(ANCHOR["per_round"], len(anchor_pool), N)
    for q in take(anchor_pool, n_anchor, chosen_keys, kp_count, ignore_cap=True):
        picks.append((q, "题库"))

    # ---- ② 原题配额 ----
    org_picks = take(originals, n_org, chosen_keys, kp_count)
    for q in org_picks:
        picks.append((q, "原题"))

    # ---- ③ 每轮至少 1 道 tier2 固定难度题（2026-10-31 裁定 / 看板 P1-8、R31）----
    # anchor 已含 tier2；anchor 不足时从题库 tier2 兜底，保证每轮都有 α 更新参照
    n_t2 = sum(1 for q, _ in picks if q["tier"] == ANCHOR["fallback_tier"])
    for q in take(bank, ANCHOR["min_tier2_per_round"] - n_t2, chosen_keys, kp_count,
                  only_tier=ANCHOR["fallback_tier"], must_anchor=True):
        picks.append((q, "题库"))
    n_t2 = sum(1 for q, _ in picks if q["tier"] == ANCHOR["fallback_tier"])
    for q in take(bank, ANCHOR["min_tier2_per_round"] - n_t2, chosen_keys, kp_count,
                  only_tier=ANCHOR["fallback_tier"]):
        picks.append((q, "题库"))

    # ---- ④ 题库补足到 N ----
    n_bank = N - len(picks)
    bank_picks = take(bank, n_bank, chosen_keys, kp_count)
    if len(bank_picks) < n_bank:
        # 题库不足或知识点配额卡死：放宽配额补满（内部折中，UI 不外露）。
        # dedup_key 是硬约束不放宽：宁可不足 N 也不重复入选同一道题（R03/R07）
        used_qids = {p[0]["qid"] for p in picks} | {q["qid"] for q in bank_picks}
        extra = [q for q in bank
                 if q["qid"] not in used_qids
                 and q["dedup_key"] not in chosen_keys
                 and (not lock_ids or q["kp"] in lock_ids)]
        for q in extra[:n_bank - len(bank_picks)]:
            bank_picks.append(q)
            used_qids.add(q["qid"])
            chosen_keys.add(q["dedup_key"])
    for q in bank_picks:
        picks.append((q, "题库"))

    # ---- 组装结果 ----
    results = []
    for q, src in picks:
        w = weight_of(q)[0]
        role = QUESTION_ROLE["original"] if src == "原题" else QUESTION_ROLE["bank"]
        results.append({"q": q, "src": src, "weight": w,
                        "target_tier": q["tier"],
                        "question_role": role,
                        "is_anchor": bool(q["is_anchor"])})
    return results, prof, prior


# ============================================================
# ④ 展示指标
# ============================================================
def mastery_tier(attempts, kp=None):
    """滑动窗口内能稳定做对（>=threshold）的最高难度档"""
    recs = [a for a in attempts if kp is None or a["kp"] == kp]
    recs = sorted(recs, key=lambda a: -a["days_ago"])[::-1][-DISPLAY["mastery_window"]:]
    best = 0
    for t in (1, 2, 3):
        sub = [a for a in recs if a["tier"] == t]
        if len(sub) >= DISPLAY["mastery_min_n"] and \
           sum(1 for a in sub if a["correct"]) / len(sub) >= DISPLAY["mastery_threshold"]:
            best = t
    return best


def tier_breakdown(attempts, window=30):
    recs = sorted(attempts, key=lambda a: a["days_ago"])[:window]
    out = {}
    for t in (1, 2, 3):
        sub = [a for a in recs if a["tier"] == t]
        out[t] = (len(sub), (sum(1 for a in sub if a["correct"]) / len(sub)) if sub else None)
    return out


# ============================================================
# Demo：模拟 5 张带日期样卷（30 次作答），跑一次完整推荐
# ============================================================
def build_mock(bank=None, seed=11):
    """
    模拟学生历史：5 张试卷分布在 40 天内。
    题目从题库里抽（qid/tier/kp 均为真实值），这样 session_id、
    question_role、repeat_penalty_mastered 都是可验证的真实链路，而非孤立的假数据。
    """
    rng = random.Random(seed)
    bank = norm_bank(bank if bank is not None else build_bank())
    ems = ["漏解", "计算失误", "漏条件", "方法选择错误", "审题遗漏"]
    attempts = []
    for sheet_i, days_ago in enumerate([38, 30, 22, 12, 4]):
        session_id = sheet_i + 1                     # 一张卷 = 一轮
        pool = rng.sample(bank, min(6, len(bank)))
        for q in pool:
            # 越近期的卷子表现越好（模拟真实进步）
            p_ok = 0.35 + (38 - days_ago) * 0.010 - (q["tier"] - 1) * 0.12
            correct = rng.random() < p_ok
            eid = EM_ID[rng.choice(ems)]        # 两字段必须同值（旧实现各抽一次，会不一致）
            attempts.append({
                "qid": q["qid"], "kp": q["kp"],
                "mech": eid, "error_mechanism_id": eid,
                "tier": q["tier"], "tier_difficulty": q["tier_difficulty"],
                "correct": correct, "days_ago": days_ago,
                "session_id": session_id,
                "question_role": QUESTION_ROLE["bank"],
                "is_anchor": q["is_anchor"],
            })
    return attempts


def build_bank():
    """题库：高频知识点 × 错误机制 × 3 难度档（此处为结构占位，真实由抓取入库）"""
    import hashlib
    import re

    def _dedup_key(text, answer):
        """与 bank/ingest.py 同一算法：数字归一化 + 去标点 + sha1 前 16 位"""
        t = re.sub(r"[-+]?\d+(?:\.\d+)?", "#", text)
        t = re.sub(r"[^\w\u4e00-\u9fff#]", "", t)
        return hashlib.sha1((t + "|" + str(answer)).encode("utf-8")).hexdigest()[:16]

    TD = DIFFICULTY["tier_difficulty"]
    bank = []
    qid = 1
    samples = {
        "一元二次方程": ["解方程 x² − 7x + 12 = 0", "解方程 x² + x − 6 = 0", "解方程 2x² − 8 = 0"],
        "因式分解": ["分解因式 3a² − 27", "分解因式 x³ − 4x", "分解因式 2x² − 12x + 18"],
        "分式": ["化简 (x² − 9)/(x − 3)", "解方程 (x+1)/(x−2) = 3", "化简 (a² − b²)/(a + b)"],
        "勾股定理": ["直角边 6、8，求斜边", "直角边 9、40，求斜边", "斜边 13、直角边 5，求另一直角边"],
        "二次函数": ["求 y = x² − 4x + 3 的顶点", "求 y = 2x² + 4x − 1 的对称轴", "求 y = x² − 6x + 5 的最小值"],
        "相似三角形": ["相似比 2:3，面积比是多少", "△ABC∽△DEF，AB=4,DE=6,AC=5，求 DF", "相似三角形周长比 3:5，面积比是多少"],
    }
    for kp_name_, texts in samples.items():
        for i, text in enumerate(texts):
            for tier in (1, 2, 3):
                bank.append({"qid": qid, "kp": KP_ID[kp_name_],
                             "mech": EM_ID[["漏解", "计算失误", "漏条件"][i % 3]],
                             "tier": tier, "tier_difficulty": TD[tier],
                             "difficulty_tier": tier,
                             "text": text, "answer": "待 sympy 校验",
                             "dedup_key": _dedup_key(text, "待 sympy 校验"),
                             "is_anchor": False})
                qid += 1
    # anchor 参照题：每个知识点挑 1 道 tier2 作固定探针（评估 §2 R2：5–8 道）
    for kp_name_, texts in samples.items():
        for q in bank:
            if q["kp"] == KP_ID[kp_name_] and q["tier"] == ANCHOR["fallback_tier"] \
               and q["text"] == texts[0]:
                q["is_anchor"] = True
                break
    return bank


if __name__ == "__main__":
    TD = DIFFICULTY["tier_difficulty"]
    bank = norm_bank(build_bank())
    attempts = build_mock(bank)
    # 原题池：每道题只保留一个难度版本（按独立去重键 dedup_key 去重，真实场景一道题只属于一个档）
    seen, originals = set(), []
    for q in bank:
        if q["dedup_key"] not in seen:
            seen.add(q["dedup_key"])
            originals.append(dict(q, qid=9000 + len(originals)))
        if len(originals) >= 8:
            break
    N = DISPLAY["default_count"]
    recent = {a["qid"] for a in attempts if a["days_ago"] <= 4}
    session_index = next_session_id(attempts)

    print("=" * 76)
    print(f"输入：{len(attempts)} 次历史作答（5 张样卷，跨 38 天）· 题库 {len(bank)} 题")
    print(f"学生选题量：N = {N}　当前轮次 session_index = {session_index}")
    print(f"anchor 参照题 {sum(1 for q in bank if q['is_anchor'])} 道 · "
          f"独立去重键 {len({q['dedup_key'] for q in bank})} 个")
    print("=" * 76)

    prof, prior = kp_profile(attempts)
    print(f"\n全局先验错误率 = {prior:.3f}")
    print(f"\n{'知识点':<14}{'近期错误率':<12}{'样本':<8}{'距上次':<10}{'能力α':<10}{'台阶'}")
    for kid, p in sorted(prof.items(), key=lambda x: -x[1]["err"]):
        mt = mastery_tier(attempts, kid)
        print(f"{kp_name(kid):<14}{p['err']:<12.3f}{p['n']:<8}{p['days_since']:<10}"
              f"{p['alpha']:<10.3f}{'tier'+str(mt) if mt else '—'}")

    print(f"\n整体 mastery_tier = tier{mastery_tier(attempts) or 0}")

    print("\n" + "=" * 76)
    print(f"推荐结果（{N} 题）")
    print("=" * 76)
    res, _, _ = recommend(attempts, bank, originals, N, recent_questions=recent,
                          seed=42, session_index=session_index)
    print(f"\n{'#':<4}{'来源':<7}{'角色':<11}{'档':<7}{'知识点':<14}{'题干':<26}{'权重':<8}")
    for i, r in enumerate(res, 1):
        q = r["q"]
        mark = " ⚓" if r["is_anchor"] else ""
        print(f"{i:<4}{r['src']:<7}{r['question_role']:<11}{'tier'+str(q['tier']):<7}"
              f"{kp_name(q['kp']):<14}{q['text'][:24]+mark:<26}{r['weight']:<8.4f}")

    dist = {}
    for r in res:
        dist[r["q"]["tier"]] = dist.get(r["q"]["tier"], 0) + 1
    print(f"\n难度分布 : { {('tier'+str(k)): v for k, v in sorted(dist.items())} }")
    print(f"原题占比 : {sum(1 for r in res if r['src']=='原题')}/{len(res)}")
    print(f"tier2   : {sum(1 for r in res if r['q']['tier'] == 2)} 题"
          f"（每轮保底 {ANCHOR['min_tier2_per_round']} 道，2026-10-31 裁定）")
    print(f"anchor   : {sum(1 for r in res if r['is_anchor'])} 道（每轮必夹"
          f" {ANCHOR['per_round']} 道，α 只由它更新）")

    # ---- 知识点锁定：改题量后必须保持锁定 ----
    print("\n" + "=" * 76)
    print(f"知识点锁定 LockKp 校验（锁定「{kp_name(KP_ID['一元二次方程'])}」）")
    print("=" * 76)
    for n in (5, 10, 20):
        r2, _, _ = recommend(attempts, bank, originals, n, recent_questions=recent,
                             seed=7, lock_kp="一元二次方程", session_index=session_index)
        kps = sorted({kp_name(x["q"]["kp"]) for x in r2})
        ok = "✓" if kps == ["一元二次方程"] else "✗"
        print(f"  N={n:<3} 抽出 {len(r2):<3} 题，知识点集合 {kps}  {ok}")

    # ---- 重复抑制第二档：做对且隔 ≥7 天 ×0.5 ----
    print("\n" + "=" * 76)
    print("重复抑制第二档（做对且隔 ≥7 天 ×0.5）")
    print("=" * 76)
    mq = sorted({a["qid"] for a in attempts
                 if a["correct"] and a["days_ago"] >= WEIGHT["repeat_penalty_mastered_min_days"]})
    print(f"  命中「已掌握」的题 {len(mq)} 道：{mq[:10]}{' …' if len(mq) > 10 else ''}")
    if mq:
        tgt = mq[0]
        w_on, _, _, _, _ = None, None, None, None, None
        base, _, _ = recommend(attempts, bank, originals, N, recent_questions=set(),
                               seed=42, session_index=session_index)
        # 同一题在有/无 mastered 标记下的权重对比
        q = next(x for x in bank if x["qid"] == tgt)
        prof2, prior2 = kp_profile(attempts)
        p = prof2.get(q["kp"])
        alpha = p["alpha"] if p else DIFFICULTY["alpha_init"]
        w_raw = (1 + WEIGHT["w_err"] * (p["err"] if p else prior2)) \
            * (WEIGHT["recency_floor"] + min(1.0, (p["days_since"] if p else 0)
                                             / WEIGHT["recency_cap_days"])) \
            * tier_weights(alpha)[q["tier"]]
        print(f"  例：qid={tgt} 原始权重 {w_raw:.4f} → 命中后 "
              f"{w_raw * WEIGHT['repeat_penalty_mastered']:.4f}"
              f"（×{WEIGHT['repeat_penalty_mastered']}）")

    print("\n" + "=" * 76)
    print("分难度档正确率（近 30 次）")
    print("=" * 76)
    for t, (n, acc) in tier_breakdown(attempts).items():
        a = f"{acc:.2f}" if acc is not None else "—"
        print(f"  tier{t}  样本 {n:<4} 正确率 {a}")

    print("\n注：题库文本为结构占位，真实流程由 AI 生成后经 sympy 校验入库（见 variant_validator.py）。")
