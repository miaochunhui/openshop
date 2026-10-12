# OpenShop · 开集

> 逛得开，买得值。

OpenShop（开集）是一个可自托管、可审阅的开源多边电商参考实现。它把**官方自营、品牌旗舰、源头拼团**三种供给放进同一套发现、比较、下单与售后体验里：同一件商品可以并排比较谁发货、多久到、贵在哪里。

后端只依赖 Python 标准库与 SQLite，前端为原生 ES Module。没有构建步骤、第三方运行时依赖、CDN、外部字体或分析脚本，便于本地运行和代码审阅。

**发布状态：v0.1.0 发布候选，未发布、未上线。** 本文说明 v0.1.0 的目标范围与运行方式，不把接口契约、占位界面或文案当成已完成功能。当前完成度请以测试和仓库门的现跑输出为准；发布门槛见 [docs/RELEASE.md](docs/RELEASE.md)。

> **真实边界**：全部商品、品牌、店铺、人物、评价与订单均为虚构演示数据；支付是明确标注的模拟流程，不产生真实扣款；这是**非生产级**单进程参考实现，不提供生产级容量、可用性或安全承诺，也未在公网发布。

## 快速开始

前置条件：**Python 3.10+**（含标准库 `sqlite3`）。运行应用不需要 Node、npm 或 pip；系统 Node 只被开发期的仓库门用于 JavaScript 语法检查。

在仓库根目录执行，显式指定独立数据目录与固定端口：

```bash
python3 -B apps/storefront/server.py --host 127.0.0.1 --port 8780 --data-dir "$HOME/.local/share/openshop-demo"
```

打开 <http://127.0.0.1:8780/>。首次启动会在 `--data-dir` 指定目录创建 `openshop.sqlite3` 并幂等播种；请使用仓库外的独立目录，不要把运行数据库提交进 Git。

健康检查：

```bash
curl -fsS http://127.0.0.1:8780/healthz
curl -fsS http://127.0.0.1:8780/api/health
```

第一条应输出 `ok`，第二条返回含 `"demo":true` 的 JSON。更完整的本地安装、数据目录与故障排查见 [docs/INSTALL.md](docs/INSTALL.md)。

### 演示账号

演示账号通过右上角登录浮层的一键演示进入，不需要也不应输入密码。所有身份均为虚构人物：

| 角色 | handle | 显示名 | 用途 |
| --- | --- | --- | --- |
| 买家 | `demo_buyer` | 林澄 | 浏览、购物车、模拟结算、订单与评价 |
| 第二位买家 | `demo_buyer_two` | 许岚 | 验证真实拼团第二人加入 |
| 商家 | `demo_seller` | 周屿 | OpenShop 自营的库存、发货与售后处理 |

演示登录只会选择种子中的这些账号。不要录入真实姓名、地址、电话或任何凭据。

## 三种供给与功能范围

| 供给 | offer 类型 | 特点 | 可参与买法 |
| --- | --- | --- | --- |
| 官方自营 | `self` | 官方选品、单一履约责任、自营仓发货 | 普通购买 |
| 品牌旗舰 | `brand` | 品牌店铺直营，品牌仓发货与售后 | 普通购买 |
| 源头拼团 | `factory` | 工厂供给以真实拼团换取更低价格 | 普通购买或拼团 |

v0.1.0 候选范围如下。完成度以仓库门和自动化测试为准，本文不逐项宣称验收通过：

- **发现与比较**：首页、分类、搜索、筛选排序、商品详情、SKU、店铺信息、评价读取；同一商品比较不同供给的价格、配送与服务。
- **买方闭环**：游客购物车、登录与注册、地址簿、优惠券、服务端结算报价、模拟支付、订单与包裹、物流时间线、取消、确认收货和评价。
- **真实拼团**：邀请码参团；公开进度只统计已付款成员；未满团不显示成团成功，超时取消与退款按订单状态处理。
- **售后与商家台**：整单单店铺的模拟退款申请与商家审批；商家仅能操作自己店铺的库存、发货和售后，不能读取买家完整地址。
- **明确不做**：真实资金、真实物流、身份/KYC、风控、推荐模型训练和分布式事务；没有生产容量与并发承诺。

### 架构概览

