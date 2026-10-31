# -*- coding: utf-8 -*-
"""
学习计划 Agent：根据错题记录生成个性化学习计划

输入：作答历史 attempts、时间周期（天数）、每日学习时长
输出：结构化学习计划（知识点优先级、每日任务、里程碑）

核心逻辑：
1. 分析薄弱知识点（错误率 + 覆盖率）
2. 计算知识点依赖关系和学习优先级
3. 分配每日学习任务（复习 + 新题）
4. 设定阶段性目标和检查点
"""
import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from config import KP_ID, EM_ID, kp_name, em_name, HIGH_FREQ_KP


# 知识点依赖关系（简化版：基础 -> 进阶）
KP_DEPENDENCIES = {
    # 代数基础
    2: [],   # 有理数运算
    3: [2],  # 代数式
    4: [3],  # 方程与不等式
    5: [4],  # 函数基础

    # 几何基础
    20: [],  # 角度
    21: [20], # 三角形
    22: [21], # 四边形

    # 进阶
    10: [5, 21],  # 二次函数（需要函数和三角形基础）
}


def analyze_weak_points(attempts: List[Dict[str, Any]],
                        threshold_error_rate: float = 0.4,
                        threshold_min_attempts: int = 3) -> List[Dict[str, Any]]:
    """
    分析薄弱知识点

    返回格式：[{kp, kp_name, error_rate, attempts, last_days, priority}]
    priority = error_rate * urgency_factor * coverage_factor
    """
    kp_stats = defaultdict(lambda: {"correct": 0, "total": 0, "last_days": 999})

    for a in attempts:
        kp = a.get("kp", 0)
        if not kp:
            continue
        correct = a.get("correct", False)
        days_ago = a.get("days_ago", 0)

        kp_stats[kp]["total"] += 1
        if correct:
            kp_stats[kp]["correct"] += 1
        kp_stats[kp]["last_days"] = min(kp_stats[kp]["last_days"], days_ago)

    weak_points = []
    for kp, stats in kp_stats.items():
        if stats["total"] < threshold_min_attempts:
            continue

        error_rate = 1 - (stats["correct"] / stats["total"])
        if error_rate < threshold_error_rate:
            continue

        # 计算优先级：错误率 * 遗忘系数 * 高频加权
        urgency = 1.0 + (stats["last_days"] / 30.0)  # 越久没练越紧急
        freq_boost = 1.5 if kp in HIGH_FREQ_KP else 1.0
        priority = error_rate * urgency * freq_boost

        weak_points.append({
            "kp": kp,
            "kp_name": kp_name(kp),
            "error_rate": round(error_rate, 3),
            "attempts": stats["total"],
            "last_days": stats["last_days"],
            "priority": round(priority, 3),
        })

    # 按优先级降序
    weak_points.sort(key=lambda x: -x["priority"])
    return weak_points


