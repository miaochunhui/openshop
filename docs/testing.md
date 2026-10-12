# 测试与验收

本页记录 OpenShop **v0.1.1** 的现读验收口径、命令、证据和环境边界。结论只对下方的运行时刻负责。

## 1. 分层门

```bash
python3 -B tools/smoke_server.py
python3 -B -m unittest discover -s tests -v
python3 -B tests/test_trade_concurrency.py
python3 -B tools/check_repository.py --selftest
python3 -B tools/check_repository.py --check

# 发布归档工具的 10 项负控（无需 tag/Node/Chromium）
python3 -B tools/rehearse_release.py --selftest --out-dir /path/to/empty-evidence
```

本机最近现读：

| 门 | 结果 |
| --- | --- |
| 冒烟 | PASS（会话/CSRF/demo 登录登出） |
| `unittest discover` | **22 tests PASS** |
| 并发验收 | **3 rounds × 7 scenarios PASS** |
| 仓库门自检 | 20 cases / 19 negative controls PASS |
| 仓库门 `--check` | **PASS 19 checks / 0 failures / 1 non-blocking warning** |
| 发布演练负控 | **10/10 controls PASS** |
| API 验收 | **35 PASS / 0 FAIL** |
| Chromium E2E | **11 PASS / 0 FAIL / 0 BLOCKED**，13 截图 |

## 2. 标准库测试（可移植）

默认把隔离数据库、日志和服务器数据写到系统临时目录，不需要 3FS：

- `OPENSHOP_TMP_BASE`：自定义临时根。
- `OPENSHOP_CATALOG_TEST_PORT`、`OPENSHOP_TRADE_TEST_PORT`、`OPENSHOP_SELLER_TEST_PORT`：三套真实 HTTP 测试的端口覆盖。
- 本轮 `unittest discover`：**22 tests PASS**（含发布演练负控的正式测试）。
- CI 使用只读权限，不安装依赖、不注入凭据、不访问外部网络。

最近本地日志：`/hf3fs-jd/data/tmp/openshop/lead/final2-unit-20261013T013142.log`（较早的 CI 等价运行见 `ci-unit-20261013T012055.log`）。

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
CONCURRENCY_ACCEPTANCE verdict=PASS rounds=3 port=8881 elapsed_seconds=37.631
/hf3fs-jd/data/tmp/openshop/trade-concurrency-final/run-20261012T171926741056/
  acceptance.json  console.log
  round-1..3/{summary.json,server.log,scenario-*.json,data}
```

CI 的同样步骤以系统临时目录运行（本地模拟结果 `CONCURRENCY_ACCEPTANCE verdict=PASS ... elapsed_seconds=4.573`，日志 `/hf3fs-jd/data/tmp/openshop/lead/ci-concurrency-20261013T012055.log`）。
同一套件在提交前还由 backend-trade 席提交并由 Lead 独立复跑；未 push，未把它当作多进程或容量证明。

## 4. 完整真实浏览器验收

该套件启动真实 stdlib 服务、真实 SQLite、真实 cookie/CSRF 和 Chromium；所有业务 API 来自后端，不使用 mock。工具在运行 API 与浏览器前分别跑“CSRF 检测器应红”和“外部请求检测器应红”的自检。

本机命令（这些工具/浏览器安装在当前开发机，CI 不运行浏览器套件）：

```bash
python3 -B tools/qa/run_acceptance.py \
  --api-port 8874 --browser-port 8875 \
  --out-root /hf3fs-jd/data/tmp/openshop/qa/final-v011b-<stamp>
```

最近一次 v0.1.1 全绿：

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
/hf3fs-jd/data/tmp/openshop/qa/final-v011b-20261013T012502/acceptance-20261013T012502989731-4091857/
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

### 运行环境导致的假红（保留）

v0.1.1 的第一次全量复跑在 E2E-05 假红：3FS 上冷 `GET /api/cart` 耗时 6.075s（无 5xx；其他 quote/address/coupon 请求正常完成），而地址控件等待上限合计约 6.2s，先于页面数据渲染结束。失败原始证据在 `/hf3fs-jd/data/tmp/openshop/qa/final-v011-20261013T012216/acceptance-20261013T012216372640-4021656/`。保留证据后把控件等待扩大为 20s/10s，并在同一代码上重跑得到上面的 11/11 PASS。生产/其他文件系统仍应自行做性能验收；该假红不是被“隐藏成功”，服务端确实返回了 200，但页面完成前测试已经超时。

## 5. API 与安全覆盖摘要

`OPENSHOP_API_ACCEPTANCE verdict=PASS pass=35 fail=0` 覆盖健康检查、Cookie/CSRF、角色隔离、注册/登录、购物车合并、目录/搜索/评价、地址越权、优惠券/报价、幂等建单、支付仅 demo、商家发货、确认收货、评价聚合、取消/售后、并发超卖与 actions 权威等。详细阶段判定在 `api/api-summary.json`。

独立 44 探针审计见 [docs/peer-review-core-api.md](peer-review-core-api.md)。原始 5 个 Low finding 已修复、五个 finding 探针复测转 PASS；审计脚本的三个 control/scope 红项来自一次性脚本的过时 seller-stub 断言和共享限速桶，不是 CI 门或未修复 finding。当前 QA 另行覆盖 seller/浏览器/并发路径。

## 6. 发布演练与 CI

- 本地发布演练的范围、10 个负控、真实归档/解包/启动检查见 [docs/RELEASE-REHEARSAL.md](RELEASE-REHEARSAL.md)。
- CI 配置顺序：标准库测试 → 三轮并发 → 仓库门 selftest → 仓库门；都只有只读权限，不安装依赖、不部署。
- 本轮在本机以相同命令模拟全绿；**没有在远端 GitHub 实际触发 CI**，也没有 push、部署或修改 DNS/TLS/Caddy/systemd。

## 7. 未覆盖 / 明确不宣称

- 并发只覆盖单进程 SQLite、单机 loopback、直连 DB 构造的 fixture；没有多进程/多机、文件系统锁超时、卖家售后审批并发、拼团过期并发、长压测或容量/SLA 证据。
- 真实浏览器依赖当前开发机的 Chromium/Playwright/3FS 路径；CI 不运行它。
- 未执行 systemd 安装、线上切换、TLS/反代、生产迁移和回滚演练。
- 拼团到期无后台自动取消退款；售后仅整单单店铺；无真实资金/物流/KYC/风控。
- 商品、店铺、人物、评价、订单、支付与物流均为虚构/模拟。
