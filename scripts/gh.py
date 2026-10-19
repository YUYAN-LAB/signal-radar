"""GitHub GraphQL 批量取数：1 次请求 = 最多 100 个仓库，配额消耗远低于 REST。

没有 token 时自动降级为 REST 逐条请求（60 次/小时，只够小清单）。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
ENDPOINT = "https://api.github.com/graphql"

FRAGMENT = """
fragment R on Repository {
  nameWithOwner
  url
  description
  stargazerCount
  forkCount
  isArchived
  createdAt
  pushedAt
  homepageUrl
  primaryLanguage { name }
  licenseInfo { spdxId }
  repositoryTopics(first: 10) { nodes { topic { name } } }
  openIssues: issues(states: OPEN) { totalCount }
  releases(last: 1) { totalCount }
  owner { avatarUrl }
  issues(states: OPEN) { totalCount }
  pullRequests(states: OPEN) { totalCount }
  defaultBranchRef { target { ... on Commit { history(first: 1) { totalCount } } } }
}
"""


def graphql_batch(names: list[str], chunk: int = 80) -> tuple[dict[str, dict], bool]:
    """返回 ({full_name: repo}, ok)。ok=False 表示 token 不可用/被限流。"""
    if not TOKEN:
        return {}, False
    out: dict[str, dict] = {}
    for start in range(0, len(names), chunk):
        batch = names[start:start + chunk]
        parts, aliases = [], {}
        for i, full in enumerate(batch):
            if "/" not in full:
                continue
            owner, name = full.split("/", 1)
            alias = f"r{i}"
            aliases[alias] = full
            parts.append(
                f'{alias}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) '
                f'{{ ...R }}')
        query = "query{" + " ".join(parts) + "}" + FRAGMENT
        body = json.dumps({"query": query}).encode()
        for attempt in range(3):
            req = urllib.request.Request(ENDPOINT, data=body, headers={
                "Authorization": f"bearer {TOKEN}",
                "Content-Type": "application/json",
                "User-Agent": "signal-radar",
            })
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    payload = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    print(f"  ! GraphQL 拒绝请求（token 无 repo 读权限或被限流）：{e.code}")
                    return out, False
                print(f"  ! GraphQL HTTP {e.code}，重试")
                time.sleep(5 * (attempt + 1))
            except Exception as e:
                print(f"  ! GraphQL {type(e).__name__}，重试")
                time.sleep(3 * (attempt + 1))
        else:
            continue

        if "errors" in payload and not payload.get("data"):
            print(f"  ! GraphQL errors: {str(payload['errors'])[:200]}")
            return out, False

        data = (payload.get("data") or {})
        for alias, full in aliases.items():
            node = data.get(alias)
            if not node:
                continue
            out[full] = {
                "full_name": node["nameWithOwner"],
                "html_url": node["url"],
                "description": (node.get("description") or "").strip(),
                "stars": node["stargazerCount"],
                "forks": node["forkCount"],
                "open_issues": (node.get("openIssues") or {}).get("totalCount", 0),
                "lang": ((node.get("primaryLanguage") or {}).get("name") or ""),
                "topics": [n["topic"]["name"] for n in (node.get("repositoryTopics") or {}).get("nodes", [])],
                "created_at": (node.get("createdAt") or "")[:10],
                "pushed_at": (node.get("pushedAt") or "")[:10],
                "license": ((node.get("licenseInfo") or {}).get("spdxId") or ""),
                "archived": bool(node.get("isArchived")),
                "homepage": node.get("homepageUrl") or "",
                "avatar": ((node.get("owner") or {}).get("avatarUrl") or ""),
                "releases": (node.get("releases") or {}).get("totalCount", 0),
                "open_prs": (node.get("pullRequests") or {}).get("totalCount", 0),
            }
        print(f"  · GraphQL {min(start+chunk, len(names))}/{len(names)}")
        time.sleep(1.0)
    return out, True
