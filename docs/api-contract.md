# OpenShop HTTP API 契约 v1

状态：**冻结契约**。主代理拥有本文件；任一席需要改契约必须先在共享任务板说明并由 Lead 修改本文件。前端不得猜测字段；后端不得静默改字段名。

## 0. 总则

- 所有路径同源，前缀 `/api`；请求/响应 UTF-8 JSON。
- 成功统一 `200 {"data": ..., "meta": {"request_id": "...", "server_time": "...", "currency": "CNY", "demo": true}}`。
- 失败统一错误 status + `{"error": {"code": "...", "message": "...", "fields": {...}?}, "meta": {...}}`。
  - 前端 API 包装器只返回 `data`，错误抛 `ApiError(status, code, message, fields)`。
- 金额全部为 **integer cents**，字段名以 `_cents` 结尾；数量为正整数。
- 时间 ISO-8601 UTC（例 `2026-10-12T14:00:00Z`）；UI 显示 Asia/Shanghai。
- ID：用户/商品/offer/sku/地址/购物项/订单均为整数；订单对外另有 `order_no`；拼团用字符串 `code`。
- 分页：请求 `page`（从 1 起，默认 1）、`page_size`（默认 12，最大 24）。响应 `{"items":[],"total":n,"page":1,"page_size":12,"pages":n}`。
- 布尔筛选接受 `1/0/true/false`；空字符串按未提供。
- 所有变更状态的方法（POST/PATCH/DELETE）必须是 `application/json`，且带 `X-OpenShop-CSRF`，值来自最近一次 `/api/session`、登录或注册响应；不匹配返回 403 `CSRF_FAILED`。不信任 Origin 作为唯一 CSRF 防御。
- `GET /api/session` 可创建游客会话（HttpOnly Cookie）。**前端启动必须先取得会话再发写请求**。
- 游客可浏览、收藏、加购；结算/地址/订单需登录。商家接口需 seller 角色且只能操作自己的 shop。
- 所有校验失败返回：
  - 400 `VALIDATION_ERROR`（字段错误放 `error.fields`）
  - 401 `AUTH_REQUIRED`
  - 403 `FORBIDDEN` / `CSRF_FAILED`
  - 404 `NOT_FOUND`
  - 409 `STOCK_CHANGED` / `GROUP_FULL` / `GROUP_UNAVAILABLE` / `ORDER_STATE` / `DUPLICATE_REVIEW`
  - 422 `COUPON_INVALID` / `MIXED_BUY_MODE` / `CART_EMPTY`
  - 429 `RATE_LIMITED`
- 登录/注册/演示入口需已存在游客会话。任何接口不返回密码、摘要、Cookie、内部 session id。
- API 明确演示模式：`meta.demo=true`；支付接口仅接受 `payment_method="demo"`。前端必须显示“模拟支付”而不是“已支付真实金额”。

## 1. 公共形状

### User
```json
{"id":2,"handle":"demo_buyer","display_name":"林澄","email":"buyer@openshop.demo","role":"buyer",
 "avatar":"/web/assets/brand/avatar-cheng.svg","member_since":"2024-03-18",
 "shop_id":null,"shop":null}
```
Seller 的 `shop` 为 `{"id":1,"slug":"openshop-self","name":"OpenShop 自营","kind":"self","verified":true}`。

### Shop
```json
{"id":1,"slug":"openshop-self","name":"OpenShop 自营","kind":"self",
 "tagline":"官方选品，仓配直达","rating":4.9,"followers_count":182300,
 "since":"2021","verified":true,"response_rate":"99.6%","ship_from":"上海 · 嘉定"}
```

### Category
```json
{"slug":"digital","name":"数码影音","subtitle":"把声音与光带在身边","icon":"headphones","count":3}
```

