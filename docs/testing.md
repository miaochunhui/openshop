# 测试与验收

本页记录 OpenShop v0.1.0 的现读验收口径、命令、证据和未覆盖边界。结论只对文末记录的运行时刻与提交负责。

## 1. 最快速的门

```bash
python3 -B tools/smoke_server.py
python3 -B -m unittest discover -s tests -v
python3 -B tools/check_repository.py --selftest
python3 -B tools/check_repository.py --check
```

- 冒烟脚本会启动真实 HTTP 服务，验证会话、CSRF 与 demo 登录/登出，并保留数据目录。
- 标准库测试默认使用系统临时目录下的隔离数据库；CI 与普通开发机不需要 3FS。覆盖：
  - 目录/首页/搜索/评价/收藏/地址 API 与所有权；
  - 购物车、服务端报价、优惠券、幂等下单、库存、订单状态、评价、取消、售后；
  - 商家概览、店铺隔离、发货、售后审批、参数与越权负控；
  - 本轮本地 `unittest discover`：**21 tests PASS**。
- 可用环境变量覆盖测试落点和端口：
  - `OPENSHOP_TMP_BASE`（所有隔离数据/日志的根目录）
  - `OPENSHOP_CATALOG_TEST_PORT`（默认 8861）
  - `OPENSHOP_TRADE_TEST_PORT`（默认 8862）
  - `OPENSHOP_SELLER_TEST_PORT`（默认 8865）
- 完整仓库门需要系统 Node（做 JS 语法检查）；产品本身不需要 Node。
- 本轮仓库门：**REPOSITORY_GATE verdict=PASS checks=19 failures=0 warnings=1**。唯一 warning 是 GitHub Actions 的 checkout action 以 tag 固定而非 commit SHA；CI 配置已限制 `contents: read`。

## 2. 完整真实浏览器验收

该套件启动真实 stdlib 服务、使用真实 SQLite、cookie/CSRF 与 Chromium；所有业务 API 都由真实后端提供，不是 mock。它比标准库测试多了 UI 行为、响应式、控制台/外链请求检查。

本轮使用的命令（主机相关绝对路径是因为本仓验收工具/浏览器安装在此开发机；CI 不运行浏览器套件）：

```bash
sudo -n -u infra python3 -B tools/qa/run_acceptance.py \
  --api-port 8874 --browser-port 8875 \
  --out-root /hf3fs-jd/data/tmp/openshop/qa/lead-full-<timestamp>
```

依赖：Python 3.10+、Node、Chromium、Playwright。工具会做两个自检（故意错误的 CSRF 必须红、外部请求检测必须红），再依次运行 API 和浏览器阶段。

本轮原始总判定：

```text
OPENSHOP_ACCEPTANCE verdict=PASS rc=0
selftest_csrf=PASS  selftest_external=PASS
api=PASS            API 35 PASS / 0 FAIL
browser=PASS        11 PASS / 0 FAIL / 0 BLOCKED
console_errors=0    page_errors=0    external_requests=0    api_errors=0
screenshots=13
```

浏览器阶段：

1. E2E-01 首页真实目录、无 stub、无横向溢出；
2. E2E-02 站内搜索 → 商品详情；
3. E2E-03 商品报价/SKU/加购；
4. E2E-04 一键买家登录并合并游客购物车；
5. E2E-05 服务端报价、新地址、优惠券；
6. E2E-06 幂等建单、模拟支付、订单 DOM；
7. E2E-07 一键商家登录、真实发货；
8. E2E-08 买家看到承运商/运单号；
9. E2E-09 确认收货并看到 COMPLETED；
10. E2E-10 真实 390px 移动截图与无溢出；
11. E2E-11 最终审计（动作次数、截图、仿真支付披露、无 mock API）。

最近一次全绿证据目录（UTF-8 JSON、日志、13 张 PNG）：

```text
/hf3fs-jd/data/tmp/openshop/qa/lead-full-20261013T002644/acceptance-20261013T002644937322-2734551/
   acceptance-summary.json
   api/api-summary.json
   browser/browser-summary.json
   browser/screenshots/*.png
```

## 3. API 验收覆盖

`OPENSHOP_API_ACCEPTANCE verdict=PASS pass=35 fail=0`，覆盖：

- 健康检查、Cookie 属性、CSRF 缺失/错误/轮换后旧 token 拒绝；
- 角色隔离、注册/登录校验、PBKDF2 哈希、游客购物车合并；
- 首页聚合、搜索分面、商品详情、报价字段、评价读取；
- 购物车校验、越权、价格伪造无效、地址 CRUD/越权 404；
- 优惠券领取/门槛/分类范围/过期；服务端报价；
- 订单创建/幂等重放与冲突/库存权威；支付仅 demo；
- 商家发货与订单状态；确认收货、评价聚合与重复评价；
- 取消恢复库存与优惠券；售后审批/拒绝/多店边界；
- **并发超卖**（stock=1，两个真实 HTTP 请求）；
- 价格篡改和服务端 actions 权威。

## 4. 独立安全审查

`docs/peer-review-core-api.md` 记录 44 个独立 HTTP 探针的原始审计。原始发现 5 项 Low：

1. 登录/注册不轮换会话标识；
2. 成功注册重置 IP 注册限速桶；
3. 密码登录没有跨 identifier 的 IP 总闸；
4. 超过 100 个 query 字段抛 500；
5. 静态路径含 NUL 导致断连/线程 traceback。

Lead 已在 commit `83fdd92` 修复，并复跑探针确认 5 个 finding probe 全部转 PASS。该一次性审计脚本末尾还有 3 个**非产品结论**的红项（seller 合入后过时的“API 尚未存在”scope 检查、以及同进程共享限速桶导致两个 control 的计数被彼此污染）；它们不进入本仓 CI，也不等价为未修复产品漏洞。详见报告末尾“修复后复核”。

## 5. 未覆盖 / 已知边界

- 完整三轮并发专项套件因执行该席 agent 的基础设施失败而未交付；只声明已跑过的并发探针与 QA 的真实并发超卖。不做规模化压测/分布式并发承诺。
- 无真实 TLS/反向代理/Caddy/client-IP 链、HSTS、跨实例会话或限速验证。
- 拼团 `expires_at` 在查看/参与/支付时拒绝过期团，但没有后台定时扫团、自动取消或自动退款。
- 售后为整单单店铺；多店售后返回明确 422。
- 真实浏览器套件依赖开发机的 3FS、Chromium 与 Playwright 路径，未接入 GitHub Actions；CI 只跑可移植的标准库测试 + 仓库门。
- 未在 GitHub 上实际触发 Actions，未推送远端，未做公网部署。
- 商品、店铺、人物、评价、支付和物流全部为虚构/模拟，不适用于真实交易。