def sort_by_dependency(weak_points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    根据知识点依赖关系调整学习顺序
    确保前置知识点排在前面
    """
    kp_set = {wp["kp"] for wp in weak_points}
    ordered = []
    visited = set()

    def visit(kp: int):
        if kp in visited or kp not in kp_set:
            return
        # 先访问依赖
        for dep in KP_DEPENDENCIES.get(kp, []):
            visit(dep)
        visited.add(kp)
        # 找到对应的weak_point并添加
        for wp in weak_points:
            if wp["kp"] == kp and wp not in ordered:
                ordered.append(wp)
                break

    # 按优先级遍历
    for wp in weak_points:
        visit(wp["kp"])

    # 添加未在依赖图中的点
    for wp in weak_points:
        if wp not in ordered:
            ordered.append(wp)

    return ordered


def generate_daily_plan(weak_points: List[Dict[str, Any]],
                        days: int = 14,
                        daily_questions: int = 10,
                        review_ratio: float = 0.3) -> List[Dict[str, Any]]:
    """
    生成每日学习计划

    参数：
    - days: 计划天数
    - daily_questions: 每天练习题数
    - review_ratio: 复习题占比（0-1）

    返回：[{day, date, tasks: [{kp, kp_name, questions, type}]}]
    """
    if not weak_points:
        return []

    daily_review = int(daily_questions * review_ratio)
    daily_new = daily_questions - daily_review

    plan = []
    current_kp_idx = 0
    today = datetime.now().date()

    for day_num in range(1, days + 1):
        day_date = today + timedelta(days=day_num - 1)
        tasks = []

        # 新题：聚焦当前知识点
        if current_kp_idx < len(weak_points):
            wp = weak_points[current_kp_idx]
            tasks.append({
                "kp": wp["kp"],
                "kp_name": wp["kp_name"],
                "questions": daily_new,
                "type": "新题突破",
                "target": f"错误率降至 {max(0.2, wp['error_rate'] - 0.2):.0%}"
            })

            # 每3天切换知识点
            if day_num % 3 == 0:
                current_kp_idx += 1

        # 复习题：从已学过的知识点中抽取
        if current_kp_idx > 0 and daily_review > 0:
            review_kps = weak_points[:current_kp_idx]
            review_per_kp = max(1, daily_review // len(review_kps))
            for wp in review_kps[:3]:  # 最多复习3个知识点
                tasks.append({
                    "kp": wp["kp"],
                    "kp_name": wp["kp_name"],
                    "questions": review_per_kp,
                    "type": "复习巩固",
                    "target": "保持熟练度"
                })

        plan.append({
            "day": day_num,
            "date": day_date.strftime("%Y-%m-%d"),
            "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][day_date.weekday()],
            "tasks": tasks,
            "total_questions": sum(t["questions"] for t in tasks)
        })

    return plan


def generate_milestones(weak_points: List[Dict[str, Any]],
                       days: int = 14) -> List[Dict[str, Any]]:
    """
    生成阶段性目标（里程碑）
    """
    milestones = []

    if not weak_points:
        return milestones

    # 按优先级分组
    high_priority = [wp for wp in weak_points if wp["priority"] > 1.0]
    medium_priority = [wp for wp in weak_points if 0.5 <= wp["priority"] <= 1.0]

    # 第一周：攻克高优先级
    if high_priority:
        milestones.append({
            "day": 7,
            "title": "第一阶段：攻克薄弱点",
            "goals": [
                f"掌握【{wp['kp_name']}】（当前错误率 {wp['error_rate']:.0%}）"
                for wp in high_priority[:3]
            ],
            "target_error_rate": 0.3,
        })

    # 第二周：巩固+拓展
    if days >= 14:
        milestones.append({
            "day": 14,
            "title": "第二阶段：全面巩固",
            "goals": [
                f"复习所有薄弱知识点",
                f"总体错误率降至 30% 以下",
            ],
            "target_error_rate": 0.25,
        })

    return milestones


def create_learning_plan(attempts: List[Dict[str, Any]],
                        days: int = 14,
                        daily_questions: int = 10,
                        daily_minutes: int = 30) -> Dict[str, Any]:
    """
    生成完整学习计划

    返回：{
        "summary": {...},
        "weak_points": [...],
        "daily_plan": [...],
        "milestones": [...],
    }
    """
    # 1. 分析薄弱点
    weak_points = analyze_weak_points(attempts)

    if not weak_points:
        return {
            "summary": {
                "status": "excellent",
                "message": "当前没有发现明显薄弱知识点，继续保持！",
                "total_attempts": len(attempts),
            },
            "weak_points": [],
            "daily_plan": [],
            "milestones": [],
        }

    # 2. 调整学习顺序（依赖关系）
    weak_points = sort_by_dependency(weak_points)

    # 3. 生成每日计划
    daily_plan = generate_daily_plan(weak_points, days, daily_questions)

    # 4. 生成里程碑
    milestones = generate_milestones(weak_points, days)

    # 5. 统计汇总
    total_questions = sum(dp["total_questions"] for dp in daily_plan)
    avg_error_rate = sum(wp["error_rate"] for wp in weak_points) / len(weak_points)

    return {
        "summary": {
            "status": "needs_improvement",
            "total_attempts": len(attempts),
            "weak_points_count": len(weak_points),
            "avg_error_rate": round(avg_error_rate, 3),
            "plan_days": days,
            "total_questions": total_questions,
            "daily_minutes": daily_minutes,
            "estimated_improvement": f"预计错误率可降低 {int((avg_error_rate - 0.25) * 100)}%",
        },
        "weak_points": weak_points,
        "daily_plan": daily_plan,
        "milestones": milestones,
    }


# ============ 测试代码 ============
if __name__ == "__main__":
    # 模拟错题数据
    test_attempts = [
        {"qid": 1, "kp": 4, "correct": False, "days_ago": 1},
        {"qid": 2, "kp": 4, "correct": False, "days_ago": 2},
        {"qid": 3, "kp": 4, "correct": True, "days_ago": 3},
        {"qid": 4, "kp": 5, "correct": False, "days_ago": 1},
        {"qid": 5, "kp": 5, "correct": False, "days_ago": 2},
        {"qid": 6, "kp": 21, "correct": False, "days_ago": 5},
        {"qid": 7, "kp": 21, "correct": False, "days_ago": 6},
        {"qid": 8, "kp": 21, "correct": False, "days_ago": 7},
    ]

    plan = create_learning_plan(test_attempts, days=7, daily_questions=8)

    import json
    print(json.dumps(plan, ensure_ascii=False, indent=2))