### Card（首页、搜索、关联推荐、收藏、拼团页均使用）
```json
{"id":3,"slug":"cloud-sofa","title":"云朵模块沙发","subtitle":"可组合的松弛角落",
 "brand":"山雾 SHANWU","category":{"slug":"home","name":"居家生活"},
 "cover":"/web/assets/products/cloud-sofa.svg","cover_alt":"米白色模块沙发与落地灯的场景插画",
 "price_cents":359900,"market_price_cents":429900,"discount_percent":16,
 "offer_count":2,"offer_kinds":["self","brand"],
 "rating":4.8,"review_count":326,"sold_count":2418,
 "badges":["原创设计","送装一体"],"services":["正品保障","7天无理由"],
 "delivery_label":"上海仓 · 预计 2 天送达","free_shipping":true,
 "group":{"offer_id":5,"size":2,"price_cents":319900},
 "saved_cents":70000}
```
- `price_cents` = 当前所有可售 offer 的最低有效单价；`market_price_cents` 为对应划线价；`group` 为可选。
- `discount_percent` 为向下取整的百分比（0-99）。
- 商品无货时仍可返回，但详情按钮禁用；MVP 种子默认有货。
- `saved_cents = market_price_cents - price_cents`（负数则 0）。

### ProductDetail
Card 基础字段外加：
```json
{"description":"……","story":"……",
 "images":[{"src":"/web/assets/products/cloud-sofa.svg","alt":"……"},{"src":"…","alt":"…"}],
 "tags":["模块化","小户型友好"],
 "highlights":[{"title":"一坐就陷进去的云感","body":"高回弹海绵与羽绒混合填充。"}],
 "specs":[{"title":"尺寸与材质","items":[{"label":"面料","value":"混纺圈绒"},{"label":"尺寸","value":"双人位 180 × 95 cm"}]}],
 "detail_sections":[{"title":"把客厅留给松弛","body":"……"}],
 "faq":[{"q":"是否支持拆洗？","a":"……"}],
 "offers":[
   {"id":5,"kind":"self","kind_label":"官方自营","name":"双人位 · 送装一体",
    "shop":{Shop},"price_cents":359900,"market_price_cents":429900,"shipping_fee_cents":0,
    "delivery":{"eta_days":2,"label":"预计后天送达","note":"上海仓直发"},
    "services":["正品保障","7天无理由","送装一体"],"stock":26,
    "group":{"size":2,"price_cents":319900,"stock":12},
    "is_primary":true}
 ],
 "skus":[{"id":9,"name":"雾灰 / 双人位","options":{"颜色":"雾灰","规格":"双人位"},"price_delta_cents":0}],
 "reviews":{"summary":{"rating":4.8,"count":326,
   "distribution":[{"stars":5,"count":250,"percent":77},{"stars":4,"count":51,"percent":16},{"stars":3,"count":18,"percent":6},{"stars":2,"count":5,"percent":2},{"stars":1,"count":2,"percent":1}],
   "tags":[{"label":"坐感舒服","count":86},{"label":"颜色耐看","count":54}]},
   "items":[Review]},
 "related":[Card],
 "created_at":"2026-09-01T00:00:00Z"}
```
- 价格计算：普通购买的 SKU 单价 = `offer.price_cents + sku.price_delta_cents`；拼团 = `offer.group.price_cents + sku.price_delta_cents`。库存是 **offer 级**。
- 前端的划线价、优惠价只展示；服务端创建订单时重算。

### Review
```json
{"id":12,"author":"林**","rating":5,"title":"比照片更温柔","body":"……",
 "tags":["坐感舒服"],"verified":true,"helpful_count":18,
 "sku_label":"雾灰 / 双人位","created_at":"2026-10-01T09:20:00Z",
 "mine":false}
```

