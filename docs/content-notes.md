# 内容与视觉说明

本文记录 OpenShop 开集的内容种子、原创商品插画与视觉精修层。所有品牌、店铺、商品、人物与评价均为虚构演示数据；没有真实商标、明星、外部素材、医疗承诺或虚假促销信息。接口字段以 docs/data-contract.md 为准。

## 交付范围

- data/catalog.json：18 件商品、8 个分类、10 家店铺、43 个 offer、55 个 SKU、54 条评价、14 个含拼团价的 offer。
- web/assets/products/：18 张封面 + 18 张细节图，共 36 个原创本地 SVG。
- web/css/theme.css：只做视觉精修，不重排页面布局。
- docs/content-notes.md：本说明。

## 分类与店铺

8 个分类：居家生活 3 件、数码影音 2 件、户外出行 2 件、厨房餐厨 2 件、穿着配饰 2 件、美妆个护 2 件、智能家电 2 件、文创礼物 3 件。每个分类至少 2 件。

| 店铺 slug | 名称 | 类型 |
| --- | --- | --- |
| openshop-self | OpenShop 自营 | self |
| shanwu-home | 山雾家居旗舰店 | brand |
| echo-lab | 回声影音专营店 | brand |
| northisle-outdoor | 北屿户外旗舰店 | brand |
| wayline-kitchen | 未行厨房旗舰店 | brand |
| suzhi-atelier | 素织衣着工作室 | brand |
| qingwu-care | 青芜个护旗舰店 | brand |
| xiaoman-electric | 小满电器旗舰店 | brand |
| zhijian-gift | 纸间礼物店 | brand |
| direct-craft | 直造生活工厂店 | factory |

每件商品都有自营 offer；八个品类品牌中，山雾提供 2 件商品的品牌 offer，回声、北屿、未行、素织、青芜、小满、纸间各提供其品类的品牌 offer。直造为家居、户外、餐厨、配饰和礼物类的 8 个 offer 提供工厂价。

## 18 件商品

| 分类 | slug | 标题 | 拼团 |
| --- | --- | --- | --- |
| home | cloud-sofa | 云朵模块沙发 | 2 个 offer |
| home | paper-lantern | 薄暮纸罩台灯 | 1 个 offer |
| home | grain-basket | 芦编提手收纳筐 | — |
| digital | hush-headphones | 静岛降噪耳机 | 1 个 offer |
| digital | reed-speaker | 苇声便携音箱 | 1 个 offer |
| outdoor | trail-pack | 起风轻量登山包 | 2 个 offer |
| outdoor | camp-table | 折木轻量桌 | 1 个 offer |
| kitchen | morning-kettle | 慢早珐琅手冲壶 | 1 个 offer |
| kitchen | cast-pan | 黑石厚铁煎锅 | 1 个 offer |
| style | linen-overshirt | 水洗亚麻罩衫 | — |
| style | canvas-carry | 晨路帆布托特包 | 1 个 offer |
| care | camellia-handcream | 山茶润手霜 | 1 个 offer |
| care | beech-toothbrush | 榉木软毛牙刷 | — |
| smart | loop-fan | 环流循环扇 | 1 个 offer |
| smart | mist-humidifier | 雾间加湿器 | — |
| gift | grid-journal | 方格线装手账 | — |
| gift | letterpress-notes | 活版花笺信纸 | — |
| gift | lullaby-box | 榉木八音盒 | 1 个 offer |

- 恰好 1 件 hero：cloud-sofa；featured 12 件。
- 4 个原有商品 slug（cloud-sofa、hush-headphones、trail-pack、morning-kettle）及其可拼团 offer 均保留；原有价格、主 offer 与拼团价未被删除。trail-pack 与 morning-kettle 各新增一个品牌 offer，morning-kettle 补足第 3 个 SKU 与第 3 条评价，trail-pack 补足第 3 条评价。
- 拼团规格为 2 人或 3 人，团价严格低于同一 offer 的标准价；种子只创建 offer，不预造已满的团，成团进度由交易流程产生。
- 54 条评价是实际写入数据库的 3 条/件，不虚构更大的 review_count；卡片与详情页评价数均来自数据库。

## 文案口径

- 品牌、店铺与人物名字为项目自造，不含真实品牌、平台、明星或版权素材。
- 文案强调材料、尺寸、工艺、配送和真实使用限制；每条评价都给出具体场景、一个取舍或不便，评分包含 3/4/5 星，不做一边倒夸赞。
- 禁用施压与虚假承诺：没有“最后 N 件”“手慢无”“全网最低”等表述；拼团人数、库存、评分、评价数均不在 JSON 中写死展示值，由服务端读取。
- 个护类描述只涉及气味、质地、包装和使用感，不作医疗或绝对功效承诺；FAQ 明确个体差异、停止使用等边界。
- 优惠券、服务承诺与物流时效为演示口径；支付仍为模拟流程，商品价格以服务端结算为准。

