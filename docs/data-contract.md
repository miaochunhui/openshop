# 内容种子契约

真源：`data/catalog.json`（UTF-8，2 空格缩进）。视觉资产为仓内自绘 SVG，零外链。种子只描述**虚构**商品、品牌、店铺、人物与评价。

## 顶层

```json
{
  "version": "1.0.0",
  "currency": "CNY",
  "categories": [],
  "shops": [],
  "users": [],
  "coupons": [],
  "products": []
}
```

## Category

```json
{"slug":"home","name":"居家生活","subtitle":"让家更像自己","icon":"sofa","sort_order":1}
```
- slug `[a-z0-9-]{2,32}` 唯一；icon 从 Lead 的共享 icon 名白名单中选（见 §图标）。

## Shop

```json
{"slug":"openshop-self","name":"OpenShop 自营","kind":"self","tagline":"官方选品，仓配直达",
 "story":"从源头到包装，每件商品都经过自己的质检台。","rating":4.9,"followers_count":18230,
 "since":"2021","verified":true,"response_rate":"99.6%","ship_from":"上海 · 嘉定"}
```
- kind：`self`（官方自营）/ `brand`（品牌旗舰）/ `factory`（源头工厂）。
- 至少 4 个店铺，含一个 self（演示商家所属）。rating 仅作店铺展示，不替代商品评分。

## User（仅演示种子）

```json
{"handle":"demo_buyer","display_name":"林澄","email":"buyer@openshop.demo","role":"buyer",
 "avatar":"/web/assets/brand/avatar-cheng.svg","member_since":"2024-03-18",
 "coupons":["HELLO20","HOME60"],
 "addresses":[{"label":"家","receiver":"林澄","phone":"13800000002",
   "province":"上海市","city":"上海市","district":"徐汇区","detail":"漕溪北路 88 号 6 栋 502","is_default":true}]}
```
seller：
```json
{"handle":"demo_seller","display_name":"周屿","email":"seller@openshop.demo","role":"seller",
 "avatar":"/web/assets/brand/avatar-zhou.svg","member_since":"2023-08-02","shop":"openshop-self"}
```
- 必须恰好一名 `demo_buyer` 风格买家（演示入口 buyer 取它）与一名 seller。
- 可另加一位 `demo_buyer_two`（配合真实拼团第二人验收；若无也可以 API 注册）。
- 种子不含密码字段。演示登录由 `POST /api/auth/demo` 完成；普通账号通过注册设置密码。
- 邮箱必须唯一且符合邮箱格式；所有电话使用明显的演示号码（1380000000x）。

## Coupon

```json
{"code":"HELLO20","title":"新人见面礼","description":"满 ¥199 减 ¥20",
 "threshold_cents":19900,"discount_cents":2000,"scope":"all",
 "valid_days":30,"auto_claim_new_user":true,"quantity":1000}
```
- code 大写字母数字，唯一；scope `all` 或 `category:<slug>`。
- 满减：discount 不超过 threshold；一次只可用一张，不与其它券叠加。
- `auto_claim_new_user=true`：注册或第一次 demo 登录的买家自动获得一张（不重复）。
- quantity 为可领总量，种子只作演示。

## Product