### 购物车 CartResponse
```json
{"items":[
  {"id":21,"offer_id":5,"sku_id":9,"quantity":1,"selected":true,"buy_mode":"standard",
   "group_code":null,"quantity_limit":26,"line_price_cents":359900,
   "product":{"id":3,"slug":"cloud-sofa","title":"云朵模块沙发","brand":"山雾 SHANWU",
     "cover":"/web/assets/products/cloud-sofa.svg","cover_alt":"……"},
   "sku":{"id":9,"name":"雾灰 / 双人位","options":{"颜色":"雾灰","规格":"双人位"}},
   "offer":{"id":5,"kind":"self","kind_label":"官方自营","name":"双人位 · 送装一体",
     "unit_price_cents":359900,"shipping_fee_cents":0,
     "delivery_label":"预计后天送达","stock":26,
     "group":{"size":2,"price_cents":319900,"stock":12},
     "shop":{"id":1,"slug":"openshop-self","name":"OpenShop 自营","kind":"self","verified":true}},
   "warnings":[]}
 ],
 "shops":[{"shop":{"id":1,"slug":"openshop-self","name":"OpenShop 自营","kind":"self","verified":true},
           "item_ids":[21],"selected_subtotal_cents":359900,"delivery_note":"官方自营 · 由上海仓发货"}],
 "summary":{"item_count":1,"selected_count":1,"all_selected":true,
   "subtotal_cents":359900,"market_subtotal_cents":429900,"savings_cents":70000,
   "shipping_cents":0,"discount_cents":0,"total_cents":359900,
   "has_group":false,"buy_mode":"standard"},
 "notices":["优惠券与运费在结算页计算。"]}
```
- 游客购物车按 session 归属；登录时合并到用户购物车（数量合并，不相容则分组规则优先）。
- 同一 `offer+sku+buy_mode+group_code` 合并数量；拼团团购项数量固定 1。
- 购物车不允许混放普通项与 group 项（加入时若冲突返回 422 `MIXED_BUY_MODE`，前端提示“拼团商品需单独结算”）。

### 报价 Quote
`POST /api/checkout/quote` body：
```json
{"coupon_code":"WELCOME20","shipping_method":"standard"}
```
（必须登录且购物车有已选项；group 项自动按其 group 价报价，group 需要有效邀请码的情况在订单创建时验证。若要从链接参团，加入购物车时已存 group_code。）
返回：
```json
{"subtotal_cents":359900,"market_subtotal_cents":429900,"savings_cents":70000,
 "shipping_cents":0,"discount_cents":20000,"total_cents":339900,
 "selected_count":1,"shipping_method":{"code":"standard","label":"标准快递","fee_cents":0,"detail":"预计后天送达"},
 "shipping_options":[{"code":"standard","label":"标准快递","fee_cents":0,"detail":"预计后天送达"},
                    {"code":"express","label":"顺丰加急","fee_cents":1200,"detail":"预计明天送达"}],
 "coupon":{"code":"WELCOME20","title":"新人见面礼","discount_cents":20000} | null,
 "coupon_error":null, "has_group":false,
 "address_required":true}
```
- 不在购物车的项不能参与报价。优惠以服务端判定，失败只让 `coupon=null` 并给 `coupon_error`（用户仍可换券/去结算）；不会静默给别人的券或过期券。
- group 项禁止 express，强制 standard；`has_group=true` 时仅允许单选一个 group item。

### OrderSummary
```json
{"order_no":"OS20261012150421AB12","status":"PENDING_PAYMENT","status_label":"待付款",
 "created_at":"…","paid_at":null,"total_cents":339900,
 "preview_items":[{"title":"云朵模块沙发","cover":"…","sku_label":"雾灰 / 双人位","quantity":1}],
 "item_count":1,"shop_count":1,
 "group":{"code":"G7K2M8","status":"FORMING","required_size":2,"joined_count":1,"expires_at":"…"} | null,
 "actions":{"pay":true,"cancel":true,"confirm":false,"review":false,"refund":false,"view_group":false}}
```

### OrderDetail
OrderSummary 外加：
```json
{"subtotal_cents":359900,"shipping_cents":0,"discount_cents":20000,
 "market_savings_cents":70000,
 "address":{"id":1,"receiver":"林澄","phone":"13800000002","province":"上海市","city":"上海市","district":"徐汇区","detail":"……","label":"家"},
 "shipping_method":"standard","payment_method":"demo","note":"",
 "paid_at":"…","shipped_at":null,"completed_at":null,"cancelled_at":null,
 "refunds":[{"id":1,"reason":"…","kind":"refund_only","amount_cents":339900,"status":"REQUESTED","created_at":"…"}]}
```
并含：
```json
{"packages":[{"id":4,"shop":{"id":1,"name":"OpenShop 自营","kind":"self"},"status":"PENDING","status_label":"待发货",
  "carrier":null,"tracking_no":null,"shipped_at":null,
  "items":[{"id":31,"product_id":3,"slug":"cloud-sofa","title":"云朵模块沙发","cover":"…","sku_label":"雾灰 / 双人位",
    "unit_price_cents":359900,"quantity":1,"offer_kind":"self","services":["正品保障"],
    "reviewable":false,"reviewed":false}],
  "events":[{"label":"订单已创建","detail":"等待付款","created_at":"…"}]}],
 "timeline":[{"label":"订单已创建","detail":"等待付款","created_at":"…"}],
 "actions":{"pay":true,"cancel":true,"confirm":false,"review":false,"refund":false,"view_group":false}}
```
- 状态：`PENDING_PAYMENT`、`GROUP_FORMING`（已付、等成团）、`PAID`（待发货）、`SHIPPED`、`COMPLETED`、`CANCELLED`、`REFUNDING`、`REFUNDED`。
- 一个订单按 shop 拆 packages。seller 只能看/操作自己的 package。
- `actions` 全由服务端计算，前端不得自行推断可点状态。

