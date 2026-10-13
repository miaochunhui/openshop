# 变更记录

本项目当前状态为 **v0.2.0 AI 中介候选发布**。真实 API、真实 Chromium、三轮并发、44 探针安全审计、25 个标准库测试（含 AI 假 provider 的真实 HTTP/安全集成测试）、仓库门与本地发布演练自检均已通过；真实模型与真实 Chromium 的 AI 中介闭环也已跑通。公开部署、DNS/TLS、边缘及回滚的现读状态见 [docs/STATUS.md](docs/STATUS.md)。

记录日期：2026-10-13（Asia/Shanghai）。公开发布日期与线上地址以 docs/STATUS.md 现读记录为准。

## [0.2.0] — AI 买手中介与公网发布准备

### Added

- 新增中立的 AI 中介页面 /ai：登录买家以自然语言提出需求与预算，AI 只从服务端真实在售 offer/SKU 中给出一个主推荐和最多 3 个替代项，解释匹配与取舍；服务端重新获取价格/库存，并给出绑定当前用户、15 分钟有效、只能使用一次的采纳方案。
- 新增 GET /api/ai/status、POST /api/ai/mediate、POST /api/ai/plans/{plan_id}/adopt，复用既有 Cookie+CSRF+登录门与购物车规则；方案采纳不会创建订单或支付。
- 新增 OpenAI 兼容模型接入：服务端仅发送公开供给、本次需求和最近对话；支持环境变量/API key 文件/systemd LoadCredential。缺少模型配置时 AI 明确关闭，商城其余功能正常。
- 新增加固与限额：每条需求长度、对话总长、每日全站额度（SQLite meta）、每日每用户额度、每 IP 滑动窗口、最多 2 个并行上游请求、超时及显式错误。模型返回必须是可校验 JSON，只接受真实成对的 offer_id/sku_id；模型文本中的货币金额会被清洗。
- 新增 tests/test_ai_mediator.py（CI 安全、本地 fake provider、真实 HTTP/SQLite/CSRF/登录/购物车），覆盖 CSRF 403、未登录 401、供给校验、一次性方案 409、用户额度 429、模型调用次数与“AI 采纳不建订单”。
- 新增 docs/ai-mediator.md；生产 systemd 模板支持 LAN 边缘上游、AI 出站、非秘密 EnvironmentFile 与 systemd credential；补充 AI 环境变量示例。
- 版本统一升级为 v0.2.0。

### Verification

- 真实 deepseek-flash 通过已授权 OpenAI 兼容网关返回合法 JSON；首次 1,100 max-token 导致空 content 的失败被保留，服务端正确返回 AI_INVALID_RESPONSE；提高到 4,000 后真实调用成功。
- 真实 HTTP 验收：CSRF 403、未登录 401、模型仅被调用一次、方案采纳购物车 0→1、二次采纳 409、订单数仍为 0；真实 Chromium 在同一 AI 流程中完成登录、提交、渲染和加购，桌面 1440 与 390px 移动无横向溢出，console/page/外链错误均为 0。
- 购物全链路、三轮并发、44/44 安全探针、真实 Chromium 11 阶段和发布演练工具保持原口径；新增测试通过后标准库测试总数从 22 增至 25。

### Known limitations

- AI 需要外部模型网络与配额，模型输出可能有不精确的定性描述；服务端 offer/SKU 身份、价格和库存是权威事实。
- 单进程内存方案存储与每 IP 窗口；重启会丢失方案/窗口，不适合多实例。没有流式 AI 输出或多件自动组合。
- Systemd 安装、DNS、TLS 与边缘发布在本变更中开始实际执行；最终线上状态与回滚结果以 docs/STATUS.md、最终交接和发布证据为准，不能以“服务还活着”代替验收。

## [0.1.2] — 安全审计全绿与 CI 安全门（本地源码，未公开发布）

### Added / Verified

