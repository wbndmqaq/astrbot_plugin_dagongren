<p align="center">  
  <img src="logo.png" width="120" alt="logo">
</p>

<h1 align="center">🏢 打工人 · 上班族物语</h1>

AstrBot 大型群聊职场生存模拟插件。以「上班族的现实」为主题：入职公司、每日打卡领薪
（通勤+五险一金+迟到判定）、摸鱼被抓、加班住院攒调休券、写周报评绩效、和领导谈加薪、
被裁员拿补偿、挤地铁通勤、交房租、吃外卖、买基金绿到发光、买股票追涨杀跌，
团建购物两不误，攒够公积金全款买房安家；玩家之间方案评审式对线撕逼，
亲自出战卷王大赛冲击「传奇卷王」段位。

- 💾 用户数据：**SQLite**（标准库，WAL 模式 + 线程安全写锁 + 原子资金操作）
- 📝 游戏文本：**JSON**（`resources/texts/`，33 个文案库共 2560+ 条，可自行扩充；流水/事件的类型标签也全部外置为 `kind_*` 键）
- 🖼️ 全部输出：**独立 Playwright 渲染器**（Chromium 截图，失败自动回退纯文本）
- 🌐 独立端口 **WebUI** 面板（**aiohttp** 实现，支持密码登录与全量管理，桌面/手机自适应）
- 🔌 适配 **OneBot v11** 与 **QQ 官方机器人**（仅用文本/图片/@ 组件）
- ⚡ 全链路异步：所有 SQLite / 文件 / 密码哈希调用一律 `asyncio.to_thread`，事件循环不被阻塞
- 🧪 自带 pytest：`python -m pytest tests/`（513 项）覆盖纯函数、存储层事务、资源 / 配置一致性与历史缺陷回归。
  pytest 只在开发时需要，**不在** `requirements.txt` 里，请自行 `pip install pytest`

### 📦 环境要求

| 项 | 要求 |
|---|---|
| AstrBot | `>= 4.9.2`（见 `metadata.yaml` 的 `astrbot_version`） |
| Python | 3.11+（`ruff.toml` 的 `target-version`；语法本身最低 3.10，用到 `X \| None`） |
| Python 依赖 | `playwright`、`aiohttp`、`jinja2`、`argon2-cffi`、`pyjwt`（全部在 `requirements.txt`，随插件自动安装）。`pytest` 只在跑自检时需要，不随插件安装 |
| 额外手动步骤 | 仅 Chromium 浏览器内核需手动下载一次（见下节；不装则所有指令回退纯文本） |
| 数据库升级 | 插件启动时自动对比 `players` 表列集并补 `ALTER TABLE ADD COLUMN`，老版本数据目录可直接覆盖升级 |

---

## 🚀 安装方式：WebUI 插件市场

AstrBot WebUI → 插件管理 → 搜索 `astrbot_plugin_shangbanzu` → 安装。

---

### 卡片渲染环境安装教程（缺了也能玩，只是回退纯文本）

卡片渲染基于本地 Playwright 截图实现。`playwright` **Python 包**写在 `requirements.txt` 里，AstrBot 安装插件时会随依赖一并装上；但**浏览器内核（Chromium）体积大，必须手动下载一次**——插件绝不会自动执行系统级安装：不修改 apt 源、不运行 apt-get、不自动下载浏览器内核。

#### ① 确认 playwright Python 包已装

正常随插件依赖自动安装。若日志提示 `No module named 'playwright'`，手动补装：

```bash
pip install playwright
# 国内网络可用清华镜像：
pip install playwright -i https://pypi.tuna.tsinghua.edu.cn/simple
```

#### ② 下载 Chromium 浏览器内核

```bash
python -m playwright install chromium
# 国内网络可用 npmmirror 加速：
PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/ python -m playwright install chromium
```

Windows PowerShell 写法：

```powershell
$env:PLAYWRIGHT_DOWNLOAD_HOST="https://npmmirror.com/mirrors/playwright/"
python -m playwright install chromium
```

#### ③（仅 Linux / Docker 容器）安装系统运行库

仅当启动渲染时报 `libnspr4` / `libnss3` / `error while loading shared libraries` 才需要，需 root：

```bash
python -m playwright install-deps chromium
```

或手动安装系统库：

```bash
apt-get update && apt-get install -y \
  libnspr4 libnss3 libgbm1 libasound2 \
  libatk-bridge2.0-0 libatk1.0-0 libcairo2 libcups2 libdrm2 \
  libx11-xcb1 libxcb1 libxcomposite1 libxdamage1 libxfixes3 \
  libxkbcommon0 libxrandr2 libxext6 libpango-1.0-0
```

容器内 apt 官方源下载慢？可选换阿里镜像源后再装：

```bash
# Debian 12 (bookworm)
sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources
# Ubuntu 22.04
sed -i 's|archive.ubuntu.com|mirrors.aliyun.com|g' /etc/apt/sources.list
apt-get update
```

#### ④ 重载插件

WebUI → 插件管理 → 本插件 → 重载。

环境未就绪时，日志会输出**一次**安装指引（`core/renderer.py` 的 `_hint_once`，不会每次渲染都刷屏），
所有指令自动回退纯文本展示；安装完成后重载即可正常出图。

---

## 🧩 插件架构（模块化）