## 2. 端点

### 2.1 会话与认证（backend-catalog 席）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/session` | 无会话则创建游客 session 并 Set-Cookie；返回 `{user,csrf,cart_count,favorite_count,is_guest}` |
| POST | `/api/auth/register` | `{display_name,email,password}`；8-72 密码、合法邮箱；自动登录；同 session 购物车合并 |
| POST | `/api/auth/login` | `{identifier,password}`；identifier 支持 email 或 handle；错误统一不泄露账号是否存在 |
| POST | `/api/auth/demo` | `{role:"buyer"|"seller"}`；仅从种子 demo 用户选择，公开演示入口；返回 session 形状 |
| POST | `/api/auth/logout` | 撤销当前 session，清 Cookie；创建新游客 session 并返回其 csrf/session 形状 |

SessionResponse：
```json
{"user":User|null,"csrf":"32+字符","cart_count":1,"favorite_count":0,"is_guest":false}
```

### 2.2 目录（backend-catalog 席）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/home` | 首页聚合；无需登录，带游客个性化 |
| GET | `/api/categories` | Category[] |
| GET | `/api/products` | 列表 + facets |
| GET | `/api/products/{slug}` | ProductDetail |
| GET | `/api/products/{slug}/reviews` | Review 分页 |
| GET | `/api/search/suggest?q=` | `{suggestions:[{text,kind}],products:[{slug,title,cover}],hot:[string]}` |
| POST | `/api/events/view` | `{product_slug}` 记录浏览；未登录用 session owner |
| GET | `/api/favorites` | 需登录；`{items:[Card],total}` |
| POST | `/api/favorites/{slug}/toggle` | 需登录；`{favorited:true,total:3}` |

`GET /api/home`：
```json
{"categories":[Category],"stats":{"products":18,"shops":4,"reviews":86},
 "hero":{"product_slug":"cloud-sofa","image":"/web/assets/products/cloud-sofa.svg",
   "eyebrow":"本周编辑选物","title":"把秋天，搬进客厅","body":"给日常留一点柔软的空白。",
   "price_cents":359900,"badge":"送装一体"},
 "hero_side":[Card,Card],
 "shelves":[{"key":"editors","title":"编辑的客厅","subtitle":"留白、木色和一点暖光","href":"/search?category=home","items":[Card]}],
 "deals":[Card],
 "group_picks":[Card],
 "new_arrivals":[Card],
 "recently_viewed":[Card],
 "recommended":[Card],
 "trust":[{"icon":"shield","title":"正品可追溯","detail":"每件商品带店铺与批次信息"}]
}
```
- `hero` 可由种子第一件 featured=true 商品生成；`shelves` 2-3 组。
- 响应元信息统一外层，下面 data 里的字段不再出现 meta。
- 首页推荐若未登录基于 session 浏览记录，登录后同时参考收藏与已完成订单；没有信号时按 featured/rating。

`GET /api/products` 参数：`q`、`category`、`kind`（self|brand|factory）、`sort`（featured|newest|price_asc|price_desc|rating|sales）、`min_price_cents`、`max_price_cents`、`min_rating`、`page`、`page_size`。
返回：
```json
{"items":[Card],"total":18,"page":1,"page_size":12,"pages":2,
 "query":"沙发","sort":"featured",
 "facets":{"categories":[{"slug":"home","name":"居家生活","count":12}],
   "kinds":[{"kind":"self","count":14},{"kind":"brand","count":8},{"kind":"factory","count":5}],
   "price_min_cents":4900,"price_max_cents":499900}}
```
- `kind` 表示存在该类型可售 offer。
- q 按标题/副标题/品牌/tags/分类搜索，大小写、空白安全；SQL 参数化，LIKE 特殊字符转义。
- 空搜索也应可用，不注入。