- 整合后的独立安全审查在 v0.1.2 代码上 **44/44 probes PASS、39/39 controls PASS、5/5 finding 回归 PASS**；Lead 独立复跑，CI 新增真实 44 探针安全审计步骤。
- 旧 seller-stub/404 scope 改为真实 RBAC control；三个限速控制通过同库原地进程重启（保留 SQLite/session，日志 append，进程限速桶清零）获得独立额度，未使用白名单、skip 或放宽断言。
- 真实浏览器补验：拼团发起付款到 1/2，再以第二个真实注册买家经 UI 参团付款到 FULL 2/2；商家工作台经真实 UI 将报价库存 26→27，HTTP 200 且刷新后持久化。浏览器错误 0。
- 应用/健康接口/页脚版本升级为 **v0.1.2**。

### Verification

- `PEER_REVIEW verdict=PASS probes=44 controls=39 failures=0 findings=0 server_boots=4 restarts=3`；所有历史 finding F-01..F-05 保持回归 PASS。
- `OPENSHOP_ACCEPTANCE verdict=PASS`：API 35/35；Chromium 11/11；13 截图；0 console/page/external/API errors。
- 并发仍为 **3 rounds × 7 scenarios PASS**；标准库 **22 tests PASS**；仓库门 **PASS** 19 checks / 0 failures（1 个非阻断 Actions tag-pinning warning）；发布演练负控 **10/10 PASS**。
- 命令与完整证据见 [docs/testing.md](docs/testing.md)、[docs/peer-review-core-api.md](docs/peer-review-core-api.md)。

### Known limitations

- 无公网部署、远端 push、远端 CI 实跑、systemd 安装、TLS/反代或生产回滚演练；v0.1.2 是本地源码版本。
- 安全审查仍是独立 API/夹具测试，不是公网渗透、TLS/HSTS/真实反代、多进程分布式限速或浏览器 XSS 的完整证明；未覆盖范围已在报告中列出。
- 三轮并发只覆盖单机单进程 SQLite + 固定 fixture；拼团无后台过期退款，售后仅整单单店铺，无真实支付/物流/KYC/风控或容量 SLA。

## [0.1.1] — 并发与发布演练补强（本地源码，未公开发布）

### Added

- `tests/test_trade_concurrency.py`：真实 HTTP 三轮并发验收，覆盖库存抢购、幂等、券领取/核销、拼团名额与支付、取消、pay/cancel 七类竞态；每轮独立 SQLite 数据目录，输出 HTML 级原始证据。
- CI 新增真实三轮并发步骤；标准库测试、并发、仓库门可顺序复跑。
- [tools/rehearse_release.py](tools/rehearse_release.py) 的 10 项负控自检（脏树、坏 tag、危险 tar、路径穿越、不健康端口等）、解包服务真实 demo 登录与目录检查、唯一测试端口隔离。
- [docs/RELEASE-REHEARSAL.md](docs/RELEASE-REHEARSAL.md) 与对应正式测试 [tests/test_release_rehearsal.py](tests/test_release_rehearsal.py)。
- 应用、健康接口与页脚版本统一升级到 **v0.1.1**。

### Changed / Fixed

- 结算验收在 3FS 偶发慢 I/O 下会因 5 秒控件等待先超时而误报；保留失败证据后改为等待 20 秒地址控件（并记录原因），最终全链路恢复 11/11 PASS。
- 发布演练为三套后端测试分配独立临时端口，不再与并行 agent/CI 的 8861/8862/8865 冲突。
- 并发验收支持 `OPENSHOP_CONCURRENCY_TEST_PORT` 与 `OPENSHOP_CONCURRENCY_EVIDENCE_ROOT`，默认证据落系统临时目录，便于 CI。

### Verification

- `OPENSHOP_ACCEPTANCE verdict=PASS`：API **35/35**；Chromium **11/11**；13 截图；0 console/page/external/API errors。
- 并发：**3 rounds × 7 scenarios PASS**；无 500/transport；超卖唯一成功、幂等只扣一次、券只绑一单、拼团不超员、pay/cancel 单终态。
- `unittest discover`：**22 tests PASS**；仓库门 **PASS** 19 checks（1 个非阻断 warning）；发布演练自检 **10/10 controls PASS**。
- 证据、复跑命令与未覆盖边界见 [docs/testing.md](docs/testing.md) 与 [docs/RELEASE-REHEARSAL.md](docs/RELEASE-REHEARSAL.md)。

