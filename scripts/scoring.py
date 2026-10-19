"""Signal Radar —— 热度与「早鸟信号」评分

设计原则：
1. **绝对热度**只说明「已经火了」，对你没用（人人都知道）。
2. **上升速度**（近 7/30 天 star 增量、增速加速度）才是「正在火」。
3. **早鸟信号**要奖励「还很小但在猛涨」的项目 —— 这才有机会当第一个吃螃蟹的人。
4. star 可以被刷，所以叠加「工程活跃度」（commit/issue/release）与「社区多样性」（fork/star 比）做折扣。
"""
from __future__ import annotations

import math
from datetime import date


def _safe_log(x: float) -> float:
    return math.log10(max(x, 0) + 1)


G7_CONFIDENCE_MIN_DAYS = 7   # 跨度不足 7 天的快照视为「低置信」，只做基线参考


def growth_7d(snapshots: list[dict], total: int, today: date, conf: str | None = None) -> float:
    """用最早/最新两次快照估算 7 日 star 增量。跨度越长越可信。"""
    pts = [s for s in snapshots if s.get("stars")]
    if len(pts) >= 2:
        a, b = pts[0], pts[-1]
        d = (date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days
        if d > 0:
            return (b["stars"] - a["stars"]) / d * 7
    if (conf or "estimate") == "estimate" and pts:
        return _baseline(pts[0], total)
    return 0.0


def _baseline(pt: dict, total: int) -> float:
    """基线推算：历史平均日增 × 7。只作参考，不参与早鸟排名。"""
    if not pt.get("created_at"):
        return total * 0.01
    age = max(1, (date.fromisoformat(pt["date"]) - date.fromisoformat(pt["created_at"])).days)
    return total / age * 7


def early_bird_score(total: int, g7: float, g30: float, created_days: int, fork_ratio: float,
                     commit_gap_days: int, has_release: bool) -> float:
    """早鸟信号 0-100：小而快 = 最高分。

    构成：
      - 速度分 (45)：相对增速 g7/total 与绝对增速 g7 的几何平均 —— 小项目靠相对值取胜
      - 新鲜度 (20)：仓库越年轻越好（但低于 7 天防止「一天爆红」的噪声）
      - 冷门度 (20)：star 越少分越高，1k 以下给满，100k 以上归零
      - 工程信号 (15)：近 30 天有 commit、有 release、fork/star 比健康
    """
    total = max(total, 1)
    relative = g7 / max(total, 50)          # 周增速 / 体量
    absolute = g7

    vel = 0.6 * _clamp(relative / 0.35) + 0.4 * _clamp(absolute / 800)
    speed = 45 * vel

    fresh = 20 * _clamp((created_days - 5) / 500) if created_days < 505 else 20 * _clamp(
        1 - (created_days - 505) / 730) * 0.6 + 4
    fresh = 20 * _clamp(fresh / 20)

    if total <= 1000:
        obsc = 20
    elif total >= 100_000:
        obsc = 0
    else:
        obsc = 20 * (1 - (math.log10(total) - 3) / 2)

    eng = 0
    if commit_gap_days <= 7:
        eng += 7
    elif commit_gap_days <= 30:
        eng += 4
    elif commit_gap_days <= 90:
        eng += 1
    if has_release:
        eng += 4
    if 0.02 <= fork_ratio <= 0.45:          # 太低=纯围观，太高=疑似刷
        eng += 4
    elif fork_ratio > 0.45:
        eng += 1

    return round(speed + fresh + obsc + eng, 1)


def momentum_score(total: int, g7: float, g30: float, commit_gap_days: int, has_release: bool,
                   weeks_old: int) -> float:
    """动量分 0-100：「这周到底有多热」。

    与早鸟分互补：早鸟分奖励「小」，动量分奖励「大且快」。
      - 体量 35：log10(stars)，10 万 star 封顶
      - 加速度 45：周增速 ÷ 长期均速的倍数。安静的项目（增速≈长期均速）中位得分
      - 活跃度 20：近 30 天有提交 / 有 release
    """
    base = 35 * _clamp(_safe_log(total) / 5)

    daily_avg = total / max(weeks_old * 7, 1)      # 长期平均日增
    daily_now = g7 / 7
    accel = daily_now / daily_avg if daily_avg > 0 else 0
    # accel≈1 → 正常; 3 倍 → 高分; 10 倍 → 满分
    trend = 45 * _clamp((math.log10(max(accel, 0.01)) + 0.3) / 1.3)

    active = 20 * (1.0 if commit_gap_days <= 7 else 0.65 if commit_gap_days <= 30 else 0.25)
    if not has_release:
        active *= 0.9
    return round(_clamp(base + trend + active, 0, 100), 1)


def heat_label(g7: float, total: int) -> str:
    """把 repo 粗分为「冷启动 / 升温 / 起飞 / 成熟」四档。"""
    rel = g7 / max(total, 50)
    if rel > 0.25 and total < 3000:
        return "ignition"      # 点火
    if rel > 0.08 or g7 > 400:
        return "rising"        # 升温
    if total > 20000:
        return "mature"        # 成熟
    return "stable"            # 平稳


def baseline_from_age(total: int, created_days: int) -> float:
    """长期年均增速，用于判断当前增速是否异常。"""
    return total / max(created_days, 1)


def confidence(snapshots: list[dict]) -> str:
    """数据可信度：real=有 ≥7 天真实跨度；early=有对比但跨度短；estimate=纯基线推算。"""
    pts = [s for s in snapshots if s.get("stars")]
    if len(pts) >= 2:
        d = (date.fromisoformat(pts[-1]["date"]) - date.fromisoformat(pts[0]["date"])).days
        return "real" if d >= G7_CONFIDENCE_MIN_DAYS else "early"
    return "estimate"


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))
