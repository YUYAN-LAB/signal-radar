"""Signal Radar —— 数据管线

流程：取数（GraphQL 批量 / REST 单条）→ 合并快照 → 打分 → 生成 data/repos.json

用法：
    python scripts/pipeline.py              # 每日增量（CI 定时跑）
    python scripts/pipeline.py --discover   # 额外跑关键词发现，输出新面孔候选
    python scripts/pipeline.py --dry-run    # 只打分不写文件
环境变量：
    GITHUB_TOKEN  必填（GitHub Actions 自带；本地 60 次/小时会非常吃紧）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import math
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gh as ghapi  # noqa: E402
import scoring  # noqa: E402
from taxonomy import ALIASES, BLOCKLIST, DOMAINS, REPO_ALIASES, REPO_DOMAINS, WHY, DOMAIN_MAP  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SNAP_DIR = DATA / "snapshots"
RENAMES_PATH = DATA / "renames.json"
try:
    RENAMES: dict[str, str] = json.loads(RENAMES_PATH.read_text())
except Exception:
    RENAMES = {}
TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()

DISCOVER_QUERIES = [
    "open source quadruped robot", "humanoid robot reinforcement learning",
    "vision language action robot", "world model video generation",
    "ai hardware esp32 voice", "open source robot arm", "lerobot sim2real",
    "edge ai wearable open source", "robot dog sdk", "vla fine-tuning",
    "on-device multimodal assistant", "open source robot vacuum",
]


def rest_repo(full: str) -> dict | None:
    req = urllib.request.Request(f"https://api.github.com/repos/{full}", headers={
        "User-Agent": "signal-radar", "Accept": "application/vnd.github+json",
        **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        return {
            "full_name": d["full_name"], "html_url": d["html_url"],
            "description": (d.get("description") or "").strip(),
            "stars": d["stargazers_count"], "forks": d["forks_count"],
            "open_issues": d.get("open_issues_count", 0), "lang": d.get("language") or "",
            "topics": d.get("topics", [])[:12], "created_at": (d.get("created_at") or "")[:10],
            "pushed_at": (d.get("pushed_at") or "")[:10],
            "license": (d.get("license") or {}).get("spdxId") or "",
            "archived": bool(d.get("archived")), "homepage": d.get("homepage") or "",
            "avatar": ((d.get("owner") or {}).get("avatar_url") or ""),
            "releases": -1, "open_prs": 0,
        }
    except Exception as e:
        print(f"    ! {full}: {e}")
        return None


def _placeholder(full: str, last: dict, prev: dict | None) -> dict:
    """用快照 + 上一版元数据拼一个可用记录。"""
    if prev:
        keep = dict(prev)
        keep["stars"] = last.get("stars", keep.get("stars", 0))
        keep["forks"] = last.get("forks", keep.get("forks", 0))
        keep["_offline"] = True
        return keep
    return {
        "full_name": full, "html_url": f"https://github.com/{full}", "description": "",
        "stars": last.get("stars", 0), "forks": last.get("forks", 0), "open_issues": 0,
        "lang": "", "topics": [], "created_at": last.get("created_at", ""),
        "pushed_at": last.get("date", ""), "license": "", "archived": False,
        "homepage": "", "avatar": "", "releases": 0, "open_prs": 0, "_offline": True,
    }


def load_snapshots() -> dict[str, list[dict]]:
    store: dict[str, list[dict]] = {}
    if not SNAP_DIR.exists():
        return store
    for f in sorted(SNAP_DIR.glob("*.json")):
        try:
            payload = json.loads(f.read_text())
        except Exception:
            continue
        for full, s in payload.items():
            store.setdefault(full, []).append({
                "date": f.stem, "stars": s.get("stars", 0), "forks": s.get("forks", 0),
                "created_at": s.get("created_at", "")})
    return store


def discover() -> list[str]:
    found: set[str] = set()
    for q in DISCOVER_QUERIES:
        url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(
            {"q": q, "sort": "stars", "order": "desc", "per_page": 10})
        req = urllib.request.Request(url, headers={
            "User-Agent": "signal-radar", "Accept": "application/vnd.github+json",
            **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                for it in json.load(r).get("items", []):
                    found.add(it["full_name"])
        except Exception as e:
            print(f"  ! 搜索失败 {q}: {e}")
        time.sleep(2.5)
    return sorted(found)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    today = date.today()
    today_s = today.isoformat()
    # 改名映射：用最新名字发起请求
    targets = [RENAMES.get(k, k) for k in REPO_DOMAINS if k not in BLOCKLIST]
    print(f"[1/4] 仓库 {len(targets)}｜GraphQL={'开' if TOKEN else '关(无 token 走 REST)'}")

    snaps = load_snapshots()
    prev_meta: dict[str, dict] = {}
    if (DATA / "baseline.json").exists():
        try:
            prev_meta = json.loads((DATA / "baseline.json").read_text())
        except Exception:
            prev_meta = {}
    if (DATA / "repos.json").exists():
        try:
            prev_meta.update({r["full_name"]: r for r in json.loads((DATA / "repos.json").read_text())["repos"]})
        except Exception:
            pass
    fetched, ok = ({}, False)
    if TOKEN:
        fetched, ok = ghapi.graphql_batch(targets)
        print(f"  GraphQL 拿到 {len(fetched)}/{len(targets)}")
    missing = [t for t in targets if t not in fetched]
    if missing and not ok and TOKEN:
        print(f"  REST 补 {len(missing)} 个（较慢）")
        for full in missing:
            d = rest_repo(full)
            if d:
                fetched[full] = d
            time.sleep(0.2)

    # 离线回退：取不到网的仓库沿用快照里的最新数值，保证站点不会缺块
    reused = 0
    for full in targets:
        if full in fetched:
            continue
        h = snaps.get(full)
        if h:
            fetched[full] = _placeholder(full, h[-1], prev_meta.get(full))
            reused += 1
    if reused:
        print(f"  ⚠ 离线沿用 {reused} 个仓库的旧快照（配额不足或断网）")

    print(f"[2/4] 历史快照 {len(snaps)} 个仓库 / {len(list(SNAP_DIR.glob('*.json'))) if SNAP_DIR.exists() else 0} 天")

    scored = []
    for full, r in fetched.items():
        created = date.fromisoformat(r["created_at"]) if r.get("created_at") else today
        age_days = max((today - created).days, 1)
        pushed = date.fromisoformat(r["pushed_at"]) if r.get("pushed_at") else created
        gap = (today - pushed).days
        total = r["stars"]

        hist = [h for h in snaps.get(full, []) if h["date"] < today_s]
        hist.append({"date": today_s, "stars": total, "forks": r["forks"],
                     "created_at": r.get("created_at", "")})
        conf = scoring.confidence(hist)
        # 数据健全性检查：一周新增不可能超过总量，异常说明快照有脏数据
        if conf != "estimate":
            first = hist[0]["stars"]
            if total - first > total * 0.9:
                print(f"    ⚠ {full} 增速异常（{first} → {total}），按低置信处理")
                conf = "estimate"
        g7 = scoring.growth_7d(hist, total, today, conf)
        g30 = g7 * 30 / 7 if g7 else 0
        fork_ratio = r["forks"] / max(total, 1)
        has_release = r.get("releases", 0) != 0

        item = dict(r)
        if r["full_name"] != full:
            # GitHub 仓库改名/转移：记下来，保证下次仍能命中
            RENAMES[full] = r["full_name"]
        item.update({
            "domains": [d for d in REPO_DOMAINS.get(full, []) if d in DOMAIN_MAP],
            "age_days": age_days,
            "growth_7d": round(g7),
            "growth_per_day": round(g7 / 7, 1),
            "early_score": scoring.early_bird_score(
                total, g7, g30, age_days, fork_ratio, gap, has_release),
            "momentum": scoring.momentum_score(total, g7, g30, gap, has_release, age_days // 7),
            "heat": scoring.heat_label(g7, total),
            "confidence": conf,
            "fork_ratio": round(fork_ratio, 3),
            "stale_days": gap,
            "why": WHY.get(full, ""),
            "kind": "list" if ("awesome" in full.lower() or full.lower().endswith("-handbook")
                               or "curated" in (r.get("description") or "").lower()) else "code",
            "alias": " ".join(
                [REPO_ALIASES.get(full, "")]
                + [ALIASES.get(d, "") for d in REPO_DOMAINS.get(full, [])]
                + [DOMAIN_MAP[d]["name"] for d in REPO_DOMAINS.get(full, []) if d in DOMAIN_MAP]
            ).strip(),
            "history": [{"d": h["date"], "s": h["stars"]} for h in hist[-120:]],
        })
        scored.append(item)

    # 估算数据（快照跨度不足）不参与排名：score 置空，前端按「收录序」展示
    for s in scored:
        if s["confidence"] == "estimate":
            s["early_score"] = None
            s["momentum"] = None
            s["heat"] = "unrated"
    ranked = [x for x in scored if x["early_score"] is not None]
    # 纯论文/资源清单不是「能上手做的东西」，排名时降一档（打个 0.85 折，但仍展示）
    for x in ranked:
        if x.get("kind") == "list":
            x["early_score"] = round(x["early_score"] * 0.85, 1)
    ranked.sort(key=lambda x: (-x["early_score"], -x["stars"]))
    flat = [x for x in scored if x["early_score"] is None]
    flat.sort(key=lambda x: (-x["stars"], x["full_name"]))
    scored = ranked + flat

    prev = prev_meta
    prev_path = DATA / "repos.json"
    for s in scored:
        p = prev.get(s["full_name"])
        s["delta_day"] = (s["stars"] - p["stars"]) if p else None
        s["is_new"] = p is None

    print("[3/4] 汇总")
    domains_payload = []
    for d in DOMAINS:
        members = [s for s in scored if d["id"] in s["domains"]]
        members.sort(key=lambda x: (x["early_score"] is None, -(x["early_score"] or 0), -x["stars"]))
        domains_payload.append({
            **d, "count": len(members),
            "total_stars": sum(m["stars"] for m in members),
            "top": [m["full_name"] for m in members[:6]],
        })

    igniting = [s["full_name"] for s in scored if s["heat"] == "ignition"][:12]
    meta = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_repos": len(scored),
        "tracked_days": len(list(SNAP_DIR.glob("*.json"))) if SNAP_DIR.exists() else 0,
        "past_week_stars": sum(max(s["growth_7d"], 0) for s in scored),
        "igniting": igniting,
        "ranked": len(ranked),
        "unrated": len(flat),
        "missing": [t for t in targets if t not in fetched],
    }
    out = {"meta": meta, "domains": domains_payload, "repos": scored}

    if args.dry_run:
        print(json.dumps(meta, ensure_ascii=False, indent=1))
        top = [(s["full_name"], s["stars"], s["early_score"]) for s in scored[:10]]
        print("Top early:", top)
        return 0

    print("[4/4] 写文件")
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    snap = {k: {"stars": v["stars"], "forks": v["forks"]} for k, v in fetched.items()}
    (SNAP_DIR / f"{today_s}.json").write_text(json.dumps(snap, ensure_ascii=False))
    prev_path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    if RENAMES:
        RENAMES_PATH.write_text(json.dumps(RENAMES, ensure_ascii=False, indent=1))
        print(f"  ↻ 记录改名 {len(RENAMES)} 条 → data/renames.json")

    if args.discover:
        known = set(REPO_DOMAINS) | BLOCKLIST
        cand = [c for c in discover() if c not in known]
        (DATA / "discovered.json").write_text(json.dumps(cand, ensure_ascii=False, indent=1))
        print(f"  新面孔候选 {len(cand)} 个 → data/discovered.json")

    print(f"✅ {len(scored)} 个仓库｜点火中 {len(igniting)}｜缺 {len(meta['missing'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