Reviews 查询参数：`rating` 1-5、`sort`（recent|helpful|rating_desc）、`page`、`page_size`。返回：
```json
{"summary":{...},"items":[Review],"total":326,"page":1,"page_size":10,"pages":33,
 "facets":{"tags":[{"label":"坐感舒服","count":86}]}}
```

### 2.3 地址与优惠券（地址 backend-catalog，优惠券 backend-trade）

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/addresses` | user | `{items:[Address],default_id}` |
| POST | `/api/addresses` | user | AddressInput -> Address |
| PATCH | `/api/addresses/{id}` | owner | 局部更新；设为默认时取消其它默认 |
| DELETE | `/api/addresses/{id}` | owner | `{deleted:true}`；已用于历史订单的快照不受影响 |
| GET | `/api/coupons` | user | `{available:[CouponDef],mine:[UserCoupon]}` |
| POST | `/api/coupons/{code}/claim` | user | `{coupon:UserCoupon}`；已领/发完/过期明确报错 |

Address：
```json
{"id":1,"label":"家","receiver":"林澄","phone":"13800000002",
 "province":"上海市","city":"上海市","district":"徐汇区","detail":"漕溪北路 88 号 6 栋 502",
 "is_default":true}
```
AddressInput 必须含 receiver(2-20)、phone(11 位大陆手机号，演示数据)、province/city/district/detail(5-120 字)、label(可选 ≤10)、is_default(bool)。
CouponDef：
```json
{"code":"WELCOME20","title":"新人见面礼","description":"满 199 减 20",
 "threshold_cents":19900,"discount_cents":20000,"scope":"all","expires_at":null,
 "claimed":true,"claimable":false,"remaining":null}
```
- 未领取券不知道领取后到期日，`expires_at` 固定返回 `null`（不得伪造未来日期）；已领取券以 user_coupon.expires_at 填值。接口可只返回 `mine` 中有效的 AVAILABLE/USED 记录。
UserCoupon 额外 `{"user_coupon_id":7,"status":"AVAILABLE","used_at":null}`。

### 2.4 购物车与交易（backend-trade 席）

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/cart` | guest | CartResponse |
| POST | `/api/cart/items` | guest | `{offer_id,sku_id,quantity=1,buy_mode:"standard"|"group",group_code:null|string}` -> CartResponse |
| PATCH | `/api/cart/items/{id}` | owner | `{quantity?,selected?}` -> CartResponse |
| DELETE | `/api/cart/items/{id}` | owner | -> CartResponse |
| POST | `/api/cart/selection` | guest | `{selected:bool}` -> CartResponse |
| POST | `/api/checkout/quote` | user | 见上，body `{coupon_code?,shipping_method:"standard"|"express"}` |
| POST | `/api/orders` | user | 创建订单，body 见下 |
| GET | `/api/orders` | user | `?tab=all|unpaid|to_ship|to_receive|completed|after_sale&page=` -> `{items:[OrderSummary],counts:{...},...分页}` |
| GET | `/api/orders/{order_no}` | owner | OrderDetail |
| POST | `/api/orders/{order_no}/pay` | owner | `{payment_method:"demo"}` -> OrderDetail |
| POST | `/api/orders/{order_no}/cancel` | owner | `{reason?}` -> OrderDetail，恢复库存/购物车不恢复 |
| POST | `/api/orders/{order_no}/confirm` | owner | 仅 SHIPPED / 包裹已发 -> OrderDetail |
| POST | `/api/orders/{order_no}/refund` | owner | 整单单店铺；`{reason,kind:"refund_only"|"return_refund",detail?}` -> OrderDetail；多店铺/状态不符 422 `AFTER_SALES_UNSUPPORTED`/409 `ORDER_STATE` |
| POST | `/api/orders/{order_no}/reviews` | owner | 一次提交整单评价（逐 item）；返回 `{reviews:[Review]}` |
| GET | `/api/groups/{code}` | public | GroupView |
| POST | `/api/coupons/{code}/claim` | user | 同 2.3 |

