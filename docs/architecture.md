# 架构

## 总览

```
Browser (vanilla ES modules)
   |  same-origin JSON + HttpOnly session cookie + CSRF header
   v
apps/storefront/server.py  ---- static \web
   |  route modules: auth_catalog / trade / seller
   v
packages/commerce/*
   |  stdlib only
   v
SQLite (WAL, foreign keys, transactional stock)
```

- 单进程、线程化标准库 HTTP 服务。适合本仓库目标：可读、可演示、容易托底；不是生产级水平扩展架构。
- SQLite 每次请求独立连接，开启 WAL 与 busy timeout。库存与优惠券使用数据库事务和条件更新防超卖/双花。
- 浏览器不做价格权威：只提交 `offer_id`、`sku_id`、数量、地址与优惠码，价格由服务端重算。
- 前端无框架、无打包步骤。页面模块通过原生 ES modules 和路由挂载。

## 代码所有权

| 区域 | 目录 | 责任 |
| --- | --- | --- |
| 服务外壳 | `apps/storefront/server.py`, `packages/commerce/web.py` | HTTP、路由、静态、统一错误、安全头 |
| 数据底座 | `packages/commerce/db.py`, `schema.sql`, `seed.py` | SQLite schema、种子、迁移 |
| 共享领域 | `packages/commerce/models.py` | 序列化、价格、库存和查询共用逻辑 |
| 账户/目录 API | `apps/storefront/routes/auth_catalog.py` | 会话、认证、地址、收藏、目录、搜索、评价读取 |
| 交易 API | `apps/storefront/routes/trade.py` | 购物车、优惠券、结算、订单、支付、售后 |
| 商家 API | `apps/storefront/routes/seller.py` | 商家概览、库存、发货、售后处理 |
| 前端核心 | `web/js/app.js`, `web/js/core/`, `web/js/components/` | 路由、状态、API、布局与共享组件 |
| 前台页面 | `web/js/pages/` | 发现、详情、购物流程、账户、商家台 |
| 视觉与内容 | `web/css/`, `web/assets/`, `data/catalog.json` | 设计系统、原创插画、虚构商品 |
| 验证 | `tests/`, `tools/check_repository.py` | 单测、API/安全/浏览器门、敏感扫描 |

## 请求生命周期

1. 新浏览器 `GET /api/session` 获得 HttpOnly 会话 Cookie 与 `csrf`。
2. 任何改变状态的方法必须同时携带 Cookie 与 `X-OpenShop-CSRF`。缺失/不匹配 -> 403。
3. 路由层做认证与角色校验；领域层再次验证资源归属。
4. 交易事务内重读价格、库存、券状态；条件扣减成功后写订单和事件。
5. 所有错误返回固定 JSON 结构；页面不能把网络错误当成功。
6. 服务返回 CSP、`X-Content-Type-Options`、`Referrer-Policy`、`Permissions-Policy`。禁止 `unsafe-eval`，不加载第三方资源。

## 存储

- 默认数据目录由 `--data-dir` 指定；本地开发使用 3FS tmp，部署由 systemd 指定外置目录。
- 启动时对空 DB 应用 `schema.sql` 并从 `data/catalog.json` 幂等播种。
- 禁止把运行 DB、日志、session 密钥、截图临时件提交进 Git。
- 时间统一存储 UTC ISO-8601；展示时转为 Asia/Shanghai。

## 安全边界（与测试对应）

- PBKDF2-HMAC-SHA256 密码摘要 + 每用户随机盐；演示登录不暴露密码。
- 登录/注册限速；会话 Cookie HttpOnly + SameSite=Lax；登出撤销服务端会话。
- 全部写操作 CSRF 校验；不允许 CORS 任意源；不反射任意 HTML。
- 商家资源查询必须带 `shop_id` 条件；订单属于当前用户。
- 前端文本渲染使用 DOM textContent 或统一 `escapeHtml`；商品内容来自种子也可能含用户评价。
- 优惠券使用条件更新防重复使用；库存使用原子条件扣减 + 回滚补回。
- 不实现真实支付，接口明确 `mode=DEMO` 与 `simulated=true`。

## 发布形态

开发通过 systemd 常驻服务 + Caddy/反代终结 TLS。发布固定提交、固定数据目录、只切一个 unit 的 `WorkingDirectory`；发布前经过全部测试与浏览器检查，失败自动回旧版本。真实公网发布必须由 Lead 在授权边界内单独执行并留现读证据。
