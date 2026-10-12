# Peer Review：核心 / 认证 / 目录 / 交易 API 安全审计

- 任务：task-10，独立对抗审查；base master a3c30a9（已合 catalog + trade）。
- Worktree：/data/dev/openshop-wt-peer-review；branch：seat/peer-review。
- 审查方式：只读产品代码；在 127.0.0.1:8882 启动真实 stdlib 服务，独立 SQLite data_dir，全部结论来自真实 HTTP 探针或明确代码坐标。
- 结论：发现 5 项 Low；38 个控制/攻击探针通过，无 High/Medium/Critical。探针套件**故意判红**以暴露 5 个开放缺陷，exit=1。没有修改任何产品代码，也没有声称修复。

原始总判定：

    AUDIT_FINDINGS verdict=FAIL ids=F-01,F-02,F-03,F-04,F-05 fail_probes=5
    PEER_REVIEW verdict=FAIL probes=44 controls=38 failures=5 findings=5

复跑命令（会新建隔离 data_dir；返回码 1 是本次审计的预期结果）：

    cd /data/dev/openshop-wt-peer-review
    sudo -n -u infra python3 -B tests/test_peer_review_probes.py

本次证据：

    /hf3fs-jd/data/tmp/openshop/peer-review/run-20261012T154549315268-1803317/
      probes.jsonl          44 条逐探针 JSON 结果
      server.log            服务端访问日志与 F-04/F-05 traceback
      data/                 本次隔离 SQLite

## 审查面与方法

- 认证：游客 session、demo 登录、注册、密码登录、登出、角色注入、错误一致性。
- 会话：Cookie 属性、CSRF 正确/缺失/错误 token、登录后 CSRF 旋转、登出撤销、会话标识轮换。
- 限速：每账号登录失败限速、跨 identifier 登录、注册成功/失败路径。
- 密码与凭据：PBKDF2 参数与盐、数据库明文检查、session token 静态哈希、响应与日志扫描。
- 授权：地址、购物车项、订单及全部订单动作、收藏、浏览事件、优惠券、评价的商品/订单归属。seller 未合入，见范围限制。
- 服务端权威：价格字段伪造、负数/零/超库存数量、SKU 与 offer 绑定、并发扣库存、优惠券门槛/归属/单次使用/并发、幂等键重放与冲突、非法状态跳转。
- 拼团：真实付款进度、未付款占位、满团竞态、跨团/跨 SKU 加入、团价与 SKU 增量。
- 输入面：SQL/LIKE 注入、路径穿越、静态文件、超限查询、NUL 路径、CORS、CSP、反射内容、敏感字段与日志。
- 对照判别力：两个独立游客 token 必须不同；同一 CSRF 控制既有有效 token 绿灯，也有错误/缺失 token 红灯；登录限速有第 9 次 429 的正向对照；密码哈希按已知 PBKDF2 参数重算。

静态服务、单购物车、幂等、团容量等并发断言均基于真实 HTTP；库存竞态只做两请求并发；测试 DB 是临时数据库，不属于产品代码。

## Findings（按优先级）

### F-01 登录/注册不在权限提升时轮换 session 标识（会话固定风险）— Low

- Owner：Lead（apps/storefront/routes/core.py、packages/commerce/auth.py）。
- 坐标：core.py:56-57、95-99、102-126、123-124；auth.py:136-152。
- 事实：同一游客 session 成功 demo/密码登录或注册后，Cookie token 原样保留，只轮换 CSRF。登录后仍使用登录前那份旧 Cookie 的请求，会被识别为已登录用户。探针以两个独立游客 token 不同作对照，证明比较器能发现变化；然后验证 demo 登录前后的 token 相同且旧 token 变为已登录。
- 前置条件：攻击者必须能把一个自己已知的游客 Cookie 放进受害者浏览器（独立会话固定/同站 cookie 注入原语）；因此列 Low，而不是把它说成无前置的远程接管。若前端或未来同站子服务提供写 Cookie 面，风险会提高。
- 探针与复现：在 /data/dev/openshop-wt-peer-review 下运行 sudo -n -u infra python3 -B tests/test_peer_review_probes.py（exit=1 为预期），查 probe auth_session_identifier_rotation；同一 run 中 control_two_guest_sessions_have_distinct_tokens、csrf_rotation_on_authenticated_session 为绿，说明不是比较器失效。
- 实际输出：

    PROBE auth_session_identifier_rotation verdict=FAIL kind=finding finding=F-01 severity=Low
    detail=demo_login=200 token_rotated=False csrf_rotated=True
    old_cookie_before_is_guest=True old_cookie_after_is_guest=False
    old_cookie_authenticated_as=demo_buyer