订单创建 body：
```json
{"address_id":1,"coupon_code":"WELCOME20","shipping_method":"standard",
 "note":"","payment_method":"demo","idempotency_key":"客户端生成的 16-64 位唯一串"}
```
- 只结算购物车中 `selected=true` 的项；下单事务会从购物车删除它们；订单为 PENDING_PAYMENT 或 group 拼团项仍先创建 PENDING_PAYMENT（付款后变 GROUP_FORMING）。
- `idempotency_key` 必填。同用户同 key 重放返回同一订单（不重复扣库存/重复用券）。不同 body 同 key -> 409 `IDEMPOTENCY_CONFLICT`。
- 服务端重新读取 offer/sku/地址/券；不能信任前端金额。
- group cart item 必须恰好一个，数量 1。若带 group_code 则加入现有团：验证团存在、状态 FORMING、同 offer+sku、未满、用户不是成员；订单付款后才算参团成功。未带 group_code 则创建新团。
- 多 shop 订单自动拆包；创建后生成 order_events 与 package 记录。
- **库存**：创建订单时以条件 UPDATE 扣减 offer.stock；任何一项不足即 409 `STOCK_CHANGED` 并整单回滚。取消/退款 /approve 补回。Group 未成团不补库存直到取消/失败。
- **优惠券**：订单创建时把 user_coupon 标 USED；取消/退款后按可用期状态退回。并发重复用券用条件更新防双花。
- **运费**：standard=0（全站包邮活动），express=1200；group 强制 standard。优惠券门槛按 subtotal 计算，discount 不超过 subtotal。
- **支付**：仅 `demo`；模拟成功可审计且明确 demo。普通订单 -> PAID；group 订单 -> GROUP_FORMING，等待团满后所有已付款成员订单变 PAID。付款事件写 timeline。不得自动确认收货。
- **GROUP_FORMING 取消**：允许取消未成团且未发货的订单，释放 group slot，退款（模拟）并恢复库存/优惠券。成团后不能直接取消，走售后。
- **售后边界（MVP 冻结）**：只支持**整单、单店铺**售后（order_packages 恰好 1 个）；多店铺订单返回 422 `AFTER_SALES_UNSUPPORTED`。仅 PAID/SHIPPED/COMPLETED 可发起；GROUP_FORMING/PENDING_PAYMENT/CANCELLED/REFUNDED/已有处理中售后不可发起。发起后订单状态转 REFUNDING 并把原状态存 last_state_before_refund；同一时刻只允许一个 REQUESTED refund。卖家 REJECT -> refund REJECTED、订单回原状态；APPROVE -> refund APPROVED、订单 REFUNDED、恢复 offer 库存与已用优惠券（如仍可用则恢复 AVAILABLE，否则 EXPIRED），写 order_events。退货物流不在本演示范围。
- **确认收货**：需所有 package 已发货；转 COMPLETED，写事件，评价窗口打开。
- **评价**：需订单 COMPLETED 且商品包含在此订单；每个 order_item 只能评价一次；rating 1-5，title ≤40，body 10-600；需要防重复与越权。
  - OrderDetail 的 `packages[].items[].id` **就是 order_items.id**（稳定评价目标）。每项另含 `reviewable:boolean`（当前订单/当前用户是否还能评）与 `reviewed:boolean`；`actions.review` 为真表示至少有一项可评。
  - 请求固定为 `{"reviews":[{"item_id":31,"rating":5,"title":"很好用","body":"至少十个字的具体评价内容。","tags":["质感好"]}]}`。1-20 项/次；可只评价其中若干项；重复 item_id、非本订单 item、未完成、已评过 -> 409 `DUPLICATE_REVIEW`/400/409 明确错误。返回 `{"reviews":[Review]}`。
  - 评价成功后必须重算该商品真实 rating 与 review_count 聚合并更新商品；不得只写评论不改聚合。