### Known limitations

- 三轮并发只在单进程 SQLite/单机 loopback + DB fixture 上运行；不覆盖多进程/多机、文件系统锁超时、卖家售后审批并发、拼团过期并发，也不构成容量或 SLA 证据。
- 公网部署、远端 push、远端 CI、systemd 安装、TLS/反代和回滚演练仍未执行；发布是本地源码级，不是生产发布。
- 拼团无后台自动过期退款任务；售后只支持整单单店铺；无真实支付/物流/KYC/风控。

## [0.1.0] — 本地源码验收版（未公开发布）

### Added

- 多边商品发现与比较：首页、分类、搜索分面、商品图集、最多三种供给报价、SKU、店铺信息和评价；18 件原创虚构商品、10 家虚构店铺、54 条示例评价。
- 完整买方链路：游客购物车合并、注册/一键演示登录、地址簿、优惠券、服务端报价、幂等下单、模拟支付、订单/包裹/物流时间线、取消、确认收货与评价。
- 真实拼团：邀请码、按已支付成员计算的公开进度、并发名额控制、成团/满员状态；过期在查看/参与/支付时校验。
- 商家工作台：仅本店数据概览、商品/库存/优惠、订单发货、掩码买家信息、整单单店售后审批/拒绝。
- 原生 ES Module + 无构建步骤的响应式前端，原创 SVG 商品/品牌资产与完整移动端底栏、键盘焦点、跳过导航。
- 标准库 HTTP/SQLite 后端：PBKDF2 密码、会话与 CSRF、服务端价格/优惠/库存权威、SQLite WAL 与显式写事务、结构化错误码。
- 独立审查与验收：44 探针安全审计、标准库测试、API QA、真实 Chromium 11 阶段 E2E、仓库门负控自检。
- 发布工程：Apache-2.0 LICENSE、README/CONTRIBUTING/SECURITY/CODE_OF_CONDUCT、安装/架构/API/数据/测试文档、GitHub Actions 只读 CI、非 root systemd 模板与发布/回滚说明。
- 5 张收版截图位于 `docs/screenshots/`；13 张全链路验收截图保存在测试证据目录。

### Fixed

- 会话权限提升时轮换 token 与 CSRF（F-01）。
- 成功注册不再重置 IP 注册限速桶（F-02）。
- 密码登录增加跨 identifier 的 IP 总闸，阻止凭据喷洒（F-03）。
- 超过字段上限的 query 返回 400，而不是 500（F-04）。
- 静态路径 NUL/控制字符拒绝并保证服务持续可用（F-05）。
- 窄屏分类横向滑轨的 2px 页面溢出；评价表单控件命名；不可用优惠券场景；商家订单页验收导航。

### Verification

- `OPENSHOP_ACCEPTANCE verdict=PASS`：真实 API **35 PASS / 0 FAIL**，真实 Chromium **11 PASS / 0 FAIL / 0 BLOCKED**，13 张截图，0 console/page error、0 外链请求。
- 可移植 `python3 -B -m unittest discover -s tests -v`：**21 tests PASS**。
- `REPOSITORY_GATE verdict=PASS checks=19 failures=0 warnings=1`；`--selftest` 20 cases / 19 negative controls PASS。
- 安全 finding F-01～F-05 全部修复，逐 finding probe 转 PASS。
- 命令、覆盖、证据与环境依赖见 [docs/testing.md](docs/testing.md) 和 [docs/peer-review-core-api.md](docs/peer-review-core-api.md)。

### Known limitations / not claimed

- 未公开部署、未 push、未在目标 GitHub 触发远程 CI。
- 完整三轮并发专项套件因执行席 agent 基础设施失败未交付；已有真实并发超卖/幂等/拼团容量探针。
- 拼团到期无后台自动取消/退款任务；售后仅整单单店铺。
- 无真实支付、物流、KYC、风控、生产容量、跨实例会话/限速、TLS/Caddy 或灾备演练；本地源码验收不等于生产发布。
- 当前协作容量为 8 个 leader + Lead，不声称 512 并发；agent 失败与替换上限见交付记录。