## 原创插画

- 每件商品恰好使用两张本地 SVG：web/assets/products/<slug>.svg 与 <slug>-detail.svg；都以 /web/assets/... 引用，路径全部存在。
- 画布统一为 0 0 1200 900。画面无文字、无字体依赖、无外部链接、无 base64、无 script、无 EXIF；SVG 内含 title/desc，目录另有 cover_alt 与图片 alt。
- 视觉共同语言：暖纸底色、低饱和土色、左上主光、柔和投影、纸纹颗粒与明确轮廓。每件商品的场景、构图与细节图主题均单独绘制，不共用一张换色模板。
- 36 个文件的 SHA-256 互不重复；所有图像经 Chromium 实际加载检查为 36/36 成功，目录墙 18/18 张图片加载成功。

## theme.css 精修

theme.css 是加载顺序最后一层，只在 base.css、components.css 与各页席 CSS 之上追加：

- 纸面氛围与标题/正文/价格字阶，商品卡标题采用系统宋体衬线，元信息使用更深的暖灰。
- 商品卡：细边框、纸相框式内外阴影、图片高光膜、拼团胶囊、悬停上浮和 focus-within 提示；不改卡片栅格。
- 货架：home-shelf / home-arrivals 增加极轻纸光与一条细货架线；分类卡、服务承诺与个人货架板改用暖纸渐变。
- 焦点：双层高对比焦点环；深色 Hero、拼团区、页脚切换为琥珀焦点色。原先全局选择器覆盖 tabindex="-1" 的问题已在收口时排除，路由标题不会再出现夸张的页面级焦点框。
- 状态：筛选选中、供给选中、售罄、禁用、表单错误、空态、骨架、Toast；供给选中会获得暖色内晕。
- 暗部：Hero、拼团暗板、促销条与页脚加入分层暖光、细纸纹和外/内阴影；页脚链接悬停带下划线。
- 全部纹理为 CSS 渐变或 SVG 内联表达，无外链字体或资源。没有写页面布局规则，没有修改 base.css、components.css 或任何 pages 文件。

## 校验与证据

外置生成器与校验器在 /hf3fs-jd/data/tmp/openshop/design-content/build/（不提交，可由 Lead 检查或丢弃）。数据源与仓库文件一致。

- 全量内容/规则/资产校验：
  - VERDICT PASS checks=54 failures=0
  - SUMMARY {"categories": 8, "featured": 12, "group_offers": 14, "hero": 1, "offers": 43, "products": 18, "reviews": 54, "shops": 10, "skus": 55}
  - 原始输出：/hf3fs-jd/data/tmp/openshop/design-content/evidence/validate-catalog.txt（另有同名 .json 摘要）。
- 独立数据目录真实启动 seed（127.0.0.1:8866）：
  - 启动日志包含 OPENSHOP_READY url=http://127.0.0.1:8866/；/api/health 返回 ok，/healthz 为 ok。
  - SQLite 检查：products=18, categories=8, shops=10, offers=43, skus=55, reviews=54，拼团 offer 14，断链资产 0；种子演示订单 3 条。
  - 原始日志与结果：.../evidence/server-8866.log、.../seed-db.json。
- 运行中服务的静态资产检查：
  - VERDICT PASS unique_assets=36 refs=54 failures=0；36 个文件 HTTP 200、image/svg+xml、与磁盘字节一致、XML 可解析。
  - 原始输出：.../server-asset-check.txt、.../server-asset-check.json。
- 视觉证据（外部 3FS，均不是提交内容）：
  - 36 张艺术品联系表：.../art-contact.png。
  - 18 件商品目录墙：.../catalog-wall.png。
  - 主题预览：.../theme-preview-full.png、theme-preview-hero.png、theme-preview-shelf.png、theme-preview-group.png、theme-preview-focus-keyboard.png、theme-preview-mobile-hero.png。
  - 真实 8866 服务外壳截图：.../server-8866-shell.png。
  - 图片加载检查：.../media-check.txt（36/36 艺术图、18/18 目录卡成功）。

## 未覆盖边界（需 Lead 集成后复验）

- 本席分支的 web/js/pages/ 与目录/交易 API 仍由其他席实现，独立运行时真实页面只到壳层；因此上述浏览器图片来自使用同仓共享样式与 discovery 席样式快照构建的外置静态预览，不是最终路由级验收。页面上的最终供给、价格、优惠、订单和拼团状态必须在 Lead 集成树重新跑真实浏览器验收。
- 本席只验证 seed 成功、数据到 DB 的数量与资产可服务性；下单、支付、真实成团、评价提交、商家发货等交易能力不在本席写集，未由本席宣称通过。
- 外置预览里的 Hero/统计文案属于设计预览，不是商品目录真源；真实首页活动文案由首页席从 API 数据生成。
- 所有内容为演示虚构；没有真实支付、真实物流或真实库存承诺。
