# 测试与验收

本页记录 OpenShop **v0.2.0** 的现读验收口径、命令、证据和环境边界。结论只对下方的运行时刻负责。

## 1. 分层门

```bash
python3 -B tools/smoke_server.py
python3 -B -m unittest discover -s tests -v
python3 -B tests/test_trade_concurrency.py
python3 -B tests/test_ai_mediator.py -v
python3 -B tests/test_peer_review_probes.py
python3 -B tools/check_repository.py --selftest
python3 -B tools/check_repository.py --check

# 发布归档工具的 10 项负控（无需 tag/Node/Chromium）
python3 -B tools/rehearse_release.py --selftest --out-dir /path/to/empty-evidence
```

本机最近现读：

| 门 | 结果 |
| --- | --- |
| 冒烟 | PASS（会话/CSRF/demo 登录登出） |
| `unittest discover` | **29 tests PASS**（含 AI 假 provider 真实 HTTP 安全集成与可信代理 IP 单测） |
| 并发验收 | **3 rounds × 7 scenarios PASS** |
| 独立安全审计 | **44/44 probes PASS；39/39 controls PASS；5/5 findings 回归 PASS** |
| 公网完整购物 Chromium | **11 PASS / 0 FAIL**，13 截图、0 errors，真实模拟支付/发货/收货/评价 |
| 公网 AI 浏览器 + API | **PASS**：真实模型、Secure/HttpOnly Cookie、CSRF 403、401、采纳、409 |
| 仓库门自检 | 20 cases / 19 negative controls PASS |
| 仓库门 `--check` | **PASS 19 checks / 0 failures**；非阻断 warnings = Actions tag-pin + 离线未抓取各文档外链（数量以当次输出为准） |
| 发布演练负控 | **10/10 controls PASS** |
| API 验收 | **35 PASS / 0 FAIL** |
| Chromium E2E | **11 PASS / 0 FAIL / 0 BLOCKED**，13 截图 |

## 2. 标准库测试（可移植）

默认把隔离数据库、日志和服务器数据写到系统临时目录，不需要 3FS：

- `OPENSHOP_TMP_BASE`：自定义临时根。
- `OPENSHOP_CATALOG_TEST_PORT`、`OPENSHOP_TRADE_TEST_PORT`、`OPENSHOP_SELLER_TEST_PORT`：三套真实 HTTP 测试的端口覆盖。
- 本轮 `unittest discover`：**29 tests PASS**（22 项原有门 + 3 项 AI + 4 项可信代理；含发布演练负控的正式测试）。
- 新增 `tests/test_ai_mediator.py` 用本地 OpenAI-compatible fake provider 启真实服务器和真实 SQLite，覆盖 CSRF 403、未登录 401、真实供给校验、服务端价格覆盖、模型只调用一次、采纳不建单、二次采纳 409、用户额度 429。
- CI 使用只读权限，不安装依赖、不注入凭据、不访问外部网络；AI 测试不会调用真实模型。

最近本地日志：`/hf3fs-jd/data/tmp/openshop/lead/v020-final-unit-20261013T083052.log`。

## 3. 三轮真实并发验收

`tests/test_trade_concurrency.py` 在每轮使用全新 SQLite 数据目录，在 loopback 端口 8881 启动真实服务，用独立 cookie/CSRF 的并发客户端发真实 HTTP。通过条件为 **连续 3 轮全部通过**，有 500/transport 或半状态即失败。

```bash
OPENSHOP_CONCURRENCY_EVIDENCE_ROOT=/path/to/durable-evidence \
  python3 -B tests/test_trade_concurrency.py
# OPENSHOP_CONCURRENCY_TEST_PORT 可覆盖 8881
```

每轮 7 个场景：

