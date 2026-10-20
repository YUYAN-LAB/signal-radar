# 📡 Signal Radar

> 在它火起来之前,先发现它。

一个**自动更新的开源情报站**,专门追踪 GitHub 上 **AI 硬件、具身智能 / VLA、开源机器人、世界模型、VLM、强化学习、消费级机器人、Vibe Coding、实时语音栈** 的项目,并用「早鸟信号分」帮你找出**还很小、但正在加速**的那一批。

不是又一个「GitHub 热门榜单」——热门榜告诉你的都是已经火了的。这个站点的目标是回答另一个问题:

> **下一个 Open Duck Mini / Microduck / 小智,现在长什么样?**

---

## 它会做什么

| 能力 | 说明 |
|---|---|
| **每日自动采集** | GitHub Actions 每天抓一次全部仓库的 star / fork / 更新时间,存成快照 |
| **早鸟信号分** | 用「周增速 ÷ 体量」为主轴打分,越小越快分越高,而不是越大越靠前 |
| **防刷星折扣** | fork/star 比异常、长期不提交、没有 release 的项目会被扣工程分 |
| **10 个细分领域** | 点卡片即可筛选,每个领域独立排序 |
| **数据诚实** | 采集不满 7 天时**不排名**,直接标注「待观测」,而不是编一个数字出来 |
| **零后端** | 纯静态页面 + JSON,托管在 GitHub Pages,免费、可 fork |

---

## 部署到 GitHub Pages(5 分钟)

### 1. 建仓库并推送

```bash
git init
git add .
git commit -m "init: signal radar"
git branch -M main
git remote add origin https://github.com/<你的用户名>/signal-radar.git
git push -u origin main
```

### 2. 开启 Pages

仓库 → **Settings** → **Pages** → **Source** 选 **GitHub Actions**。

推送后 `Deploy to GitHub Pages` 会自动跑,大约 1 分钟后站点上线:

```
https://<你的用户名>.github.io/signal-radar/
```

### 3. 让定时任务跑起来

`Update signals` 工作流每天在**北京 08:20 / 11:20 / 15:20** 三个窗口触发(后两个是兜底,数据够新会自动跳过),用的是 GitHub 自动提供的 `GITHUB_TOKEN`,**无需任何配置**。

想立刻跑一次:仓库 → **Actions** → **Update signals** → **Run workflow**(默认 `force=true`,会跳过时间判断)。

> ⚠️ 第一次跑完之后,页面顶部会显示「数据积累中」——这是正常的。**连续采集 7 天后**,「早鸟信号榜」才会真正生效。采集越久,信号越准。

**运维说明见 [OPS.md](OPS.md)** —— 包含定时机制、健康判断、故障修复。

---

## 加项目 / 加领域

所有人工判断都集中在 [`scripts/taxonomy.py`](scripts/taxonomy.py),改这一个文件就行:

```python
# 1) 加一个仓库 → 指定它属于哪些领域
REPO_DOMAINS = {
    "apirrone/Open_Duck_Mini": ["robotics", "rl"],
    "某个新项目/名字": ["embodied"],
}

# 2) 给它写一句「为什么值得看」(可选,但很推荐)
WHY = {
    "某个新项目/名字": "一句话说清它的独特性。",
}
```

然后本地跑一次:

```bash
python scripts/pipeline.py
```

没有 token 时 GitHub 只给 60 次/小时,大约够 60 个仓库。批量补数据建议设 token:

```bash
export GITHUB_TOKEN=ghp_xxx
python scripts/pipeline.py --discover   # 顺带跑关键词发现,结果写到 data/discovered.json
```

### 加一个领域

在 `taxonomy.DOMAINS` 里加一条(需要 `id` / `name` / `emoji` / `color` / `desc`),然后把仓库的领域标签指向它即可,前端会自动生成卡片。

---

## 评分逻辑

```
E-SCORE = 45 × 速度 + 20 × 新鲜度 + 20 × 冷门度 + 15 × 工程信号
```

| 维度 | 权重 | 含义 |
|---|---:|---|
| **速度** | 45 | 30% 绝对周增 + 70% 相对周增(周增 ÷ 体量)。小项目靠相对值就能赢 |
| **新鲜度** | 20 | 仓库越年轻越高。刻意避开「一天爆红」的噪声 |
| **冷门度** | 20 | ≤1k star 满分,≥100k 归零。这是「早鸟」的核心 |
| **工程信号** | 15 | 近 7 天有提交(+7)、有 release(+4)、fork/star 比健康(+4) |

完整实现见 [`scripts/scoring.py`](scripts/scoring.py)。

### 三档数据可信度

| 标记 | 含义 |
|---|---|
| `实测` | 快照跨度 ≥ 7 天,增速是真实测出来的 |
| `短期实测` | 有 ≥2 天对比,但不足 7 天 |
| `待观测` | 只有 1 天数据 —— **不排名、不出分**,只展示基本情况 |

这不是保守,是刻意的。一个乱给分数的雷达,比没有雷达更危险。

---

## 项目结构

```
├── index.html                  # 单文件前端(无构建、无依赖)
├── data/
│   ├── repos.json              # 前端唯一读取的数据文件(自动生成)
│   ├── baseline.json           # 仓库元数据缓存(描述、语言、license)
│   ├── renames.json            # 仓库改名映射(自动维护)
│   └── snapshots/YYYY-MM-DD.json  # 每日 star/fork 快照 ← 历史就是资产
├── scripts/
│   ├── taxonomy.py             # ⭐ 领域分类 + 仓库清单 + 人工点评(你要改的)
│   ├── scoring.py              # 评分算法
│   ├── pipeline.py             # 抓取 → 打分 → 输出
│   └── gh.py                   # GitHub GraphQL 批量取数
└── .github/workflows/
    ├── update.yml              # 每日采集
    └── deploy.yml              # 部署 Pages
```

**关于配额**:`gh.py` 用 GraphQL 一次请求拿 80 个仓库,400 个仓库也只消耗 5 次配额。REST 只在 GraphQL 不可用时兜底。

---

## GitHub 之外的信号

这个仓库只自动化了 GitHub。真实世界里,信号往往先出现在:

- **Discord** — Open Duck / LeRobot / Reachy 的开发者先在频道里吵需求,再写代码
- **Hugging Face Hub** — 机器人策略/数据集的下载量,比 star 更早反映真实使用
- **小红书 / B站** — 国内 AI 硬件的热度策源地,「小智」就是先在 B站 DIY 圈炸开的
- **arXiv cs.RO** — VLA 和世界模型的论文基本都先出 arXiv 再出代码
- **Hacker News /newest** — 比首页早 24~48 小时
- **闲鱼 / 淘宝** — DIY 套件的转卖量,是消费级硬件最诚实的验证

手动发现的好项目,直接加进 `taxonomy.py` 提交 PR 就行。

---

## 已知限制

- **只看 star 有盲区**:star 既是信号也是噪音,所以才有工程信号折扣,但没法根治。
- **中文社区信号没有自动化**:B站/小红书没有开放 API,目前靠人工。
- **改名会丢数据**:GitHub 仓库改名/转移后旧名 404。管线已有 `renames.json` 自动记录,但历史快照仍按旧名存。
- **star 趋势图需要积累**:刚开始只有 1 个点,画不出线。

---

## License

MIT。数据来自 GitHub 公开 API,归各项目所有。