GroupView：
```json
{"code":"G7K2M8","status":"FORMING","required_size":2,"joined_count":1,"expires_at":"…",
 "group_price_cents":319900,"joined_by_me":false,
 "product":{"slug":"cloud-sofa","title":"云朵模块沙发","cover":"…","brand":"山雾 SHANWU"},
 "sku":{"id":9,"name":"雾灰 / 双人位"},
 "offer":{"id":5,"name":"双人位 · 送装一体","kind":"self","shop":{"id":1,"name":"OpenShop 自营"}},
 "members":[{"display_name":"林澄","joined_at":"…","is_initiator":true}],
 "actions":{"can_join":true,"needs_login":false,"reason":null}}
```
- `joined_count` 只计算已付款成员；pending 订单不增长公开进度。付款之后才占坑，坑满即 FULL；但为避免 code 被大量未付款占满，group_members 可包含 PENDING，最多 required_size，公开只展示已付款人数。见 trade 实现说明。

### 2.5 商家（merchant 席，全部 seller 权限）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/seller/overview` | 经营概览；7 天真实订单与事件统计 |
| GET | `/api/seller/products` | seller 自己 offers，支持 q/page |
| PATCH | `/api/seller/products/{offer_id}` | `{price_cents?,stock?,status?}`；校验权限与范围 |
| GET | `/api/seller/orders` | `?status=PENDING|SHIPPED|ALL&page=`；仅自己 shop packages |
| POST | `/api/seller/orders/{package_id}/ship` | `{carrier,tracking_no}`；仅 PAID + 自己的包，原子转 SHIPPED |
| GET | `/api/seller/refunds` | 自己 shop 的售后列表 |
| POST | `/api/seller/refunds/{refund_id}/decision` | `{action:"approve"|"reject",note}`，仅自己；核验状态 |

Seller overview 返回字段：
```json
{"shop":Shop,"range_days":7,
 "metrics":[{"key":"gmv","label":"成交额","value_cents":1293000,"previous_cents":1100000,"delta_pct":17.5,"spark":[1,2,3,4,5,6,7]}],
 "series":{"labels":["10-06","…"],"gmv_cents":[…],"order_counts":[…]},
 "todos":{"to_ship":2,"refunds":1,"low_stock":2},
 "low_stock":[{"offer_id":5,"product_title":"云朵模块沙发","sku_hint":"…","stock":2}],
 "recent_packages":[{"order_no":"…","package_id":1,"status":"PAID","buyer_display":"林**","total_cents":359900,"created_at":"…","item_titles":["云朵模块沙发"]}]}
```
- 7 天窗口无订单时返回真实 0，不能写死；spark 真数据。
- 商家不能看买家完整地址（演示身份也先遮蔽）；发货不需要买家地址。

Carrier 固定 `["青鸟速运","江海快递","晨风同城"]`；tracking_no 6-24 位字母数字。
Refund decision：approve -> Order REFUNDED，写事件并恢复库存/优惠券；reject -> Order 恢复原状态，写事件，保留 reason。仅 REQUESTED 可决定；重复/越权 403/409。

### 2.6 健康

`GET /api/health` -> `{"ok":true,"app":"openshop","version":"0.1.0","demo":true}`（不套 data 信封，供探针直读）。
`GET /healthz` -> 纯文本 `ok`。

## 3. 前端调用约定

- Lead 的 `web/js/core/api.js` 导出 `api.get/post/patch/del`、`ApiError`，自动带 Cookie、CSRF、JSON 和 request id；页面不得直接写 `fetch('/api')`。
- Lead 的 `web/js/core/store.js` 保存 session/user/csrf/cart summary/favorite ids，提供 `refreshSession()`、`refreshCart()`、`subscribe(fn)`、`setSession(payload)`、`toast()`?（toast 在 components）。
- 页面模块导出 `export async function render(ctx)`；`ctx = {root, params, query, navigate, signal}`。路由由 `web/js/app.js` 决定。离开页面时应用 `signal.aborted` 中止异步。
- 所有呈现的用户数据必须转义；优先用 `el()` 创建节点，模板字符串拼接用户内容必须 `escapeHtml()`。
- 页面切换要更新 `document.title`，首 h1 唯一；提供可聚焦跳转。
- 禁止在页面中硬编码价格/库存/订单状态等可能变化的数据；页面只渲染 API 返回值。
- 结算必须使用服务端 quote；禁止客户端自行算最终应付并直接显示为已扣款。