1. **stock_race**：8 个独立买家会话抢 stock=1 的 offer；恰好一个 200，其余 409 `STOCK_CHANGED`，stock 1→0，订单行仅 1。
2. **idempotency_race**：4 个并发同 idempotency key 建单，返回同一 order_no，订单/库存只落一次。
3. **coupon_redemption_race**：同一券并发核销，最多一单成功，券只绑定一个订单。
4. **coupon_claim_race**：发券 quantity=1，5 个真实客户端竞争领取，1×200 / 4×409，claimed_count=1。
5. **group_join_pay_race**：团容量 3；并发入团 2×200 / 3×409，公开人数只计已付款，付款后 FORMING/1→FULL/3，三个成员订单全部 PAID。
6. **cancel_cancel_race**：两个不同会话并发取消，同一订单只有一次取消成功，库存只回补一次。
7. **pay_cancel_race**：支付/取消并发，只有一个终态胜者；胜者可为 pay 或 cancel（本轮 Lead 复跑三轮均为 pay 胜、cancel 409），无半状态或 500。

本轮（3FS durable evidence）：

```text
CONCURRENCY_ACCEPTANCE verdict=PASS rounds=3 port=8881 elapsed_seconds=32.582
/hf3fs-jd/data/tmp/openshop/concurrency-v020/run-20261013T003147689442/
  acceptance.json  console.log
  round-1..3/{summary.json,server.log,scenario-*.json,data}
```

CI 的同样步骤以系统临时目录运行（v0.2.0 结果 `CONCURRENCY_ACCEPTANCE verdict=PASS ... elapsed_seconds=32.582`，日志 `/hf3fs-jd/data/tmp/openshop/lead/v020-final-concurrency-20261013T083052.log`）。
同一套件由 backend-trade 席提交、Lead 独立复跑后进入 CI；它随后随公开仓推送，但仍不证明多进程或容量。

## 4. 完整真实浏览器验收

该套件启动真实 stdlib 服务、真实 SQLite、真实 cookie/CSRF 和 Chromium；所有业务 API 来自后端，不使用 mock。工具在运行 API 与浏览器前分别跑“CSRF 检测器应红”和“外部请求检测器应红”的自检。

本机命令（这些工具/浏览器安装在当前开发机，CI 不运行浏览器套件）：

```bash
python3 -B tools/qa/run_acceptance.py \
  --api-port 8874 --browser-port 8875 \
  --out-root /hf3fs-jd/data/tmp/openshop/qa/final-v020-<stamp>
```

最近一次本地 v0.2.0 全绿（公网复跑另见下）：

```text
OPENSHOP_ACCEPTANCE verdict=PASS rc=0
selftest_csrf=PASS  selftest_external=PASS
api=PASS            API 35 PASS / 0 FAIL
browser=PASS        11 PASS / 0 FAIL / 0 BLOCKED
console_errors=0    page_errors=0    external_requests=0    api_errors=0
screenshots=13
```

证据目录：

```text
/hf3fs-jd/data/tmp/openshop/qa/final-v020-20261013T083310/acceptance-20261013T083310656811-836095/
  acceptance-summary.json
  api/api-summary.json
  browser/browser-summary.json
  browser/screenshots/*.png
  logs/*
```

浏览器阶段：

1. E2E-01 首页真实目录、无 stub、无横向溢出；
2. E2E-02 站内搜索 → 商品详情；
3. E2E-03 商品报价/SKU/加购；
4. E2E-04 一键买家登录并合并游客购物车；
5. E2E-05 服务端报价、新地址、适用券；
6. E2E-06 幂等建单、模拟支付、订单 DOM；
7. E2E-07 一键商家登录、真实发货；
8. E2E-08 买家看到承运商/运单号；
9. E2E-09 确认收货并看到 COMPLETED；
10. E2E-10 真实 390px 移动截图、无横向溢出；
11. E2E-11 最终审计（动作次数、截图、仿真支付披露、无 mock）。

### 公网完整购物 E2E（v0.2.0，真实 HTTPS + 真实 Chromium）

2026-10-13 08:43 以同一套真实浏览器脚本显式授权公网目标
https://openshop.ai4everyone.top，跑完 11 个阶段：

- 首页真实目录、搜索→商品、真实加购、游客购物车在演示买家登录后合并；
- 新地址与服务端报价、可用券、服务端总价与模拟支付；
- 商家真实发货，买家看到承运商/运单号并确认收货，提交评价；
- 真实 390px 移动首页与移动订单页，无横向溢出；
- 11/11 PASS、13 张公网截图、0 console/page/external/API errors；
  订单 OS20261013004340PLSF，测试账户/数据均在公开演示库。

