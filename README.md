# 剧查查数据采集（juchacha-scraper）

剧查查没有独立站点，数据挂在 DataEye 的 **ADX 行业版 Web** 上：`https://adxray.dataeye.com`
（`adx.dataeye.com` 会 302 到这里；`juchacha.com` 是抢注的域名待售页，跟它无关）。
原生入口是微信小程序，小程序与 Web 是两套后端：小程序那套免登录、可直连，见第 8 节。

```
juchacha-scraper/
├── jcc_rank.py            # 小程序三榜采集（红果漫剧/3D动态漫/AI真人）——免登录，开箱可用
├── jc.py                  # Web 端采集器：签名 / 探活 / 榜单 / 搜索 / 落库 / 导出
├── get_token.js           # 浏览器 Console 一键导出 Web 登录态
├── config.example.json    # 复制成 config.json 填 userKey / cookie
├── requirements.txt
├── README.md
├── data/                  # 输出：CSV / XLSX / SQLite
│   └── jcc/               # 小程序三榜的每日结果
└── reverse/               # 逆向留档（体积大，可选）
    ├── apidocs.json       # 小程序后端 Swagger 2.0（809 个接口）
    ├── app.js, common.js  # Web 端 SPA bundle（签名算法来源）
    ├── bridge.js          # 小程序拉起页，里面是 appid + 后端域名
    └── bm.js, chunk_*.js  # Next.js 路由清单与红果漫剧榜页
```

## 快速开始（免登录，直接出数据）

```bash
git clone https://github.com/hancock28/juchacha-scraper.git
cd juchacha-scraper
pip3 install -r requirements.txt
python3 jcc_rank.py                # 抓最新日期的三个榜
python3 jcc_rank.py --day 2026-10-25   # 指定日期
```

输出在 `data/jcc/`：Excel 三 sheet、分榜 CSV、合并 CSV、原始 JSON、`jcc_ranks.db`。

---

## 1. 拿到登录态（唯一需要手工的一步）

浏览器登录 https://adxray.dataeye.com 之后，F12 → **Console**：

```js
copy(JSON.stringify({userKey: App.userKey, cookie: document.cookie}))
```

粘贴到 `config.json`：

```json
{"userKey": "…", "cookie": "SESSION=…; tokenglobal=…", "outDir": "data", "rate": 1.5}
```

拿不到 `App.userKey` 时用 Network 面板兜底：随便点开一个 `/playlet/*` 请求 →
**Payload** 里复制 `token` 的值，**Headers → Cookie** 整行复制。

> `token` = `userKey`，会随会话过期（一般几天）。过期后所有接口回到 401，重取一次即可。

## 2. 跑起来

```bash
cd juchacha
cp config.example.json config.json      # 填好 userKey / cookie
python3 jc.py probe                     # 22 个接口逐个探活，看哪些对你的账号开放
python3 jc.py dates                     # 热度榜有哪些可选日期
python3 jc.py hot --date 2026-10-25 --pages 10 --size 50 --raw
python3 jc.py search --kw 总裁 --pages 5
python3 jc.py export --table hot_ranking   # data/hot_ranking.csv (+xlsx 若有 openpyxl)
```

`--rate 1.5` 是请求间隔（秒）。付费账号并发限制不公开，跑大批量建议 1.5~2s，
遇到 429/509 就调大；`--raw` 会把原始 JSON 另存 `hot_ranking_raw` 表，字段变动时不丢数据。

## 3. 协议（从 `app.js` 逆向，已实测签名被服务端接受）

| 项 | 值 |
|---|---|
| Host | `https://adxray.dataeye.com` |
| 方法/格式 | `POST`，`application/json;charset=UTF-8`，业务参数平铺在 body |
| 公共参数 | `thisTimes = int(Date.now()/100)`（10Hz 时间片） |
| 签名 | `sign = MD5( 排序后的 "k=v&k=v" + "&key=g:%w0k7&q1v9^tRnLz!M" ).toUpperCase()` |
| 身份 | body `token = userKey`；同时带浏览器 `Cookie: SESSION=…` |
| 登录 | `POST /user/login`，form-urlencoded：`accountId` / `password`（前端加密）/ `vCode` |

签名细节（对应前端 `computeSignByParams`）：

- key 按字典序升序，`数组 → 逗号串`，`字符串 trim`，`null → 空串`
- **先算 sign 再塞进 body**，即 sign 不覆盖 `sign` 和 `token` 自身
- 实测：`/search/quickSearch` 不带 sign 返回 `{"msg":"Error Null sign","statusCode":509}`，
  用本工具算的 sign 换成 `statusCode:500`（业务参数问题），说明签名算法正确

