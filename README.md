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