```json
{
 "slug":"cloud-sofa",
 "title":"云朵模块沙发",
 "subtitle":"可组合的松弛角落",
 "brand":"山雾 SHANWU",
 "category":"home",
 "description":"用更深的坐深和高回弹填充，接住下班后的每一次瘫坐。",
 "story":"我们花了很多时间推翻扶手。最后留下一个刚好能靠住的弧度。",
 "tags":["模块化","小户型友好","可拆洗"],
 "badges":["原创设计","送装一体"],
 "art":{
   "cover":"/web/assets/products/cloud-sofa.svg",
   "cover_alt":"米白色模块沙发与落地灯组成的暖色客厅插画",
   "images":[{"src":"/web/assets/products/cloud-sofa.svg","alt":"云朵沙发正面"},
             {"src":"/web/assets/products/cloud-sofa-detail.svg","alt":"圈绒面料与坐垫细节"}]
 },
 "flags":{"hero":true,"featured":true,"new":true},
 "sold_count":128,
 "highlights":[{"title":"一坐就陷进去的云感","body":"高回弹海绵与羽绒混合填充，坐深 62cm。"}],
 "specs":[{"title":"尺寸与材质","items":[{"label":"面料","value":"混纺圈绒"},{"label":"尺寸","value":"双人位 180 × 95 × 72cm"}]}],
 "detail_sections":[{"title":"把客厅留给松弛","body":"模块化结构，可单买、可拼接，也能在搬家时重新组合。"}],
 "faq":[{"q":"是否支持拆洗？","a":"外套可拆洗，建议专业干洗。"}],
 "skus":[{"name":"雾灰 / 双人位","options":{"颜色":"雾灰","规格":"双人位"},"price_delta_cents":0},
         {"name":"燕麦 / 双人位","options":{"颜色":"燕麦","规格":"双人位"},"price_delta_cents":20000},
         {"name":"雾灰 / 三人位","options":{"颜色":"雾灰","规格":"三人位"},"price_delta_cents":90000}],
 "offers":[
   {"shop":"openshop-self","kind":"self","name":"双人位 · 送装一体","price_cents":359900,
    "market_price_cents":429900,"shipping_fee_cents":0,"stock":26,
    "delivery_eta_days":2,"delivery_note":"上海仓直发 · 送装一体",
    "services":["正品保障","7天无理由","送装一体"],"is_primary":true,
    "group_size":2,"group_price_cents":319900},
   {"shop":"shanwu-home","kind":"brand","name":"品牌旗舰 · 双人位","price_cents":369900,
    "market_price_cents":429900,"shipping_fee_cents":0,"stock":18,
    "delivery_eta_days":4,"delivery_note":"品牌仓直发 · 预约送装",
    "services":["品牌直营","7天无理由"],"is_primary":false}
 ],
 "reviews":[
   {"author":"陆**","rating":5,"title":"比照片更温柔","body":"……","tags":["坐感舒服","颜色耐看"],
    "verified":true,"helpful_count":18,"days_ago":6,"sku_name":"雾灰 / 双人位"}
 ]
}
```
### 必填/约束
- 至少 **18 个商品**，覆盖至少 6 个分类；每个分类至少 2 件商品；每件必须有：
  - 唯一 slug（`[a-z0-9-]{2,64}`）、标题 ≤28 字、副标题 ≤40 字、brand、已存在 category；
  - 1 个 cover 且文件存在；images 至少 1 项（可含 cover），全部资产必须为项目内本地路径 `/web/assets/...`；
  - 至少 2 个 offer，且每个商品至少 1 个 self 或 brand offer；至少 6 个 offer 带真实拼团（`group_size` 2-3 与更低的 `group_price_cents`）；
  - 至少 3 个 SKU，price_delta_cents 0 或正整数；
  - 至少 2 个 highlights、1 组 specs、1 个 detail_section、2 个 FAQ；
  - 至少 3 条 reviews；rating 由 DB 从 reviews 计算，不在 JSON 里写 aggregate；
  - flags 可含 hero/featured/new；恰好 1 件 hero=true；至少 8 件 featured=true。
- offers 中同一商品只允许 1 个 is_primary=true；`kind` 必须与所属 shop.kind 一致；价格为正整数且 market_price_cents >= price_cents；group_price_cents < price_cents；stock ≥ 0（演示不得售罄主商品，主 offer stock ≥ 8）。
- 配送: eta_days 1-7；services 数组非空；shipping 全部 0，展示“全站包邮”活动；最终实现仍以服务端运费规则为准。
- 评价 rating 1-5；body 10-240 字，具体、有信息，不虚构医疗/绝对化功效；同一商品内 title 不重复；review_count 由 DB 计算，卡片显示真实 DB 数。
- 文案避免真实品牌、真实平台商标、明星姓名、医疗承诺、虚假“全网最低”。所有内容为演示。

### 图标白名单
`sofa, lamp, headphones, reader, speaker, jacket, shoe, bag, candle, serum, toothbrush, tent, backpack, bottle, pot, coffee, board, robot, fan, humidifier, notebook, bookmark, puzzle, shield, truck, return, chat, heart, search, cart, user, star, arrow, check, plus, minus, close, menu, spark, leaf, factory, store, card, wallet, box`

### 首页内容
- Lead 以 flags、分类、折扣和拼团字段现算首页；设计席不要另建 hero 文案数据文件。
- 活动文案在页面模板中可编辑，但价格/库存/评分/评论数一律来自 API。

## 图像规范
- 每个商品至少两张原创 SVG（cover + detail），画布 viewBox 建议 `0 0 1200 900` 或 `0 0 900 900`；无外链、无 base64 大图、无字体依赖、无 EXIF。
- 视觉：暖纸底色、低饱和材质、清晰主体、柔和阴影、纸感颗粒；同一光源与留白体系；禁止复制真实电商商品图。
- 文件命名 `web/assets/products/<slug>.svg`、`<slug>-detail.svg`；文件名只用小写连字符。
- 通用占位图不得冒充真实商品；若某商品尚未出图，宁可缩小商品集合也不发破图。