## 4. 已提取的接口清单

`jc.py` 里全部封装好，`python3 jc.py probe` 会逐个打一遍：

```
榜单/概览  /playlet/getHotRankingDate          /playlet/listHotRanking
           /playlet/listNewRanking             /playlet/listRanking
           /playlet/overview/getTrendList       /playlet/overview/getPlayletTypeTop
           /playlet/overview/getPlayletTagTop   /playlet/overview/getPlayletPublisherTop
           /playlet/overview/getPlayletMobileTypeTop
搜索       /playlet/search/searchPlaylet        /playlet/search/searchPublisher
           /playlet/searchHotTitle              /search/quickSearch
详情/趋势  /playlet/getPlayletInfo               /playlet/listTrendByPlaylet
           /playlet/company/listTrendByMaterial /playlet/getAllLabels
发行/公司  /playlet/listPublisherRanking         /playlet/listPublisherPlayletRanking
           /playlet/listPublisherDistribution   /playlet/listProductDistribution
           /playlet/company/getCompanyInfo      /playlet/company/listMediaDistribution
```

导出 Excel 报表走 GET + query（sign 放 query 即可），例如：

```
/playlet/listTrendByPlaylet?startDate=…&endDate=…&thisTimes=…&sign=…
```

## 5. 分页/字段校准

分页字段不同接口可能是 `pageIndex/pageSize` 或 `page/size`，`--page-key/--size-key` 可改。
第一次接新接口，先 `--raw` 存原始 JSON 再定表结构：

```bash
sqlite3 data/juchacha.db ".tables"
sqlite3 data/juchacha.db "select json_extract(payload,'$.data[0]') from hot_ranking_raw limit 1" | python3 -m json.tool
```

字段确认后改 `jc.py` 里 `_dig_rows` 的取值路径或直接加字段映射，重跑即可。

## 6. 如果要的是小程序那份数据

剧查查小程序与 Web 是同一后端，但如果你要的是小程序里才有的入口（名片库、版权对接），走抓包：

```bash
pip3 install --user mitmproxy
mitmdump -p 8080 -w data/wx.flow          # macOS 上装好 mitmproxy 证书并在 iPhone 里信任
# 手机 Wi-Fi 代理指向电脑 IP:8080，打开剧查查小程序，翻一遍榜单
mitmdump -nr data/wx.flow -w data/wx2.flow --set save_stream_file=   # 回放/筛选
# 过滤业务域名
mitmdump -nr data/wx.flow "~d dataeye.com" -w data/wx_dataeye.flow
```

- 小程序请求体同样是 `payload + thisTimes + sign + token`，`token` 在请求头 `token` 或 body 里
- Android 抓小程序 https 建议用 `charles + JustTrustMe`/`frida` 去 pinning；iOS 用 mitmproxy 证书 + 系统级信任
- 抓到一次带 token 的请求后，把 token/cookie 塞进 `config.json`，`jc.py` 就能批量跑，不必真机常驻

## 7. 注意事项

- `userKey`/`cookie` 就是账号凭证，别提交进 git、别贴群里
- 单账号并发别猛（`--rate` 保持 ≥1.5s）；被限流时接口返回 429/509，工具会抛错，调大间隔重跑
- 数据用途自行把握：落库后可按剧名/公司/题材做去重与聚合，CSV 用 `utf-8-sig` 编码，Excel 直接双击不乱码

---

## 8. 小程序三榜（红果漫剧榜 / 3D动态漫榜 / AI真人榜）— 已打通，免登录

小程序后端与 Web 端是**两套**服务，小程序这套不需要 userKey：

```
host   : https://playlet-applet.dataeye.com      # 小程序 appid wxa4d39034d8eeffe7
文档   : /v2/api-docs                            # Swagger 2.0，809 个接口，未授权可读
榜单池 : /common/rankConfig/list                 # 30 个榜单的 rankId / rankType / 副标题
```

三个榜的实际接口（GET，无签名、无 token）：

| 榜单 | 接口 | 关键参数 |
|---|---|---|
| 红果漫剧榜 | `/playlet/getComicsPlayletByDate` | `day` `pageId` `pageSize` `type=1`（1 热播 / 2 新剧） |
| 3D动态漫榜 | `/playlet/motionComic` | `day` `pageId` `pageSize` `rankType=1` |
| AI真人榜 | `/playlet/motionComic` | `day` `pageId` `pageSize` `rankType=2` |
| 沙雕漫榜 | `/playlet/motionComic` | `rankType=3` |
| 红果AI短剧榜 | `/playlet/getAiPlayletByDate` | `day` `pageId` `pageSize` |
| 红果真人剧榜 | `/playlet/getHumanPlayletByDate` | 同上 |

