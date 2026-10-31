# -*- coding: utf-8 -*-
"""
简化推荐引擎：只根据当前试卷的错题推荐，不考虑历史时间因素

专为 demo 演示设计，聚焦当前输入的错题：
1. 统计错题知识点分布
2. 从题库中找相同知识点的题
3. 优先推荐错误率高的知识点
4. 难度匹配当前错题难度
"""
import random
from collections import defaultdict
from typing import Any, Dict, List, Optional

from config import KP_ID, EM_ID, kp_name, em_name


def analyze_errors(attempts: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """
    分析当前试卷的错题分布

    返回：{kp_id: {total, errors, error_rate, avg_tier}}
    """
    kp_stats = defaultdict(lambda: {"total": 0, "errors": 0, "tiers": []})

    for a in attempts:
        kp = a.get("kp", 0)
        if not kp:
            continue

        kp_stats[kp]["total"] += 1
        if not a.get("correct", True):  # 错题
            kp_stats[kp]["errors"] += 1

        tier = a.get("tier", 2)
        kp_stats[kp]["tiers"].append(tier)

    # 计算错误率和平均难度
    result = {}
    for kp, stats in kp_stats.items():
        if stats["total"] == 0:
            continue

        error_rate = stats["errors"] / stats["total"]
        avg_tier = sum(stats["tiers"]) / len(stats["tiers"]) if stats["tiers"] else 2

        result[kp] = {
            "total": stats["total"],
            "errors": stats["errors"],
            "error_rate": error_rate,
            "avg_tier": round(avg_tier),
        }

    return result


def recommend_simple(attempts: List[Dict[str, Any]],
                    bank: List[Dict[str, Any]],
                    n: int = 5,
                    seed: Optional[int] = None) -> tuple:
    """
    简化推荐：根据当前错题推荐巩固题

    参数：
    - attempts: 当前试卷的作答记录
    - bank: 题库
    - n: 推荐题数
    - seed: 随机种子

    返回：(推荐题列表, 知识点错误率画像, 先验错误率)
    """
    rng = random.Random(seed)

    # 1. 分析错题分布
    error_stats = analyze_errors(attempts)

    if not error_stats:
        # 没有错题，返回空
        return [], {}, 0.0

    # 2. 计算总体错误率
    total_attempts = sum(s["total"] for s in error_stats.values())
    total_errors = sum(s["errors"] for s in error_stats.values())
    overall_error_rate = total_errors / total_attempts if total_attempts > 0 else 0.0

    # 3. 按错误率排序知识点（错误多的优先）
    sorted_kps = sorted(
        error_stats.items(),
        key=lambda x: (-x[1]["error_rate"], -x[1]["errors"])
    )

    # 4. 从题库中筛选候选题
    # 已做过的题不再推荐
    done_qids = {a.get("qid") for a in attempts if a.get("qid")}

    candidates_by_kp = defaultdict(list)
    for q in bank:
        qid = q.get("qid")
        if qid in done_qids:
            continue

        kp = q.get("kp") or q.get("kp_id", 0)
        if isinstance(kp, str):
            kp = KP_ID.get(kp, 0)

        if kp in error_stats:
            candidates_by_kp[kp].append(q)

    # 5. 按知识点错误率分配推荐题
    recommendations = []
    used_qids = set()

    # 为每个有错题的知识点推荐至少1题
    for kp, stats in sorted_kps:
        if len(recommendations) >= n:
            break

        cands = candidates_by_kp.get(kp, [])
        if not cands:
            continue

        # 难度匹配：优先推荐接近平均难度的题
        target_tier = stats["avg_tier"]
        cands_with_score = []
        for q in cands:
            if q.get("qid") in used_qids:
                continue

            tier = q.get("tier") or q.get("difficulty_tier", 2)
            tier_diff = abs(tier - target_tier)
            score = 1.0 / (1.0 + tier_diff)  # 难度越接近分数越高
            cands_with_score.append((q, score))

        if not cands_with_score:
            continue

        # 加权随机选择
        total_score = sum(score for _, score in cands_with_score)
        r = rng.random() * total_score
        acc = 0.0

        for q, score in cands_with_score:
            acc += score
            if r <= acc:
                recommendations.append({
                    "q": q,
                    "src": "题库",
                    "reason": f"巩固薄弱知识点，当前错误率 {stats['error_rate']:.0%}",
                })
                used_qids.add(q.get("qid"))
                break

    # 6. 如果还不够n道，从其他候选中随机补充
    if len(recommendations) < n:
        all_cands = []
        for kp_cands in candidates_by_kp.values():
            for q in kp_cands:
                if q.get("qid") not in used_qids:
                    all_cands.append(q)

        rng.shuffle(all_cands)
        for q in all_cands[:n - len(recommendations)]:
            recommendations.append({
                "q": q,
                "src": "题库",
                "reason": "扩展练习",
            })

    # 7. 生成知识点画像（供前端展示）
    profile = {}
    for kp, stats in error_stats.items():
        profile[kp] = {
            "err": stats["error_rate"],
            "n": stats["total"],
        }

    return recommendations, profile, overall_error_rate


def build_simple_bank():
    """构建简化题库（调用原引擎的题库）"""
    try:
        import engine
        return engine.build_bank()
    except Exception:
        return []


if __name__ == "__main__":
    # 测试
    test_attempts = [
        {"qid": 1, "kp": 4, "tier": 2, "correct": False},
        {"qid": 2, "kp": 4, "tier": 2, "correct": False},
        {"qid": 3, "kp": 5, "tier": 3, "correct": False},
        {"qid": 4, "kp": 21, "tier": 2, "correct": True},
    ]

    bank = build_simple_bank()
    recs, prof, prior = recommend_simple(test_attempts, bank, n=5, seed=42)

    print(f"总体错误率: {prior:.1%}")
    print(f"\n知识点错误率:")
    for kp, stats in sorted(prof.items(), key=lambda x: -x[1]["err"]):
        print(f"  KP {kp}: {stats['err']:.0%} ({stats['n']}题)")

    print(f"\n推荐题目:")
    for i, r in enumerate(recs, 1):
        q = r["q"]
        print(f"  {i}. {q.get('text', '')[:30]}... - {r['reason']}")