```text
Browser (vanilla ES modules, same-origin fetch)
  -> apps/storefront/server.py        Python 标准库 ThreadingHTTPServer
       -> apps/storefront/routes/     auth/catalog, trade, seller JSON API
       -> packages/commerce/          领域逻辑、服务端计价、鉴权、种子
       -> SQLite (WAL)                --data-dir 下的单文件持久化
```

同一来源同时提供 `/api` JSON、静态 `web/` 资源与健康检查。价格、优惠、库存和订单可执行状态均由服务端重算/判定；写请求要求会话 Cookie 与 CSRF 头；响应带 CSP 等安全头，页面不使用内联脚本或第三方资源。详细分层、请求生命周期和安全边界见 [docs/architecture.md](docs/architecture.md)，冻结的接口与数据约定见 [docs/api-contract.md](docs/api-contract.md) 和 [docs/data-contract.md](docs/data-contract.md)。

支付接口只接受 `payment_method="demo"`，返回的元信息带 `demo:true`。任何“完成支付”都只代表模拟状态流转。

## 测试与仓库门

```bash
python3 -B -m unittest discover -s tests -v
python3 -B tools/check_repository.py --selftest
python3 -B tools/check_repository.py --check
```

- 单元/API 测试使用 Python 标准库 `unittest`，不安装测试框架。
- `--selftest` 在临时 fixture 上验证仓库门的正控与 19 个负控。
- `--check` 扫描外链与第三方资源、内联脚本、秘密形态、占位残留、JSON/JS/Python/HTML 语法、资产引用、本地文档链接、重复的 CSS `:root` token、CI 权限、部署模板和运行时第三方依赖。
- **未完成树上的门禁允许并且应该 FAIL。** 修复缺件，不要通过删除断言、加入白名单或放宽检查来求绿；原始输出应随发布证据保留。
- 当前门禁需要系统 `node` 做 JS 语法检查；若 Node 缺失会 fail closed。运行产品本身不需要 Node。

当前树中尚未合入的目录或功能会如实让门禁变红；这不是“已验证通过”。

## 目录

| 路径 | 用途 |
| --- | --- |
| `apps/storefront/` | HTTP 服务、路由注册与静态资源入口 |
| `packages/commerce/` | SQLite schema/种子、领域模型、鉴权、服务端报价 |
| `web/` | 无构建步骤的 ES Module 前端、本地 CSS 与原创 SVG |
| `data/` | 可审阅的虚构商品、店铺、人物与生成规则 |
| `tests/` | 标准库验收测试入口；发布时必须由 CI 实际发现并运行 |
| `docs/` | 产品、架构、API/数据契约、安装、开源与发布流程 |
| `tools/` | 仓库门与本地烟雾测试工具 |
| `deploy/` | 非 root systemd 模板、健康检查与安装/回滚样例 |
| `.github/workflows/` | 最小权限、无凭据、无网络安装的 CI |

目录表描述 v0.1.0 的集成目标布局。缺失文件不会被忽略：仓库门和 CI 会明确判失败。

## 参与与安全

贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。安全问题请不要开公开 issue，按 [SECURITY.md](SECURITY.md) 私下联系维护者。社区行为准则见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)；开源边界、素材来源和第三方依赖政策见 [docs/OPEN-SOURCE.md](docs/OPEN-SOURCE.md)；变更记录见 [CHANGELOG.md](CHANGELOG.md)。

生产安装、TLS 入口和发布动作均未在本仓库任务中执行。`deploy/` 只是需要单独授权的模板，不会自动触碰任何既有服务、DNS、证书或反向代理。

## 许可

代码与仓库内原创文档/素材以 [Apache License 2.0](LICENSE) 发布。OpenShop、开集品牌、商品、店铺、人物、评价和所有视觉素材均为项目原创或虚构，不代表任何真实主体，也不使用第三方平台的商标、截图或商品图片。

## English

OpenShop is an audit-friendly, self-hosted open-source marketplace reference implementation: three supply types, real group-buying state, a stdlib-only Python backend, SQLite, and vanilla ES modules with no build step or third-party runtime dependencies. Payments are simulated, all catalog identities are fictional, and this is not a production-scale or publicly deployed service. See [docs/INSTALL.md](docs/INSTALL.md) for local installation. Licensed under Apache-2.0.