- 最小修复建议：在注册、密码登录和 demo 登录的权限绑定点生成新 session id/token（复用已验证的用户、CSRF、购物车合并结果），撤销旧 session 并 Set-Cookie 新 token。不要只更新 user_id。

### F-02 成功注册会清空 IP 注册限速桶，账号创建可无限绕过 — Low

- Owner：Lead（core.py:37、60，auth.py:155-168）。
- 事实：每次成功注册都执行 reset_rate_limit(register:<ip>)。因此“同一 IP 10 分钟最多 5 支注册”的限制只覆盖连续失败，不覆盖账号创建本身。探针在本窗口先成功创建 3 个测试账号，再连续发起 3 次有效注册，3 次全为 200，同窗口成功注册总数为 6，超过 max_attempts=5。随后单独做 5 次失败 + 第 6 次有效的对照，得到 429，证明限速器本身存在，只是成功路径重置了它。
- 影响：可批量注册账号，配合自动领券、订单/库存占用或后续滥用；属反自动化控制失效，不构成认证绕过。Severity Low。
- 探针与复现：在 /data/dev/openshop-wt-peer-review 下运行 sudo -n -u infra python3 -B tests/test_peer_review_probes.py；查 rate_register_successful_signup_bypass 与对照 rate_register_five_failure_control。
- 实际输出：

    PROBE rate_register_successful_signup_bypass verdict=FAIL kind=finding finding=F-02 severity=Low
    detail=successful_registrations_same_ip_window=6 extra_statuses=[200, 200, 200]
    successful signup calls reset bucket at core.py:60 before the next call
    PROBE rate_register_five_failure_control verdict=PASS
    detail=first5=[400, 400, 400, 400, 400] sixth=429/RATE_LIMITED

- 最小修复建议：注册限速不要把成功注册从桶里清掉；成功和失败都计数，按 IP + 时间窗限制账号创建，并考虑按邮箱/设备补充限速与审计。登录成功清桶可以保留，注册成功不应等同。

### F-03 登录限速仅按 identifier 计键，无 IP 级总闸，且每次尝试执行 PBKDF2 — Low

- Owner：Lead（core.py:74、auth.py:155-168、auth.py:27-29）。
- 事实：同一账号连错 8 次后第 9 次正确登录得到 429，说明 per-identifier 限速有效；但从同一 IP 轮换 10 个不存在的 identifier，连续 10 次全部 401，没有任何 429 或 IP 总闸。每次不存在的账号仍执行一次 210k PBKDF2 dummy hash，因此该路径允许从单一来源进行跨账号口令喷洒，并持续消耗 CPU/创建会话与内存限速键。Severity Low（非认证绕过，反自动化/可用性防御缺口）。
- 另一运维风险（本次未做反向代理实测）：ctx.client_ip 取自直连 socket；文档部署形态是 Caddy 反代。若代理不传真实客户端 IP，所有用户会共享一个注册/登录 IP 桶；修复时须明确可信代理链，而不是直接信任任意 X-Forwarded-For。
- 探针与复现：在 /data/dev/openshop-wt-peer-review 下运行 sudo -n -u infra python3 -B tests/test_peer_review_probes.py；查 auth_login_identifier_rotation_spray 与对照 rate_login_per_identifier_failure_control。套件中实际 attempts=10、distinct_identifiers=10、rate_limited=0。
- 实际输出：

    PROBE auth_login_identifier_rotation_spray verdict=FAIL kind=finding finding=F-03 severity=Low
    detail=attempts=10 distinct_identifiers=10 statuses=[401, 401, 401, 401, 401, 401, 401, 401, 401, 401]
    codes=['INVALID_CREDENTIALS'] rate_limited=0
    PROBE rate_login_per_identifier_failure_control verdict=PASS
    detail=first8=[401,...] ninth_correct=429/RATE_LIMITED

- 最小修复建议：在 per-identifier 之外增加 per-IP/全局预算计数器（滑窗+定期清理旧桶），对不存在账号的登录尝试也计入；对高失败率或高基数标识符做退避/挑战。反代部署必须由受信代理链提供客户端 IP；不要无条件信任客户端头。

### F-04 超过 100 个 query 字段时返回 500，异常栈写入服务端日志 — Low