公网入口证据：
    /hf3fs-jd/data/tmp/openshop/deploy/public-full-e2e-20261013T084320/

### AI 中介真实模型证据（v0.2.0）

- 本地真实网关探针：模型 deepseek-flash，真实目录 18 商品/43 报价；
  首次 1,100 输出 token 上限返回空 content，服务端返回
  AI_INVALID_RESPONSE（未伪造），提高到 4,000 后返回合法 JSON。
- 自动化测试：本地 fake provider 真 HTTP（29 tests 之一），模型不触网。
- 真实 API（公网 HTTPS）：
    CSRF 403、未登录 401、模型 200、采纳、二次采纳 409、无订单；
    deepseek-flash 19.2 秒、9643 prompt tokens、3060 completion tokens。
  证据：/hf3fs-jd/data/tmp/openshop/deploy/public-ai-api-evidence-20261013T084504/api-live.json
- 真实 Chromium（公网 HTTPS）：
    在线 AI 状态、demo 买家登录、真实模型、服务端价格、方案采纳；
    Secure/HttpOnly session cookie、HSTS/CSP、0 console/page/external
    errors；桌面 1440 与移动 390 两个视口均无溢出。
  证据：/hf3fs-jd/data/tmp/openshop/deploy/public-ai-browser-20261013T084044/
  截图已入仓 docs/screenshots/ai-mediator-page-desktop.png、
  ai-mediator-plan-desktop.png、ai-mediator-adopted-mobile.png。
- 一次公网 AI API 流程已成功但写证据目录权限失败，脚本退出非 PASS；
  保留该记录并以可写目录重跑得到上述 PASS，未把它当第一次全绿。

### 真实拼团与商家库存补验（人工真实运行）

在 11 阶段浏览器旅程之外，另用真实 Chromium 对 8780 本地服务做了两项交互（记录时运行 v0.1.1，v0.1.2 仅有版本号差异；完整 11 阶段已在 v0.1.2 重跑）：

- 自营 2 人团：`demo_buyer` 在商品页点“发起拼团”，真实结算 + 模拟支付生成团 `GYKNKDZ`，公开进度 1/2；随后一个真实注册的新买家在拼团页点“加入拼团购物车，去结算”，经真实报价、建单、模拟支付后，团变为 **FULL 2/2**，两名已付款成员可见。
- 商家库存：商家在 `/seller?tab=inventory` 把 offer 1 的库存从 **26 改为 27**，真实 PATCH `/api/seller/products/1` 返回 200；刷新后仍为 27。
- 两轮真实浏览器运行的 console/page errors 均为 0。
- 证据：`/hf3fs-jd/data/tmp/openshop/lead/group-join-evidence/202610121746293/`、`/hf3fs-jd/data/tmp/openshop/lead/group-inventory-evidence/20261012174325/`，整理说明 `/hf3fs-jd/data/tmp/openshop/lead/GROUP-INVENTORY-EVIDENCE-20261013.md`。

这两项补充了真实交互证据，但没有扩成可重复自动化 CI 阶段；仍受下方未覆盖边界约束。

### 运行环境导致的假红（保留）

v0.1.1 的第一次全量复跑在 E2E-05 假红：3FS 上冷 `GET /api/cart` 耗时 6.075s（无 5xx；其他 quote/address/coupon 请求正常完成），而地址控件等待上限合计约 6.2s，先于页面数据渲染结束。失败原始证据在 `/hf3fs-jd/data/tmp/openshop/qa/final-v011-20261013T012216/acceptance-20261013T012216372640-4021656/`。保留证据后把控件等待扩大为 20s/10s，并在同一代码上重跑得到上面的 11/11 PASS。生产/其他文件系统仍应自行做性能验收；该假红不是被“隐藏成功”，服务端确实返回了 200，但页面完成前测试已经超时。

## 5. API 与安全覆盖摘要

