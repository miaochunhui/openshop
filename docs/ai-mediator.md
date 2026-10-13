# AI 中介 / AI Mediator

OpenShop 在传统商城之外提供一个**中立的 AI 买手中介**：买家用自己的语言说出「买什么、预算、何时要、是否接受拼团」，AI 先理解需求，再从服务端提供的真实在售 offer/SKU 中挑选一件主推荐，解释匹配与取舍，经买家确认后才加入购物车。AI 不创建订单、不支付，也不改写价格和库存。

公网入口：**https://openshop.ai4everyone.top/ai**（已在 2026-10-13 上线并通过真实模型验收；状态与限制见 docs/STATUS.md）。源文件：web/js/pages/ai.js、web/js/pages/ai_view.js、web/css/ai.css。

## 它做什么

- 读取浏览器提交的自然语言委托与最近对话。
- 由服务端构造**真实供给快照**：商品标题、品牌、类目、标签、报价、SKU、价格、库存、预计到货、自营/品牌/工厂、拼团人数和拼团价。
- 将快照交给已授权的 OpenAI 兼容模型网关，要求严格 JSON 输出。
- 服务端验证模型给出的 offer_id 与 sku_id 必须成对存在于同一 offer 的真实目录中，且在该模式下有售。
- 所有金额、库存、店铺与配送信息从 SQLite 重新读取；模型自由文本中的货币金额会被服务端清洗。
- 给出一份绑定当前登录用户、15 分钟有效、只能采纳一次的方案。
- 采纳时复用现有购物车与库存校验；服务端失败不会留下半加入状态；不会创建订单或发起支付。
- 支持追问：前端携带最近对话，最多 6 条消息；每次重新产出并校验一份方案。

## 它不做什么

- 不自动下单、不自动支付。
- 不调用任意工具、不执行模型给的代码、不访问用户地址/订单/支付信息；模型提示词里没有这些数据。
- 不编造供给：不在真实目录中的 offer/SKU 会被拒绝。
- 不代替服务端正价、库存锁、权限或优惠规则。
- 不是「另一个搜索引擎」；产品通过一个明确的 AI 中介页面提供该服务。

## 数据与隐私

只有以下内容会发给模型服务：本次买家输入、最近对话、公开商品目录快照。账号、cookie、地址、购物车明细、订单和支付信息不会被发送。页面对此做显式披露，并要求登录后使用。

外部模型是可选依赖：没有配置网关时，AI 页面会明确显示未启用，其余浏览、购物、商家台功能不受影响。仓库不包含任何 API key。

## 配置

AI 由环境变量控制：

- OPENSHOP_AI_ENABLED：1/0；仍需同时有 endpoint 与 key 才可用。
- OPENSHOP_AI_BASE_URL：OpenAI 兼容网关的 v1 根地址，例如 https://your-gateway.example/v1。
- OPENSHOP_AI_MODEL：请求的模型名，默认 deepseek-flash。
- OPENSHOP_AI_MODEL_LABEL：页面显示的模型标签。
- OPENSHOP_AI_API_KEY：服务端 API key。推荐不要直接写环境，使用 OPENSHOP_AI_API_KEY_FILE 或 systemd LoadCredential。
- OPENSHOP_AI_API_KEY_FILE：只含 key 的 0600 文件路径。
- 若 systemd 提供 CREDENTIALS_DIRECTORY，应用会尝试其中的 openshop_ai_token。
- OPENSHOP_AI_TIMEOUT_SECONDS：默认 55，生产可用 75。
- OPENSHOP_AI_MAX_OUTPUT_TOKENS：默认 4000；推理型模型给得过低会返回空 content。
- OPENSHOP_AI_GLOBAL_DAILY_LIMIT：全站每日上限（默认 80）。
- OPENSHOP_AI_USER_DAILY_LIMIT：每登录账号每日上限（默认 6）。
- OPENSHOP_AI_IP_WINDOW_LIMIT / OPENSHOP_AI_IP_WINDOW_SECONDS：每 IP 滑动窗口（默认 4 次 / 600 秒）。

生产示例见 deploy/openshop.env.example。实际生产配置位于服务器的 /etc/openshop/openshop.env；key 文件不能进入 Git。systemd 从 /etc/openshop/openshop_ai.token 加载凭据，以 0400 的临时 credential 暴露给服务进程。线上值是全站每日 60、每登录账号每日 4、每 IP 每 10 分钟 3。

公网入口由两台 Caddy 上游反代；应用 systemd 只允许这些上游和模型/DNS 端点的 IP，并设置 OPENSHOP_TRUSTED_PROXIES。应用仅对来自这两个上游的连接解析 X-Forwarded-For，按真实客户端 IP 计限额；不要通过公网直接暴露 8790，也不要放宽可信代理。