- Owner：Lead（packages/commerce/web.py、apps/storefront/server.py）。
- 坐标：web.py:77（parse_qs(max_num_fields=100)）；server.py:256-258（宽泛 Exception 捕获并返回 500）。
- 事实：发送 101 个不同 query 字段时，urllib.parse 抛 ValueError('Max number of fields exceeded')，API 返回 500 INTERNAL_ERROR；服务仍存活。响应是通用错误，不泄漏 traceback/SQL；但这是客户端可重复触发的 5xx 和 ERROR 日志放大面，且应属于 400 输入拒绝。
- 探针与复现：在 /data/dev/openshop-wt-peer-review 下运行 sudo -n -u infra python3 -B tests/test_peer_review_probes.py；查 input_query_field_limit；套件另跑 input_query_flood_server_alive=PASS。
- 实际输出：

    PROBE input_query_field_limit verdict=FAIL kind=finding finding=F-04 severity=Low
    detail=http=500 code=INTERNAL_ERROR expected_4xx_got_server_error=True
    server.log:
    ERROR Unhandled API error ... path=/api/products
    ValueError: Max number of fields exceeded

- 最小修复建议：在 RequestContext 构造阶段捕获 parse_qs 的 ValueError（或其他解析异常）并映射为 400 VALIDATION_ERROR / 字段错误；不要让它落入 500 通用 catch。

### F-05 静态路径含 NUL 字节时连接被无响应关闭并记录 traceback — Low

- Owner：Lead（apps/storefront/server.py static-file handler）。
- 坐标：server.py:264-297；实际 traceback 落在 server.py:282 的 (REPO_ROOT / rel).resolve()。
- 事实：GET /web/%00 触发 Path.resolve() 的 ValueError: embedded null byte。该异常在 API try/except 之外，客户端得到 RemoteDisconnected（无 HTTP 状态、无安全头/错误 JSON），服务进程仍存活。路径穿越探针本身全部 404，未读到 docs/data 文件；本项是输入健壮性与错误处理后置问题，不是文件穿越。
- 探针与复现：在 /data/dev/openshop-wt-peer-review 下运行 sudo -n -u infra python3 -B tests/test_peer_review_probes.py；查 input_nul_byte_static_path；同时套件验证 health_after=True 和 5 条真实穿越请求均 404。
- 实际输出：

    PROBE input_nul_byte_static_path verdict=FAIL kind=finding finding=F-05 severity=Low
    detail=http=None transport_error=RemoteDisconnected health_after=True
    server.log:
    Exception occurred during processing of request from ('127.0.0.1', ...)
    ...
    ValueError: embedded null byte

- 最小修复建议：在静态 handler 入口拒绝 NUL/控制字符并返回 400/404；将 path resolve 包在明确的异常边界内（ValueError/OSError -> 404），最好复用统一 JSON/纯文本错误响应和现有安全头。

## 已验证控制（38 个控制探针 + 1 个范围探针）

下列每组结论均至少有一个攻击探针；完整原始行在 probes.jsonl。