`rankType` 语义不是官方口径，是用 `/playlet/rankHonor?playletId=` 逐条回查反推的
（`rankHonor` 的 `type` 编码：1 热力 2 抖音热播 3 快手热播 4 漫剧热播 5 动态漫 6 沙雕漫 7 真人AI）：
用 `day=2026-10-25` 取 `motionComic` 前三条，再看这三条在 `rankHonor` 里的 type/rankNum，
`rankType=1` 的三条在 type=5 上排名正好 1/2/3，`rankType=2` 对应 type=7，`rankType=3` 对应 type=6。

**分页限制（实测）**：`pageSize` 硬上限 30，服务端不认更大值；
`getComicsPlayletByDate` 只有 pageId 1、2 有效（rank 1–60），pageId≥3 回落重复第二页，
虽然 `totalRecords` 报 99 —— 所以第 61–99 名目前取不到；
`motionComic` 分页完全无效，固定返回 Top30。

```bash
python3 jcc_rank.py                    # 最新日期，三榜全量
python3 jcc_rank.py --day 2026-10-25
```

输出到 `data/jcc/`：`剧查查三榜_<day>.xlsx`、分榜 CSV、合并 CSV、原始 JSON、`jcc_ranks.db`（表 `rank_rows`）。

字段说明（红果漫剧榜）：`hotValueText` 热度（原文带万/亿）、`hotValue` 热度数值、
`totalEpisode` 集数、`playletTags` 题材、`producerCompany` 出品、`contractorCompany` 承制、
`relatedPartyCompany` 关联方、`playletNativeId` 红果站内 id、`listDays` 连续霸榜天数。

动态漫/真人AI 榜（motionComic）：`playCount` 播放总量、`playCountAdd` 播放增量、
`rankNum` 排名、`listDays` 连续霸榜、`manufacturer` 承制、`producerList` 出品、
`playletUser` 账号信息（抖音号 / 昵称）。

---

## 9. 详情级采集（jcc_detail.py）

`jcc_rank.py` 输出的是榜单简表；`jcc_detail.py` 把每部剧能拿到的字段全榨干：

```bash
python3 jcc_detail.py                  # 三榜 + 上榜历史 + 制作团队 + 标签翻译
python3 jcc_detail.py --no-honor       # 只要宽表（快，省 115 次请求）
python3 jcc_detail.py --token "xxx"    # 追加剧集详情（见下）
```

输出 `data/jcc/剧查查三榜详情_<day>.xlsx`，6 个 sheet：

| sheet | 行数 | 说明 |
|---|---|---|
| 红果漫剧榜 | 60 | 榜单宽表（30 列） |
| 3D动态漫榜 | 30 | 含播放量字段（39 列） |
| AI真人榜 | 30 | 含播放量字段（38 列） |
| 全部明细 | 121 | 三榜合并（51 列） |
| 上榜历史 | 115 | 每部剧的历史排名/连续霸榜 |
| 剧集详情 | 115 | 制作团队等；`--token` 后自动补齐简介等 |

### 9.1 标签 id 翻译（已自动做）

榜单里的 `tags` 是数字 id（如 `17、87、104、59、92、12、95`），字典来自
`GET /playlet/playletFilterList?isSearch=0`（122 个 value→label，含维度）：

```
17=系统(主题情节)  87=架空(时代背景)  104=大男主(角色设定)
59=玄幻仙侠(主题情节)  92=3D漫(短剧类型)  12=古代  95=男频(频道)  96=女频(频道)
```

输出新增两列：`tags_中文` = 系统、架空、大男主、玄幻仙侠、3D漫、古代、男频；
`tags_维度` = 主题情节=系统/玄幻仙侠；时代背景=架空/古代；角色设定=大男主；短剧类型=3D漫；频道=男频。

### 9.2 上榜历史（免登录）

`GET /playlet/rankHonor?playletId=&pageId=1&pageSize=60` → 每部剧在各榜的历史名次：

```
honor_days   = 2                       # 上过榜的天数
honor_ranks  = 动态漫榜:第15名(2026-10-25)     # 各榜最好名次
honor_history= 2026-10-25 动态漫榜 第15名；…
```

type 编码：1 热力 / 2 抖音热播 / 3 快手热播 / 4 漫剧热播 / 5 动态漫 / 6 沙雕漫 / 7 真人AI。