```text
astrbot_plugin_shangbanzu/
├── main.py                  # 主入口：仅生命周期 + 输出渲染，指令经声明式路由安装
├── metadata.yaml            # 插件元数据（版本规范 PEP 440）
├── _conf_schema.json        # 250 项配置 Schema（数值项全部带 min/max，WebUI 在线调参即时生效）
├── handlers/                # 【指令路由层】Route 声明 + (ctx, event) -> R 业务函数，共 95 条指令（其中 5 条管理员）
│   ├── __init__.py          #   ALL_ROUTES 汇总（按域拼接各 *_cmds.ROUTES）
│   ├── base.py              #   Route + install()（重写 __module__ 归属主模块）+ 每用户 TTL 锁
│   ├── system_cmds.py       #   帮助 / 早报 / 今日事件 / 四大排行 / 上周榜快照
│   ├── career_cmds.py       #   职业：找工作→打卡→摸鱼→加班→晋升→跳槽→辞职 / 创业
│   ├── company_cmds.py      #   加薪谈判 / 进修 / 同事录
│   ├── battle_cmds.py       #   对线 / 卷王大赛（亲自出战）
│   ├── life_cmds.py         #   吃饭/健身/租房/通勤/团建/购物/买房/工资条/道具
│   ├── life2_cmds.py        #   办公室日常：开会/带饭/抢会议室/峰会/宠物/考证
│   ├── finance_cmds.py      #   银行/利息/转账/基金买卖
│   ├── stock_cmds.py        #   股市行情/持仓/买入/卖出
│   ├── extra_cmds.py        #   年终奖/技能/社交/成就称号/红包/刮刮乐
│   ├── lottery_cmds.py      #   游戏化双色球彩票（累积奖池，每日自动开奖）
│   ├── extra2_cmds.py       #   年会抽奖/借钱/建议/工位升级/加班餐/体检
│   ├── market_cmds.py       #   跳槽市场（在招公司一览）
│   ├── review_cmds.py       #   年终考评
│   ├── push_cmds.py         #   每日群推送开关与状态
│   └── backup_cmds.py       #   SQLite 在线数据备份系列（管理员）
├── core/                    # 【业务服务层】（大模块已按域拆分子模块/facade）
│   ├── db/                  #   SQLite 存储（Mixin 拆分：_const/_core/_players/_ranking/_events/_company/_lottery/_money/_redpacket/_session）
│   ├── career.py            #   职业核心服务 facade（career_common/job/work/growth/business）
│   ├── life.py              #   生活消费服务 facade（life_daily/housing/items/career）
│   ├── extra.py             #   扩展玩法 facade（extra_bonus/social/achievement/redpacket/scratch）
│   ├── context.py           #   GameCtx（群昵称拉取 + TTL 缓存，close() 释放缓存）
│   ├── result.py            #   R() 统一返回结构
│   ├── renderer.py          #   Playwright 渲染器（浏览器复用、并发信号量、在飞计数收尾）
│   ├── logic.py             #   业务通用纯函数（cooldown / 金额解析 / 安全钳制 / 文案占位填充 / ISO 周 / 涨薪累进）
│   ├── web_auth.py          #   Argon2id 口令哈希 + JWT 密钥/临时密码生成
│   ├── gamedata.py          #   JSON 静态数据加载（副本保护 + 逐文件容错，坏掉的那份沿用旧值）
│   ├── backup.py            #   在线快照备份管理器（分片 backup，不阻塞玩家读写）
│   ├── stocks.py            #   100 支股票波动与交易（原子买卖 + 单事务一键清仓 + 持仓只数上限）
│   ├── life2.py             #   生活扩展 facade（life_office/pet/cert/travel；文件名的 2 是开发批次，不是域）
│   ├── social.py            #   对线与卷王大赛服务
│   ├── finance.py           #   金融理财服务
│   ├── lottery.py           #   双色球彩票 + 开奖播报
│   ├── review.py            #   年终考评（档位表读 data/review.json）
│   ├── extra_achievement.py #   成就解锁（判定口径与阈值读 texts/achievements.json）
│   └── extra2.py            #   扩展Ⅱ facade（extra_party/loan/advice/work；文件名的 2 是开发批次，不是域）
├── webui/                #   独立端口管理面板（默认 127.0.0.1:17817）
│   ├── server/              #   aiohttp 服务（Mixin 拆分：_core/_auth/_serve/_api/_admin/_profile，另加 _const/_deps/_util 三个支撑模块；共 9 个模块、34 个路由）
│   ├── index.html           #   响应式管理面板页面
│   ├── style.css            #   深浅色主题样式（含移动端适配层）
│   └── app.js               #   前端交互与数据流控制
├── resources/
│   ├── texts/*.json         #   游戏文案库（33 个库共 2560+ 条，含 atmosphere 今日氛围 / achievements 成就）
│   ├── data/*.json          #   公司 116 家 / 职级 12 阶 / 房产 12 档 / 股票 100 支 / 宠物 2 种
│   │                       #   + 饭价 3 档 / 通勤 4 种 / 证书 4 种 / 技能 5 种 / 刮刮乐 4 档
│   │                       #   + 商店 29 件 / 对手 25 名 / 工位 5 档 / 考评 5 档
│   │                       #   + 年终奖 3 档 / 排行榜 4 类 / 双色球玩法与奖级
│   └── templates/*.html     #   Jinja2 渲染模板（8 套）
├── ruff.toml                # Lint 规则（签入以保证 `ruff check .` 结果可复现）
└── tests/                   # pytest（513 项）：纯函数、存储层事务、并发守恒、模板渲染、资源与配置一致性、历史缺陷回归
```

> 数据与代码的边界，按「这个数值需不需要运维在线调」分两类：
>
> - **只在 JSON 里**：条目本身的固有属性（饭价、通勤费、证书报名费、房租押金、
>   公司基薪、股票初始价）。改这些要编辑 `resources/data/*.json`。
> - **JSON 存「配置键名 + 默认值」，实际数值在插件配置里**：跨档位的平衡参数
>   （考评阈值与奖金倍数 `review.json`、年终奖概率与倍数 `yearbonus.json`、
>   双色球各奖级占比 `lottery.json`）。这样运维调数值不用改 JSON，改文案不用改代码。
>
> 文案、条目名、图标配色一律在 JSON；代码只保留规则与判定。指令正则也不枚举 JSON 里的
> 内容名——吃法、通勤方式、宠物种类、排行榜别名都由 JSON 拼装或交服务层校验，
> 所以新增一种吃法/宠物/榜单只改 JSON 即可，不必碰代码（`tests/test_resources.py` 会验证这一点）。