- Cookie：cookie_httponly_samesite_path PASS（HttpOnly、SameSite=Lax、Path=/、Max-Age=2592000、无 Domain）；cookie_secure_behind_https_proxy PASS（带 X-Forwarded-Proto: https 时 Secure 存在）。纯 HTTP 测试中 Secure 不存在，HSTS 不在 app 头里；需要 TLS/反代配置补齐，未列为本代码缺陷。
- CSRF：csrf_known_control_positive_and_negative PASS（有效 200；缺失/错误 403 CSRF_FAILED）；csrf_rotation_on_authenticated_session PASS（登录后旧 token 403，新 token 200）。这是正/负对照。
- 登出：session_logout_revokes_old_cookie PASS（旧 Cookie 读订单 401 AUTH_REQUIRED；登出响应为新游客）。
- 认证：authz_registration_ignores_injected_role PASS（请求 role=seller/shop_id=1，落库 buyer/null）；auth_login_uniform_failure PASS（存在与不存在账号同为 401 INVALID_CREDENTIALS、同文案）。
- 密码与令牌：auth_password_pbkdf2_at_rest PASS（PBKDF2-HMAC-SHA256 210k、32 hex 盐、逐用户不同、存储非明文、重算匹配）；auth_session_token_hashed_at_rest PASS（session 表只存 SHA-256(token)，注册响应无 password_hash/salt/token_hash/session_id）；server_log_no_credentials_or_tokens PASS（本次扫描 35 个测试密钥值，0 泄漏）。
- IDOR/跨会话：authz_addresses_owner_only、authz_cart_items_owner_only、authz_orders_and_actions_owner_only、authz_favorites_session_scope、authz_browse_events_session_scope、authz_coupon_owner_only、authz_reviews_order_and_item_scope 全部 PASS。跨用户订单读/支付/取消/退款/评价均 404；跨用户地址 PATCH/DELETE 404；账单地址不属于自己时下单 400 且无订单；评价需订单 owner 且 item 必须属于该订单（他项 400）。
- seller API：scope_seller_api_not_merged = 404，seller.py 仍为 stub；商家越权、发货、售后 approve/reject 未在本轮范围内。
- 服务端价格权威：server_price_authority PASS（注入 total/subtotal/unit/line/discount 非法字段后，购物车单价=offer+SKU delta，订单总额=quote=DB=199800，库存只扣 quantity=2）；server_quantity_and_sku_rejection PASS（负/零 quantity 400，over-stock 409 STOCK_CHANGED，错 SKU 404，负 PATCH 400）。
- 库存竞态：stock_concurrent_no_oversell PASS。临时 fixture 将 offer 8 stock 置 1（原 seed stock 72），A/B 同时下单，结果 [409 STOCK_CHANGED, 200]，最终 stock=0，只有 1 个订单。
- 幂等：idempotency_replay_conflict_and_stock_once PASS。同 C cookie 并发两次同 key 得 200/200 同一 order_no、DB 仅 1 行、库存只减 1；改 note 重用 key 得 409 IDEMPOTENCY_CONFLICT。
- 优惠券：coupon_threshold_enforced PASS（subtotal 21900 < 29900 时 quote 只给 coupon_error，create 422 且无订单）；coupon_single_use_sequential PASS（首次 create 200 后状态 USED，再次 quote/create 被拒）；coupon_concurrent_single_use PASS（并发两单只有一单 200，另一单 422 CART_EMPTY，券只绑定一单，库存只减 1）；authz_coupon_owner_only PASS（A 的 WEEKEND30 对 B 不生效，创建 422 且无订单）。
- 非法状态：order_state_machine_rejects_illegal_transitions PASS（未支付 confirm/refund 409 ORDER_STATE；非 demo 支付 400；支付后重复支付/取消 409；未发货 confirm 409；未完成评价 409；最终仍 PAID）。
- 拼团：group_capacity_race_and_paid_progress PASS（初始 joined=0；A 支付后 1；B/C 同时创建，1 个 200、1 个 409 GROUP_FULL；未支付成员不增长公开人数；最终 FULL/joined=2/paid_members=2，两单均 PAID，实付团价与 SKU 增量匹配）。group_offer_sku_and_single_cart_binding PASS（重复参团 409 GROUP_UNAVAILABLE、错 SKU 409、另一团 code 混入购物车 422 MIXED_BUY_MODE）。
- SQL/LIKE：like_positive_reference PASS；like_special_characters_escaped PASS（%、_、SQLi payload 均不放大结果）；sql_injection_surfaces_rejected PASS（login 401、group/order/coupon 路径不执行注入，响应无 SQL/traceback）。
- 路径与头：static_traversal_blocked PASS（5 条穿越路径 404，正常 SVG 200）；security_headers_csp_baseline PASS（CSP script-src self、object-src none、frame-ancestors none、XFO DENY、nosniff；HSTS 缺失由部署层承担）；cors_no_cross_origin_grant PASS（GET 无 ACAO；OPTIONS 405 无 ACAO）；reflected_xss_api_json_only PASS（/search 不反射 payload；/api/products 以 application/json 返回）。
- 输入服务存活：input_query_flood_server_alive=PASS；NUL 路径后 health_after=True。审计不掩盖 F-04/F-05 的 5xx/断连。

## 未覆盖与限制

- seller API 未合入（apps/storefront/routes/seller.py 为 register-only stub）。商家资源、库存修改、发货、售后审核及其跨店 IDOR 未测试。
- 前端页面仍多为 stub，且本次不做浏览器执行；存储型/反射型 XSS 的最终渲染安全、CSP 在真实 DOM 上的行为未覆盖。API 只保证 JSON 与安全头基础面。
- 未验证真实 TLS、Caddy 反代 client IP、HSTS、生产 OPENSHOP_SECURE_COOKIES；Secure 仅通过 X-Forwarded-Proto 模拟验证。
- 并发只做每个场景两个请求，没有压测、进程重启、SQLite 故障注入或长期限速内存增长测试。
- 未覆盖拼团过期清理/自动取消售后审批路径（seller 未合入）与真实支付/物流。
- 反代下 per-IP 限速桶的耦合是代码+部署风险，未在真正的 Caddy 链路复现。
- 本报告是独立审查证据，不是修复记录；未修改 apps/packages/contracts。

## Owner summary

| Finding | Severity | Minimal owner |
| --- | --- | --- |
| F-01 session 不轮换 | Low | Lead / auth-core |
| F-02 注册成功重置限速 | Low | Lead / auth-core |
| F-03 登录无 IP 总闸 | Low | Lead / auth-core |
| F-04 query-field flood 500 | Low | Lead / web+server |
| F-05 NUL static path 断连 | Low | Lead / server static handler |