### 9.3 剧集详情（需登录态）

详情接口对**未登录**统一返回 `{"statusCode":200,"content":null}`（不是 401，是权限过滤），
所以榜单能免登录拿，详情不行。接口清单：

```
/playlet/getPlayletInfoV2              基础详情：简介/题材/集数/上线/平台/出品
/playlet/topData                       top 数据
/playlet/getPlayletRankData            6 类统计（明确返回 401 请注册登录）
/playlet/audienceAnalysis              受众分析
/playlet/makingTeam                    制作团队（免登录可用，见下）
/playlet/listTrendByPlaylet            素材投放趋势
/playlet/listProductDistribution       投放剧场分布
/playlet/listPublisherDistribution     投放公司分布
/playletTitbits/selectByPlayletId      花絮
```

`--token` 接受三种来源（脚本会同时塞到 `token` / `Authorization` 头）：

```bash
python3 jcc_detail.py --token "$JCC_TOKEN"
python3 jcc_detail.py --cookie "SESSION=…; …"
JCC_TOKEN=xxx python3 jcc_detail.py
```

**取 token 的办法**

1. **真机抓包（最省事）**：`pip3 install mitmproxy && mitmdump -p 8080 -w wx.flow`，
   手机 Wi-Fi 代理指向电脑 8080，装并信任 mitmproxy 证书，微信里打开剧查查小程序翻一次榜单；
   `mitmdump -nr wx.flow "~d dataeye.com" -w wx_dEYE.flow` 过滤后看请求头里的 `token` / `Cookie`。
2. **微信开发者工具**：导入小程序码包，Network 面板找 `/login/loginUser` 之后的请求头。
   小程序登录是 `GET /login/loginUser?appType=…&code=…`（code 由 `wx.login` 产生，只能用一次）。
3. **网页端与会话**：Web 端（adxray）登录态是 `userKey`，两套后端不通用，小程序要它自己的 token。

目前实测：**制作团队（makingTeam）免登录可拿**，115 部里 17 部有数据（含配音/演员 `synthespian`、
`celebrityId`）；其余详情字段卡在登录墙。

### 9.4 字段字典（宽表 51 列里的重点）

| 列 | 含义 |
|---|---|
| `_rank_pos` / `ranking` | 榜内名次 |
| `hotValue` / `hotValueText` | 红果热度（数值 / 原文“8602万”） |
| `playCount` / `playCountAdd` | 抖音播放总量 / 播放增量（动态漫、真人AI 榜有） |
| `totalEpisode` | 集数 |
| `tags_中文` / `tags_维度` | 题材标签（已翻译，带维度） |
| `playletTags` | 平台侧标签原文 |
| `producerCompany` / `producerList` | 出品方 |
| `contractorCompany` / `contractorList` | 承制方 |
| `copyrightHolderCompany` / `copyrightHolderList` | 版权方 |
| `platformList` | 播出平台 |
| `relatedPartyCompany` | 关联方（如“番茄”） |
| `playletUser` | 抖音号：昵称 / 账号 / userId |
| `playletNativeId` | 红果站内剧 id（可直接拼红果链接） |
| `listDays` | 连续霸榜天数 |
| `playletOnlineTime` / `releaseStartDate` | 上线时间 |
| `honor_days` / `honor_ranks` / `honor_history` | 上榜历史 |

---

## 10. 免登录拿详情：红果站内页（jcc_hongguo.py）

**关键发现**：榜单里的 `playletNativeId` 就是红果的 `series_id`。
红果详情页是 SSR，`seriesDetail` 直接写在 HTML 里——**不用登录、不用签名、不用 token**。

```
https://hongguoduanju.com/detail?series_id=<playletNativeId>
```

```bash
python3 jcc_hongguo.py                 # 三榜 + 红果详情（约 8 分钟 / 63 列）
python3 jcc_hongguo.py --workers 8 --limit 10   # 调试
```

输出 `data/jcc/剧查查三榜_红果详情_<day>.xlsx`，多一个「红果详情」sheet。

### 10.1 能拿到的字段（`seriesDetail`）

| 字段 | 说明 | 实测覆盖 |
|---|---|---|
| `hg_name` | 红果剧名 | 52/52 |
| `hg_intro` | **完整简介**（几百字） | 52/52 |
| `hg_tags` | 题材（玄幻/系统/异界…） | 52/52 |
| `hg_episode_text` | “全1000集” | 52/52 |
| `hg_episode_cnt` | 总集数 | 52/52 |
| `hg_accessible_episode` | 免费可看集数 | 52/52 |
| `hg_status` | 连载中 / 已完结 | 52/52 |
| `hg_vid_count` / `hg_vid_head` | 分集 vid 数量 / 前 5 个（可直接拼播放） | 52/52 |
| `hg_celebrities` / `hg_celebrity_n` | 演员表 | 部分剧为空 |
| `hg_first_visible` / `hg_pay_type` / `hg_cover` | 上线时间戳 / 付费类型 / 封面 | 52/52 |