## API

详见 docs/api-contract.md 的 AI 中介章节。

- GET /api/ai/status：公开的启用状态、模型展示名、限额与用量（登录时包含个人剩余额度）。
- POST /api/ai/mediate：登录 + CSRF。返回对话结论、需求归纳、一个主推荐或追问、最多 3 个替代供给和一次性 plan_id。
- POST /api/ai/plans/{plan_id}/adopt：登录 + CSRF。一次性采纳，仍走现有服务端购物车规则。

服务端限制：单条需求最多 500 字；对话总长受限；最多 2 个并行模型请求；调用超时与 provider 错误有显式 429/502/503/504；限流和全局额度都 fail-closed。方案在进程内存中保存，服务重启即失效；这是有界演示设计，不是生产级分布式方案存储。

## Prompt injection 与输出校验

- 系统提示明确把买家文本和目录视为不可信数据。
- 模型只输出 JSON；服务端拒绝无法解析、缺少字段或引用不存在 supply 的结果。
- offer_id、sku_id、buy_mode 重新与真实目录核对；模型给的金额不被采用。
- 方案采纳前不做任何购物车写入；采纳由服务端重新校验。
- 展示层不渲染模型 HTML；全部通过转义/文本节点输出。

这降低了供给伪造、越权和前端 XSS 风险，但不是对所有生成内容的事实保证；非价格描述仍可能有模型错误。

## 自动化与真实验收

CI 安全且可复跑的测试（本地假 provider，真实 HTTP、真实 SQLite、真实 CSRF/登录/购物车）：

    python3 -B tests/test_ai_mediator.py -v

覆盖：缺 CSRF 403、未登录 401、真实目录中的 offer/SKU 校验、服务端价格优先、一次性方案、跨次采纳 409、用户日额度 429、模型未被拦截请求触达、AI 采纳不创建订单。

真实模型与浏览器证据（2026-10-13，Asia/Shanghai）：

- 真实 OpenAI 兼容模型 deepseek-flash 连通性探针 PASS；公网 AI 服务当前 mode=live，页面/接口版本 0.2.0。
- 公网真实 Chromium：https://openshop.ai4everyone.top/ai，demo 买家登录 → 真实模型 17.3 秒 → 服务端校验与复价 → 采纳 → 购物车角标更新；Secure/HttpOnly Cookie、HSTS/CSP 通过；桌面 1440 与移动 390 无横向溢出，console/page/外链错误均为 0。
  证据：/hf3fs-jd/data/tmp/openshop/deploy/public-ai-browser-20261013T084044/
- 公网 HTTPS API：CSRF 403 → 未登录 401 → 真实模型 200（19.2 秒、9643 prompt tokens、3060 completion tokens）→ 采纳 → 二次采纳 409 → 未创建订单。
  证据：/hf3fs-jd/data/tmp/openshop/deploy/public-ai-api-evidence-20261013T084504/api-live.json
- 线上限额生效证据：每账号每日 4、每 IP 3 次/10 分钟、全站每日 60；状态接口现读 global/user usage。
- 失败保留：首次 1,100 输出 token 上限导致响应无最终 content，服务端正确返回 AI_INVALID_RESPONSE；提高到 4,000 后通过。一次公网 API 验收实际请求成功但证据目录不可写而退出，非 AI 失败；换可写目录重跑 PASS，保留两段记录。
- 自动化测试：tests/test_ai_mediator.py（CI 可跑）并作为 29 个标准库测试的一部分；不使用真实模型。公网证据截图已入仓 docs/screenshots/ai-mediator-*.png。
- 真实模型需要外网与付费网关，未在 GitHub Actions 中调用；CI 只跑假 provider 的真实 HTTP 集成测试。

## 已知限制

- 依赖外部模型可用性；不可用时展示明确错误，不会伪造 AI 回答。
- 当前只推荐一个主供给，不自动组合多件商品；可追问下一件。
- 模型可能给出不精确的定性理由；价格/库存/供给身份以服务端为准。
- 单进程内存限流与单进程方案存储；没有跨实例、持久化 plan 或多实例 AI 配额。
- 每日/每 IP 配额是**成本上限**，不是防滥用或反机器人系统的完整替代。
- 公网已部署并由两台上游 Caddy 终结 TLS；代码回滚已演练（含一次前进时未 restart 的失误与修正）。证据和入口见 docs/deployment.md、docs/STATUS.md；本地/演示验证不等于生产 SLA。
- 外部源码公开于 GitHub/Gitee，CI 由 GitHub Actions 执行；见 docs/repositories.md。真实模型调用仍由人工公网验收（不使用远端 CI 的 token/费用），自动化 CI 只用 fake provider。