`OPENSHOP_API_ACCEPTANCE verdict=PASS pass=35 fail=0` 覆盖健康检查、Cookie/CSRF、角色隔离、注册/登录、购物车合并、目录/搜索/评价、地址越权、优惠券/报价、幂等建单、支付仅 demo、商家发货、确认收货、评价聚合、取消/售后、并发超卖与 actions 权威等。详细阶段判定在 `api/api-summary.json`。

独立 44 探针审计见 [docs/peer-review-core-api.md](peer-review-core-api.md)。v0.2.0 整包现已 **44/44 probes PASS、39/39 controls PASS、5/5 finding 回归 PASS**，无失败、无 skip、无白名单。旧 seller-stub scope 已替换为 guest 401 / buyer 403 / seller 200 且店铺匹配的真实 RBAC；三个限速 control 通过同库原地进程重启获得干净的内存限速桶，session/CSRF 和 SQLite 数据保留。AI 另有自动化安全控制（CSRF、登录、配额、供给校验、一次性方案、订单未创建）与真实公网验证。

```text
PEER_REVIEW verdict=PASS probes=44 controls=39 failures=0 findings=0
server_boots=4 restarts=3
/hf3fs-jd/data/tmp/openshop/peer-review-v020/run-20261013T003220467815-815132/
```

## 6. 发布演练与 CI

- 本地发布演练的范围、10 个负控、真实归档/解包/启动检查见 [docs/RELEASE-REHEARSAL.md](RELEASE-REHEARSAL.md)。
- CI 配置顺序：标准库测试（含 AI fake provider 集成测试）→ 三轮并发 → 独立 44 探针安全审计 → 仓库门 selftest → 仓库门；远端配置只有只读权限，不安装依赖、不部署。
- 本轮本机以相同命令全绿；[GitHub Actions](https://github.com/miaochunhui/openshop/actions/workflows/ci.yml) 由 master push 触发。终态与逐 job 结论以 Actions 和当次外部证据为准，静态文档不冒充 CI PASS。
- systemd、DNS、TLS 与两台 Caddy 边缘已经实际部署，详见 [deployment.md](deployment.md)；代码回滚已演练，Caddy 边缘回滚未执行。外部公开仓、发布/回滚边界见 [repositories.md](repositories.md)。
- 公网与外部源码入口：[GitHub](https://github.com/miaochunhui/openshop)、[Gitee](https://gitee.com/sdfwefqaa/openshop)、本站 tag 归档 https://openshop.ai4everyone.top/web/source/openshop-0.2.0.tar.gz。

## 7. 未覆盖 / 明确不宣称

- 并发只覆盖单进程 SQLite、单机 loopback、直连 DB 构造的 fixture；没有多进程/多机、文件系统锁超时、卖家售后审批并发、拼团过期并发、长压测或容量/SLA 证据。
- 安全审计的 44 探针仍只覆盖 API/夹具可及的面（AI 另有本节列出的自动化与在线验证）；它没有覆盖分布式配额、Caddy 配置回滚、拼团过期清理或公网渗透，不是合规证明。
- 真实浏览器依赖当前开发机的 Chromium/Playwright/3FS 路径；CI 不运行主浏览器套件。公网 E2E 与 AI 浏览器验收为手工编排的真实运行，不是远端 CI 阶段。
- systemd 安装、应用切换、DNS、Let’s Encrypt 与两台上游 Caddy 热重载已实际执行；回滚演练进行了代码回滚，发现前进切换必须显式 restart，并已修正。Caddy 边缘回滚备份/命令存在但本次未执行；没有生产数据迁移。
- AI 每日额度、单进程 IP 窗口、模型供应商配额与延迟；模型定性文案可能不精确；服务重启清空内存方案/IP 窗口。
- 外部公开 Git 仓与 GitHub Actions 已接入；CI 结论以 Actions 页面和外部交接证据为准。发布工具有独立的 dry-run/selftest，不自动重试；两 token 文件未改。Gitee 空仓不能公开，故先私有推送后切公开。
- 拼团到期无后台自动取消退款；售后仅整单单店铺；无真实资金/物流/KYC/风控。
- 商品、店铺、人物、评价、订单、支付与物流均为虚构/模拟。