### 10.2 两个坑

1. **DNS 被劫持**：本机若开代理（TUN/fake-ip），`hongguoduanju.com` 会解析成 `198.18.x.x` 直接连不上。
   脚本先走 DoH（阿里 / Cloudflare）拿真实 A 记录，再 patch `socket.getaddrinfo`，保留 SNI 为原域名。
   手工验证：`curl --resolve hongguoduanju.com:443:<真实IP> https://hongguoduanju.com/detail?series_id=…`
2. **流式截断**：页面约 800KB，脚本边收边找 `"seriesDetail":`，凑够 `vid_list` 就断开；
   服务器偶发 `ChunkedEncodingError`，已做「已收数据照样解析 + 重试」。

### 10.3 覆盖边界（实测 2026-10-26）

| 榜单 | 可补详情 | 原因 |
|---|---|---|
| 红果漫剧榜 | **49 / 60** | `playletNativeId` 齐全；11 部是网络抖动/红果无此剧 |
| 3D动态漫榜 | 3 / 30 | 抖音原生榜，DataEye **不给** `playletNativeId` |
| AI真人榜 | 0 / 30 | 同上 |

抖音原生两榜要补详情，只有两条路：带小程序 token 调 DataEye 详情接口，或自己做抖音 `a_bogus` 签名。

## 11. 开源方案参考

红果系已经有一批开源客户端把接口逆向好了，不用重复造轮子：

| 项目 | 星 | 能复用什么 |
|---|---|---|
| [N3urda/hongguoTV](https://github.com/N3urda/hongguoTV) | 797 | `server/provider.mjs` + `server/vendor/hongguo.cjs`：红果推荐/搜索/详情/选集/播放解密，**本文档的 seriesDetail 解析就出自这里** |
| [yangbin1322/hongguo-player](https://github.com/yangbin1322/hongguo-player) | 1310 | 红果桌面客户端，接口行为参考 |
| [zhoufuweigg/guoapp](https://github.com/zhoufuweigg/guoapp) | 1439 | 红果客户端源码，多端实现 |
| [KEJIYUNB/hongguo](https://github.com/KEJIYUNB/hongguo) | 136 | 红果模块化封装 |
| [jackherex/duanjuzhijia-down-tv](https://github.com/jackherex/duanjuzhijia-down-tv) | 115 | 红果 TV 端播放器，选集/播放链路 |

`vendor/hongguo.cjs` 里有红果 App 的搜索接口完整签名（`api5-normal-sinfonlinea.fqnovel.com/reading/bookapi/search/tab/v`，
sm3 + AesV3 + protobuf + X-Bogus 类加密，签名函数约 60 行），**装了 node 就能直接跑**：

```bash
cd /tmp && git clone --depth 1 https://github.com/N3urda/hongguoTV && cd hongguoTV
npm i && node -e "const p=require('./server/vendor/hongguo.cjs'); console.log(p.appSearchPage('奶团子一开口咱家好运来',11,1,20))"
```

用它可以按**剧名**反查红果 `series_id`，从而给「DataEye 没给 native id」的抖音榜剧补红果详情——
这是目前唯一不需要登录态的补全路径。

## 12. 登录态到底要不要 / 会不会过期

| 需求 | 登录态 |
|---|---|
| 三榜排名、热度、集数、题材、公司、上榜历史 | 不要 |
| 红果漫剧榜的简介/分集/状态（`jcc_hongguo.py`） | 不要 |
| DataEye 详情接口（`getPlayletInfoV2` 等，覆盖抖音两榜） | 要 |
| 抖音原生剧的详情（不走 DataEye） | 不要，但要自己算 `a_bogus` |

小程序 token 一般是**微信会话级**（几天到一个月），过期表现是接口返回 `401 请注册登录`，
或者详情接口静默返回 `content:null`。脚本侧已支持：

```bash
python3 jcc_detail.py --token "$JCC_TOKEN"     # 环境变量
python3 jcc_detail.py --cookie "SESSION=…"
```

换号/换 token 直接重跑即可，不需要改代码；建议一次跑完整个榜单，别让 token 在 60 部的中途过期。