---

## 📮 用户群

QQ 群（插件讨论）：[点击加入](https://qm.qq.com/q/8sOZdZTnaw)

---

## 🎮 完整指令一览

> 指令统一以 `#` 前缀触发（例如 `#打卡`），无前缀的裸指令不会响应。下表为完整指令速查（共 95 条，含 5 条管理员指令）。
>
> ⚠️ **这 95 条全部注册为 `@filter.regex` 正则，而不是 AstrBot 的 `@filter.command`。**
> 这是「# 前缀免唤醒 + 同一功能多个别名（`#签到`/`#打卡`）」的必然代价：正则路由不参与
> AstrBot 的指令注册表，因此**不会出现在指令管理面板里，也不能单独停用 / 改名 / 配置触发权限**，
> 只有插件的总开关（启用/停用本插件）对它们生效。也正因为如此，
> **没有指令重名保护**——与其它插件撞正则时 AstrBot 不会提示，需要自己避让；
> 想改触发词请直接改 `handlers/*.py` 里的正则，或用 `resources/data/*.json`
> 里已外置的正则片段（如 `rankings.json` 的 `aliases`、`pets.json` 的 `action`）。
> 同样因为不注册指令，`#帮助` 里的菜单是插件自己渲染的，不随 AstrBot 面板变化。
>
> 绝大多数指令是群聊玩法，私聊会提示「只能在群聊中使用」；`#帮助`、`#职场早报`、`#今日事件`
> 与 4 条备份指令不依赖群 ID，私聊同样可用（`Route.group_only=False`）。
>
> 表中方括号里的可选值（通勤方式、技能、证书、房型…）都来自 `resources/data/*.json`，
> 不带参数发送对应指令即会列出当前可选项——改了 JSON 就以 JSON 为准。
> 例外：`#吃饭` 不带参数时会**随机挑一档便宜的**直接吃掉，想看菜单请发一个不存在的吃法。

| 分类 | 指令示例 | 说明 |
|---|---|---|
| ℹ️ **系统** | `#上班族帮助` / `#职场早报` / `#今日事件` | 帮助菜单 / 每日早报 / 全群突发公共事件 |
| 💼 **职业** | `#找工作 [公司名]` / `#我的公司` | 投简历入职指定或推荐公司（群友自建公司同样可投） / 查看雇主详情 |
| 💼 **职业** | `#上班` / `#打卡` | 每日打卡领薪（自动扣通勤、五险一金、房租，触发随机事件） |
| 💼 **职业** | `#摸鱼` / `#加班` | 摸鱼回蓝（小心被抓罚款）/ 加班赚钱涨经验（概率拿调休券）。加班有冷却**和**每日次数上限（`overtime_daily_limit`，默认 4 次） |
| 💼 **职业** | `#请假` / `#请调休` / `#写周报` | 请假回血 / 消耗调休券带薪休假 / 评绩效拿奖金 |
| 💼 **职业** | `#加薪` / `#晋升` / `#跳槽` / `#辞职` | 谈涨薪 / 职级提升 / 换公司 / 裸辞。谈薪与年终考评拿到的涨薪会累积进个人系数，晋升/跳槽重算薪资时不会被抹掉 |
| 💼 **职业** | `#我的简历` / `#简历` / `#我的状态` | 查看个人职场档案（公司、职级、薪资、状态条、通勤、公积金） |
| 💼 **成长** | `#我的技能` / `#技能列表` | 查看已掌握的技能（技能数直接加成加班收益与晋升成功率） |
| 💼 **成长** | `#学技能 [技能名]` / `#进修` / `#摆摊` / `#副业升级` | 学硬技能（提升加班收益与晋升成功率，可选技能见 `skills.json`）/ 自费进修班提升身价 / 下班摆摊赚外快 / 升级副业等级 |
| 👑 **创业** | `#创建公司 [名称]` / `#公司分红` | 身价/职级达标后自建企业当老板，提取企业利润分红 |
| 🏠 **生活** | `#吃饭 [吃法]` / `#午休` / `#健身` | 恢复健康与精神值（吃法见 `meals.json`，默认外卖/食堂/大餐） |
| 🏠 **生活** | `#租房 [房型]` / `#买房` / `#通勤 [交通方式]` | 搬家（`houses.json` 的 `rent` 是**日租**，每次打卡扣一次）/ 全款购房安家 / 设定通勤方式（见 `commute.json`，默认地铁/公交/骑车/打车） |
| 🏠 **生活** | `#商店` / `#购买 [道具]` / `#我的背包` / `#使用 [道具]` | 便利店购买功能道具卡并使用（护盾/拉屎卡/咖啡/雷达）。咖啡续命包只**缩短**加班冷却（`coffee_pack_cd_cut_hours`），不清零 |
| 🏠 **生活** | `#工资条` / `#购物` / `#团建` | 查看收支流水明细 / 网购剁手 / 公司团建 |
| 🐱 **宠物** | `#养猫` / `#养狗` / `#撸猫` / `#遛狗` | 领养宠物陪伴，每日互动恢复精神 |
| ⚔️ **对抗** | `#对线 @群友` / `#卷王大赛`（别名 `#排位赛`）/ `#参加卷王大赛`（别名 `#参加排位赛`）| 与群友方案撕逼 / 查看段位（ELO 积分）/ 亲自出战冲击传奇卷王 |
| 🤝 **社交** | `#发红包 [金额] [个数]` / `#抢红包` | 群内塞拼手气红包，全群开抢 |
| 🤝 **社交** | `#职场社交 @群友` / `#带饭 @群友` / `#和同事吃饭` | 喝奶茶建人脉 / 帮同事带饭 / 同事拼饭 |
| 🤝 **社交** | `#我的成就` / `#佩戴称号 [名称]` / `#卸下称号` | 查看职场里程碑并佩戴专属头衔展示 |
| 🤝 **社交** | `#刮刮乐` / `#下班刮刮乐` | 购买职场刮刮乐（小赌怡情，返奖率由 `scratch_rtp` 控制）。注意 `#彩票` 指的是双色球奖池，不是刮刮乐 |
| 🏢 **办公室** | `#开会` / `#回消息` / `#抢会议室` / `#帮领导做事` | 职场日常操作与应酬 |
| 🏢 **办公室** | `#行业峰会` / `#考证 [证书名]` / `#旅游` | 参与高端峰会 / 考取**证书**（与「#学技能」不同：技能按概率学习，见 `skills.json`；证书按报名费+通过率考试，见 `certs.json`）/ 度假散心 |
| 🎁 **福利** | `#年会抽奖` / `#工位升级` / `#加班餐` / `#年度体检` | 年底抽现金大奖 / 工位升星 / 免费夜宵 / 体检回血 |
| 🎁 **福利** | `#请年假` / `#年终奖` / `#年终考评` | 消耗年假休息 / 每年年终奖 / 年度绩效评定（S/A/B/C/D） |
| 🪙 **理财** | `#存款 [金额]` / `#取款 [金额]` / `#领取利息` | 银行存款，每小时单利计息；单次最多计 `bank_max_interest_hours` 小时（默认 24，是**计息小时数**上限而非金额上限）。存/取之前会先按旧余额自动结息，所以不能靠「临领息前才存满」白拿满额利息 |
| 🎰 **彩票** | `#买彩票 3` / `#买彩票 3 7 12 5` / `#我的彩票` / `#彩票` / `#奖池` / `#开奖结果` | 游戏化双色球（号码池与奖级见 `resources/data/lottery.json`，默认红 1~16 选 3 + 蓝 1~8 选 1）：机选或自选，购票款全部进池，每晚自动开奖，无人中奖滚存下期。机器人在开奖时间离线时，下次上线会**补开**欠下的期次 |
| 🪙 **理财** | `#银行信息` / `#我的银行` / `#账户信息` | 查看银行账户、信用等级、存款上限与基金持仓 |
| 🪙 **理财** | `#升级信用` / `#一键存款` / `#转账 @群友 [金额]` | 提升存款上限 / 全部存入 / 个人间原子安全转账（@ 与金额顺序不限） |
| 📊 **基金** | `#买基金 [金额]` / `#卖出基金 [比例%]` | 申购基金，净值每日波动，按比例赎回 |
| 📈 **股票** | `#股市` / `#我的股票` / `#持仓` | 查看 100 支股票行情 / 查看持仓盈亏 |
| 📈 **股票** | `#买股票 [代码/名称] [金额]` / `#卖出 [代码/名称] [比例%]` / `#清仓` | 股票交易与止盈止损（比例可省略 %）/ 全部持仓在单个事务内一次性清算 |
| 🏆 **排行** | `#富豪榜` / `#卷王榜` / `#身价榜` / `#职级榜`（榜单与别名定义在 `resources/data/rankings.json`） | 群内四大维度排行榜单：**富豪榜=总资产、卷王榜=经验值 exp、身价榜=value、职级榜=level**。注意与「#卷王大赛 ELO 积分」是两套独立数值——前者按经验，后者按段位分 |
| 🏆 **排行** | `#上周榜` / `#周榜` | 上一周归档的财富榜快照（由 `weekly_archive_enabled` 每周自动归档，`archive_top_n` 控制条数） |
| 🤝 **社交** | `#人才市场` / `#同事录` / `#同事` | 查看全群同事的在职状态与月薪 |
| 🤝 **社交** | `#借钱 @群友 [金额]` | 借钱给群友，有概率有去无回（`lend_fail_rate`） |
| 📰 **资讯** | `#职场建议` / `#职场八卦` / `#跳槽市场` | 毒鸡汤 / 群内吃瓜 / 在招公司一览（含群友自建公司） |
| 🛠️ **管理** | `#推送`（管理员） / `#推送状态` | 切换本群每日早报推送开关。开关是**全群共享**状态，因此限管理员；`#推送状态` 任何人可查 |
| 🛠️ **管理** | `#创建备份` / `#备份列表` / `#恢复备份 <序号\|完整名称>` / `#删除备份 <序号\|完整名称>` | 管理员在线创建、查看、回滚、删除 SQLite 快照。恢复/删除必须带参数且名称需完整（防误操作），恢复前会校验快照完整性与字段兼容性 |

---

## ⚙️ 核心配置项说明（`_conf_schema.json`，共 250 项，数值项全部带 min/max）

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `use_image` | bool | `true` | 是否启用 Playwright HTML 图片卡片渲染（关闭则为纯文本） |
| `render_scale` | float | `2.0` | 截图渲染清晰度缩放倍率（推荐 2.0 超清） |
| `webui_enabled` | bool | `true` | 是否开启独立 WebUI 管理服务 |
| `webui_host` | string | `127.0.0.1` | WebUI 监听地址（默认仅本机；需局域网访问改 `0.0.0.0`）。留空密码时插件会自动生成临时密码，所以不存在「无鉴权对外监听」的状态 |
| `webui_port` | int | `17817` | WebUI 访问端口 |
| `webui_password` | string | `""` | WebUI 访问密码。管理面板可删档/恢复备份/改配置，强烈建议设置。留空时 WebUI 首次启动会自动生成 18 位临时密码（明文一次性打印到启动日志），登录后必须立即改密。 |
| `webui_jwt_secret`| string | `""` | JWT(HS256) 签名密钥，首次启动自动生成并持久化（32 字节 / 64 个十六进制字符）；勿手动修改。面板保存时长度不足 32 字节会被拒绝并保持原值 |
| `webui_trusted_proxies`| list | `[]` | **非回环**反代的地址白名单。对端是回环地址时已默认视为同机反代（同机 nginx/caddy 是最主流部署，去掉它会让反代后所有用户共用一个限流桶），本项用于把非回环的反代出口 IP 也列为可信；非可信来源的 `X-Forwarded-For/Proto` 一律忽略。完整口径见下方「登录信任模型」 |
| `start_cash` | int | `800` | 新玩家初始备用金（元） |
| `backup_max_keep` | int | `20` | 备份快照保留数量上限（超出自动删除最旧的） |
| `social_insurance_rate`| float | `0.10` | 打卡扣除的五险一金比例 |
| `stock_fee_rate` | float | `0.005` | 股票买卖交易手续费率 |
| `fund_fee_rate` | float | `0.005` | 基金申购/赎回手续费率 |
| `house_price` | int | `100000` | 自购房产全款价格（哪一档算「自购」由 `houses.json` 的 `owned` 标记决定） |
| `push_hour` | int | `8` | 每日自动推送早报与股市行情的时间（0~23 点） |
| `create_company_cost` | float | `30000.0` | 创业注册验资资金门槛（元） |
| `create_company_min_value` | float | `50000.0` | 创业所需最低职场身价门槛（元） |
| `scratch_lottery_cost` | float | `20.0` | 职场刮刮乐单张彩票花费（元） |
| `redpacket_min_amount` | float | `10.0` | 群内塞红包允许的最低起始总金额（元） |
| `weekly_archive_enabled`| bool | `true` | 每周自动归档排行榜快照（`#上周榜` 读的就是这份归档） |
| `archive_top_n` | int | `10` | 每周归档快照保留的 Top 人数 |
| `cooldown_exempt_users` | list | `[]` | 豁免冷却时间的特权用户 ID 列表 |

> 余下 226 项覆盖：冷却时长（求职/跳槽/调休/团建/帮领导/峰会…）、每日次数上限（加班…）、成功率（摸鱼被抓/学技能/对线/卷王/借钱有去无回/考证/年会/体检…）、金额（学费/健身/周报罚款/社交请奶茶/年终奖系数…）、上下限（年假天数/副业等级/医院阈值/对线身价惩罚/股票涨跌停/周榜保留周数…）。所有数值在 WebUI「插件配置」页可即时在线修改。
>
> 条目的固有属性（饭价、通勤费、证书报名费、房租押金）只在 `resources/data/*.json` 里，改它们就是改文件并热加载；跨档位的平衡参数（考评阈值、年终奖倍数、彩票奖级占比）在 JSON 里只写「配置键名 + 默认值」，实际数值走插件配置。详见上文「数据与代码的边界」。

---

## 🌐 现代化 WebUI 管理面板

在浏览器打开 `http://127.0.0.1:17817`（默认仅本机可访问；手机访问请将 `webui_host` 改为 `0.0.0.0` 并设置密码，面板自动适配移动端布局）：

- 📡 **实时动态流**：群内打卡、加薪、跳槽、对线实时事件播报，可一键清空；
- 🏆 **全群排行榜**：按群随时切换查看富豪、卷王、身价、职级榜；
- 🔍 **玩家全景档案**：可视化查询玩家属性、状态条与核心数值；
- ⚙️ **玩家管理**：档案数值在线编辑与玩家删除（高危操作带确认弹窗）；
- 📈 **股市管理中心**：100 支股票行情展示、在线快速调价、全局波动触发与重置；
- 🗂️ **公司与文案编辑器**：116 家公司参数全字段表格编辑、**33 个文本库**在线配置热重载（分类按钮由后端扫 `resources/texts/*.json` 下发，新增一个 json 就会自动出现在面板里）；
- 🧩 **在线参数配置**：250 项游戏规则与数值在线修改（数值带 min/max 钳制，列表项可热更新 JSON），即存即生效。配置项在面板里显示为中文标签，并按玩法域**自动分组**（46 组，可整体展开/折叠），顶部有**关键词搜索**（键名 / 说明 / 提示均可命中，命中项自动展开所在分组）。性能与容量类内部参数（缓存容量、TTL、并发上限、签名密钥等 18 项）集中在「内部参数」组，**默认折叠**，且在 AstrBot 原生插件配置页中已标记 `invisible` 隐藏——避免一次误改就把截图并发压到 1 或轮换掉 JWT 密钥。
- 🔒 **收紧的 CSP**：面板没有任何内联脚本与内联事件处理器（交互统一走 `data-act` + 事件委托，参数经 `data-*` 传递），因此 `script-src` 为 `'self'` 而非 `'unsafe-inline'`，XSS 兜底不再形同虚设。
- 🛡️ **Argon2id 密码存储 + JWT 服务端会话**：密码用 Argon2id（m=64MiB, t=3, p=4，比 OWASP 建议的 19MiB/t=2 更保守；实测单次哈希约 70ms，弱 CPU 或与截图渲染抢核时 150~300ms）哈希存盘，配置表单里是掩码输入框、只用于提交新口令，存盘恒为哈希；登录令牌走 JWT(HS256)，服务端维护 webui_sessions 会话表（jti 绑定，12h TTL，面板每小时清理一次过期行），可在「我的会话」面板单独撤销任意设备；改密立即下线全部会话。
- 🔐 **纵深防护**：Host 白名单防 DNS rebinding（绑定 `0.0.0.0` 时只放行「内网/回环 IP 且端口匹配」，域名 Host 一律拒绝）+ 写请求校验 Origin + cookie `HttpOnly/SameSite=Lax` + CSP；登录失败 per-IP 限流（5 次锁 300 秒）+ **与来源无关的全站失败闸门**（300 秒窗口内全站失败满 60 次即一律 429 并记一条 warning，见下方「登录信任模型」），Argon2 校验限并发以防未鉴权路径把内存放大到 GB 级；清空访问密码（= 关闭鉴权）只允许在本机监听下并需显式确认。

#### 🔎 登录信任模型（`X-Forwarded-For` / `X-Forwarded-Proto` 什么时候被采信）

1. **默认只监听 `127.0.0.1`**。不改 `webui_host` 时面板只有本机能连。
2. **对端是回环地址 → 视为同机反向代理**（同机 nginx/caddy 是最主流的部署方式）。此时采信：
   - `X-Forwarded-For` 的**最后一段**（`hops[-1]`，即紧邻本服务的代理追加的那个真实对端），用作限流桶的 key；
   - `X-Forwarded-Proto`（只用于决定 cookie 要不要带 `secure`）。
   取最后一段而不是第一段：第一段完全由客户端自带，取它等于让攻击者自选桶。
3. **`webui_trusted_proxies`** 用于把**非回环**的反代地址也列入可信（例如反代与插件不在同一台机器）。列进来的地址与回环对端同等对待。
4. **非可信来源的这两个头一律忽略**：直连场景下它们由客户端任意伪造，采信等于让攻击者自选限流桶（绕过 per-IP 限流）与伪造 `secure`。
5. **即便如此仍有兜底**：因为第 2 条让同机来源可以靠「每个请求换一个 `X-Forwarded-For`」不断换桶，另有一道**与 IP 无关的全站闸门** —— `LOGIN_WINDOW`(300s) 窗口内全站登录失败总数达到 60 次就一律返回 429 并记一条「疑似爆破」warning，窗口滑过自动恢复。正常运维一个窗口内失败 0~1 次，60 次只有自动化爆破才够得到。
- 🗄️ **数据备份与回滚**：一键在线创建数据库安全快照，随时安全恢复。备份走 SQLite 在线 backup API 分片复制，不阻塞玩家的正常读写；恢复是破坏性覆盖，全程互斥并预先校验快照完整性与字段兼容性。

---

## 🗂️ 自定义扩充文案与数据（所有 JSON 详解）

插件的所有事件文案与数值数据均位于 `resources/` 目录下，分为 **`texts/`（剧情文案库）** 与 **`data/`（数值规则库）**。可直接编辑 JSON 文件，或在 WebUI「公司与文案」页在线编辑（保存即时热更新）：

### 1. 📖 剧情文案库（`resources/texts/`）

> 共 **32** 个文件，分两类：
>
> - **剧情池**（下表逐个列字段）：随机抽取的事件与台词，一个键下面挂一组句子。
> - **界面文案**（表后统一说明）：面板标题 / 标签 / 页脚 / 纯文本回退句，一个键一句。

| 文件名 | 主要字段（真实键名） |
|---|---|
| **`work.json`** | `checkin_events`（打卡随机事件：text/cash/health/mind/exp）、`slack_ok` / `slack_caught`（摸鱼）、`shield_slack`（摸鱼被抓但护盾生效）、`overtime_events`（加班）、`hospital_texts`（住院）、`layoff_texts` / `layoff_safe`（裁员）、`leave_texts`（请假）、`promote_ok` / `promote_fail`（晋升）、`resign_texts`（辞职）、`job_offer` / `job_fail`（应聘）、`hop_ok` / `hop_fail`（跳槽）、`weeklyreport_ok` / `weeklyreport_fail`（周报）、`commute_late`（迟到） |
| **`life.json`** | `takeout` / `canteen` / `feast`（吃饭三档）、`gym`（健身）、`stall_income` / `stall_fail`（摆摊）、`house_move`（搬家）、`rent_paid` / `rent_failed`（房租）、`nap_ok` / `nap_caught`（午休）、`shopping` / `shopping_refund`（退货，占位 `{shipping}`）/ `shopping_deal`（薅券，占位 `{budget}`/`{pay}`/`{saved}`）、`teambuild`（团建：必须带 `type` 字段）、`house_owned_texts`（已购房） |
| **`company.json`** | `jinxiu_ok` / `jinxiu_fail`（进修成败）、`negotiation_ok` / `negotiation_fail`（加薪谈判） |
| **`duel.json`** | `actions`（对线回合招式，支持 `{a}`/`{b}` 昵称占位符）、`win_lines` / `lose_lines`（胜负台词）、`shield_duel`（对线失利但护盾生效，支持 `{target}` 占位） |
| **`news.json`** | `headlines`（每日职场早报头条，按日期种子全天固定） |
| **`extra.json`** | `yearbonus_ok` / `yearbonus_bad`（年终奖）、`skill_learn_ok` / `skill_learn_fail`（学技能）、`social_ok` / `social_fail`（职场社交）、`gossip_texts`（职场八卦，支持 `{a}`/`{b}`）、`side_hustle_up`（副业升级）、`annual_leave`（年假） |
| **`extra2.json`** | `party_prizes`（年会奖品：rank/text/amount）、`career_advice`（职场建议）、`lend_ok` / `lend_fail`（借钱）、`ot_meal`（加班餐）、`checkup_ok` / `checkup_bad`（体检） |
| **`extra3.json`** | `meeting`（开会）、`bring_food`（帮带饭）、`reply_msg`（回消息）、`meeting_room`（抢会议室）、`eat_with`（和同事吃饭）、`boss_task_ok` / `boss_task_fail`（帮领导做事）、`summit`（峰会）、`pet_interact`（宠物互动）、`cert_ok` / `cert_fail`（考证）、`travel`（旅游：text/cost/mind/health/dest，`dest` 为余额不足提示里显示的地名）、`lottery_broadcast`（双色球开奖全群播报模板：`header`/`header_late`/`number_line`/`tier_hit`/`tier_miss`/`footer`/`cta`）、`daily_push`（每日早报模板：`news`/`news_empty`/`up`/`down`/`cta`） |
| **`help.json`** | `sections`（帮助菜单：icon/title/commands[{usage, desc}]） |
| **`atmosphere.json`** | `events`（今日职场氛围公共事件：`title` / `desc` / `accent`；按日期种子全天固定） |
| **`achievements.json`** | `items`（成就：`id`/`name`/`desc` + `metric`（判定口径：attend_streak / net_worth / duel_wins / lvl / has_pet / house_owned）/ `threshold`（阈值）。阈值在 JSON 里，所以 `desc` 里写的数字就是实际生效的数字） |

**界面文案库（21 个）** —— 按业务域一一对应到 `core/` 与 `handlers/` 的同名模块，
代码里通过 `tt("<表名>", "<键>")` / `gd.s("<表名>", "<键>")` 读取，键名前缀是统一约定：

| 前缀 | 含义 | 例 |
|---|---|---|
| `titles.title_*` | 面板标题 | `title_checkin` = 打卡成功卡片的标题 |
| `lbl_*` | 面板 block 的标签 | `lbl_today_income` = 「今日到账」 |
| `val_*` | 面板 block 的值模板 | `val_today_income` = `+{net} 元（绩效 x{perf}）` |
| `foot_*` | 面板底部提示 | `foot_work_hint` |
| `text_*` | 关图后的纯文本回退句 | `text_checkin_head` |
| `err_*` | 报错/拒绝提示 | `err_already_checkin` |
| `ev_*` / `tx_*` | 群事件播报 / 收支流水备注 | `ev_layoff`、`tx_dividend` |
| `line_*` / `lines_*` | 面板正文叙述行 | `line_report_intro` |
| `col_*` / `cell_*` | 表格表头 / 单元格 | `col_company` |

| 文件名 | 覆盖的功能域 |
|---|---|
| **`career_work.json`** | 打卡 / 摸鱼 / 加班 / 请假 / 调休 / 周报 |
| **`career_growth.json`** | 晋升 / 谈薪 / 我的公司 |
| **`career_job.json`** | 找工作 / 跳槽 / 辞职 / 跳槽市场 |
| **`career_business.json`** | 创业 / 公司分红（`custom_company_tag` 是自建公司的行业标签） |
| **`life_daily.json`** | 吃饭 / 健身 / 午休 / 摆摊 / 购物 / 团建 |
| **`life_housing.json`** | 租房 / 买房 / 通勤 |
| **`life_items.json`** | 便利店 / 背包 / 使用道具 |
| **`life_career.json`** | 简历 / 工资条 / 进修 |
| **`life2.json`** | 开会 / 带饭 / 抢会议室 / 峰会 / 宠物 / 考证 / 旅游 |
| **`social.json`** | 对线 / 卷王大赛 / 同事录 |
| **`extra_social.json`** | 职场社交 / 八卦 |
| **`extra_bonus.json`** | 年终奖 / 学技能 / 副业升级 / 年假 |
| **`extra_achievement.json`** | 成就列表 / 佩戴与卸下称号 |
| **`extra_redpacket.json`** | 发红包 / 抢红包 / 刮刮乐（`tx_refund_note` 是超时退回的流水备注） |
| **`review.json`** | 年终考评（档位名/配色在 `data/review.json`，这里只放文案） |
| **`finance.json`** | 存取款 / 利息 / 信用 / 转账 / 基金 |
| **`stocks.json`** | 股市行情 / 买卖 / 持仓 / 清仓 |
| **`lottery.json`** | 双色球购票 / 我的彩票 / 奖池 / 开奖结果（`labels` 子对象放面板标签） |
| **`backup.json`** | 备份创建 / 列表 / 恢复 / 删除（含完整性校验的拒绝原因） |
| **`push.json`** | 推送开关与状态 |
| **`system.json`** | 帮助头部 / 排行榜 / 上周榜 / `gid_hint`（私聊拦截）/ `err_hint`（兜底异常）/ `not_in_game` / `no_record` |

> 改这些键只影响显示，不改数值。键缺失或 JSON 写坏时代码会回退到内置默认串（不会崩），
> 但那意味着你的改动没生效——保存后发一条对应指令确认即可。
> 占位符写错（少写、多写、写成 `{a[0]}`）也不会打断指令，只会原样输出 `{占位}`。

### 2. 📊 核心数值与配置库（`resources/data/`）

> `meals.json` / `commute.json` / `certs.json` 里的费用与效果**只能改 JSON**，没有对应配置键；
> `scratch.json` 的奖项是「相对售价的倍数」，售价与返奖率才由配置（`scratch_lottery_cost` / `scratch_rtp`）控制。
>
> **改完怎么生效**：
> - 在 **WebUI「公司与文案」页**保存 → 强制热重载（`load_all(force=True)`），游戏内立即生效；
> - **直接编辑 `resources/data/*.json` 或 `resources/texts/*.json` 保存 → 不会自动加载**，
>   需要在 AstrBot 的插件管理里重载本插件才生效。原因：`gamedata.load_all()` 只在
>   `force=True` 时读盘，data/texts 走的是纯内存缓存（这两处合计 40+ 个 JSON，
>   给每条指令都做一遍 mtime 校验不划算）；
> - **唯一例外**是 `resources/templates/*.html`：它按 mtime 自动失效（`main._template_source`
>   缓存键带 mtime），改完下次出图即生效。

| 文件名 | 主要字段（真实键名） |
|---|---|
| **`companies.json`** | 116 家企业：`id`、`name`、`tag`（行业）、`salary`（底薪）、`intensity`（强度）、`risk`（日裁员率）、`min_exp`（门槛）、`desc`，部分公司带 `perks`（福利）。WebUI 保存时自动按薪资升序重排 ID |
| **`positions.json`** | 12 级职级：`i`（0~11）、`title`、`mult`（薪资系数）、`need`（晋升经验）、`cost`（打点费） |
| **`shop.json`** | 便利店道具：`id`、`name`、`price`、`health`/`mind`（恢复）、`desc`；`type:"card"` 的道具带 `card_key`（shield/poop/coffee_pack/radar）入背包，并用 `use_text`（字符串数组）定义使用后的说明文案，支持 `{health}`/`{mind}`/`{news}`/`{layoff_scale}`/`{cd_cut}`（咖啡续命包缩短的加班冷却时长）占位 |
| **`houses.json`** | 12 档住房：`i`（0~11）、`name`、`rent`（日租金）、`recover`（每日恢复）、`deposit`（押金）、`desc`；`owned: true` 标记「只能买房获得、不可租」的那一档（代码按标记定位，不再写死下标与名称） |
| **`stocks.json`** | 100 支股票：`code`、`name`、`sector`、`price`（基准价） |
| **`opponents.json`** | 卷王大赛对手池：`name`、`score`（战力）、`effect`（特征描述） |
| **`rankevents.json`** | 卷王挑战事件 `events[25]`（`name`/`effect`/`desc`）+ 段位表 `tiers[5]` / `tier_scores[5]`（段位名称与阈值的单一来源，`logic.tier_of` 读取） |
| **`review.json`** | 年终考评档位 `grades[5]`：`grade`/`name`/`color`/`icon` 为展示数据；阈值与奖金、调薪只存配置键名 + 默认值（`threshold_key`/`bonus_key`/`raise_key` 及对应 `*_default`），实际数值仍由插件配置决定，最后一项是兜底档（`threshold_key` 为 `null`） |
| **`workstations.json`** | 5 档工位 `workstations`：`lv`（0~4）、`name`、`cost`、`bonus`、`desc`；另有 `side_hustle_titles`（5 个副业等级称号，地摊小贩→副业大亨） |
| **`skills.json`** | 可学技能表：`[{name, cost, exp}]`。技能数量直接加成加班收益（`skill_overtime_bonus_rate`）与晋升成功率（`skill_promote_bonus_rate`） |
| **`yearbonus.json`** | 年终奖档位 `tiers[3]`：`title`/`color`/`icon`/`text_key` 为展示数据；概率与倍数只存配置键名 + 默认值（`rate_key`/`multi_key` 及 `*_default`），最后一项是兜底档（`rate_key` 为 `null`）。与 `review.json` 同一范式 |
| **`rankings.json`** | 排行榜定义 `rankings[4]`：`key`（口径）/`name`（标题）/`unit`（单位）/`aliases`（触发别名）。榜单标题、单位、指令正则的单一来源——加一个榜只改这一处 |
| **`lottery.json`** | 双色球玩法：`red_max`/`blue_max`/`red_pick`（号码池与每注红球个数）+ `tiers[3]`（奖级 `name`/`red_hit`/`blue_hit`/`rule` 与占比配置键 `pct_key`）。改号码池会影响历史票判奖，请谨慎 |
| **`pets.json`** | 宠物（猫/狗）：`type`、`cost`、`mind_bonus`、`health_bonus`（狗独有）、`desc`、`icon`（面板图标）、`action`（互动指令名）。`#养<type>` 与 `#<action>` 两条路由的正则由本文件拼装，加一种宠物只改 JSON |
| **`meals.json`** | 吃饭三档（外卖 / 食堂 / 大餐）：`name`、`key`、`cost`、`health`、`mind` |
| **`commute.json`** | 4 种通勤方式：`name`、`cost`、`health`、`mind`、`late_rate`。迟到当天的薪资折扣由配置项 `late_pay_penalty_rate` 控制；第一项是新玩家的默认通勤方式 |
| **`certs.json`** | 4 种证书（PMP / CPA / 法考 / CFA）：`name`、`cost`、`exp`。配合 `cert_pass_rate` / `cert_value_bonus_rate` 调难度与回报 |
| **`scratch.json`** | 下班刮刮乐：`prizes[4]`（`name`/`multiplier`/`prob`/`color`，金额按「相对售价的倍数」存储）+ `lose`（未中奖文案）。配合配置项 `scratch_lottery_cost` / `scratch_rtp` 调售价与返奖率（变更售价不会破坏返奖率） |

---

欢迎提交 PR 补充或丰富插件的创意文案与企业！

---

## 致谢

- [肉辣胖](https://ti.qq.com/open_qq/index2.html?url=mqqapi%3a%2f%2fuserprofile%2ffriend_profile_card%3fsrc_type%3dweb%26version%3d1.0%26source%3d2%26uin%3d3279524507) — 感谢这个群友画的logo
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) — 多平台聊天机器人框架

---

## 📄 许可证

本项目采用 [GNU General Public License v3.0](LICENSE) 开源。

---

<div align="center">

如果觉得这个插件对你带来快乐，欢迎 Star 或者 PR 一下哈哈

</div>
