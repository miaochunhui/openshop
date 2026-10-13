# 变更记录

本项目当前状态为 **v0.2.0 公网演示在线**：https://openshop.ai4everyone.top/。真实 API、真实 Chromium、三轮并发、44 探针安全审计、29 个标准库测试（含 AI 假 provider 的真实 HTTP/安全集成测试与可信代理解析单测）、仓库门、真实模型 AI 与公网完整购物 E2E 均已通过。DNS、Let’s Encrypt 证书、两台上游 Caddy、systemd 应用与代码回滚已实际执行；公开只读源码由本站归档提供。源码同时公开于 [GitHub](https://github.com/miaochunhui/openshop) 与 [Gitee](https://gitee.com/sdfwefqaa/openshop)，远端 CI 使用 [GitHub Actions](https://github.com/miaochunhui/openshop/actions/workflows/ci.yml)。

记录日期 / 公开发布日期：2026-10-13（Asia/Shanghai）。

## [Unreleased] — 外部公开仓、远端 CI 与受控发布

### Added

- 新增 GitHub `miaochunhui/openshop` 与 Gitee `sdfwefqaa/openshop` 两个新公开代码仓，并接入 GitHub Actions 工作流；CI 运行记录见 [Actions](https://github.com/miaochunhui/openshop/actions/workflows/ci.yml)。
- 新增发布工具 `tools/publish_public_repos.py`：目标仓库和 release commit 写死、默认 dry-run、只推 master + annotated tag v0.2.0、无 force/delete、无自动重试；token 仅从 operator 提供的外部文件读取，推送前扫描完整待发送对象和工作树的 token 字面量。
- 新增 `docs/repositories.md`，记录公开仓、CI、凭据边界和只读回滚计划。

### Notes

- Gitee 服务端拒绝“空仓库设为公开”（实测 422）；最终发布流程是：创建公开 GitHub 仓 → 向新 private Gitee 仓推入 master+tag → 向 GitHub 推送 → Gitee 有内容后再改公开。
- 未改动既有 OpenMath GitHub credential helper、Gitee SSH 登录、token 文件、全局 Git config；本工具的 GitHub helper 只在单条命令内生效且限定到本仓。
- 外部 push/CI 的最终终态与逐 job 结论以操作者证据和 Actions 页面为准，不在本静态变更记录里冒充。

## [0.2.0] — AI 买手中介与公网发布

## [0.2.0] — AI 买手中介与公网发布

### Added

- 新增中立的 AI 中介页面 /ai：登录买家以自然语言提出需求与预算，AI 只从服务端真实在售 offer/SKU 中给出一个主推荐和最多 3 个替代项，解释匹配与取舍；服务端重新获取价格/库存，并给出绑定当前用户、15 分钟有效、只能使用一次的采纳方案。
- 新增 GET /api/ai/status、POST /api/ai/mediate、POST /api/ai/plans/{plan_id}/adopt，复用既有 Cookie+CSRF+登录门与购物车规则；方案采纳不会创建订单或支付。
- 新增 OpenAI 兼容模型接入：服务端仅发送公开供给、本次需求和最近对话；支持环境变量/API key 文件/systemd LoadCredential。缺少模型配置时 AI 明确关闭，商城其余功能正常。
- 新增加固与限额：每条需求长度、对话总长、每日全站额度（SQLite meta）、每日每用户额度、每 IP 滑动窗口、最多 2 个并行上游请求、超时及显式错误。模型返回必须是可校验 JSON，只接受真实成对的 offer_id/sku_id；模型文本中的货币金额会被清洗。
- 新增 tests/test_ai_mediator.py（CI 安全、本地 fake provider、真实 HTTP/SQLite/CSRF/登录/购物车），覆盖 CSRF 403、未登录 401、供给校验、一次性方案 409、用户额度 429、模型调用次数与“AI 采纳不建订单”；新增 tests/test_proxy_headers.py 覆盖可信代理解析。
- 新增 docs/ai-mediator.md、docs/deployment.md；生产 systemd 支持 LAN 边缘上游、AI 出站白名单、非秘密 EnvironmentFile、systemd credential 与可信反代 IP。
- 新增已公开的部署：DNS openshop.ai4everyone.top、Let’s Encrypt 证书、.183/.184 两台上游 Caddy 热重载、openshop.service、单机数据/密钥路径和回滚手册。公网 HSTS/CSP、Secure/HttpOnly cookie 已现读。
- 版本统一升级为 v0.2.0；公开只读源码由本站 source 归档发布。

### Verification

- 真实 deepseek-flash 通过已授权 OpenAI 兼容网关返回合法 JSON；首次 1,100 max-token 导致空 content 的失败被保留，服务端正确返回 AI_INVALID_RESPONSE；提高到 4,000 后真实调用成功。
- 公网真实 Chromium：登录 → AI 真实模型（17.3 秒）→ 服务端复价 → 采纳；桌面 1440 与 390px 无溢出、0 console/page/外链错误；HSTS/CSP、Secure/HttpOnly cookie 通过。
- 公网 AI API：CSRF 403、未登录 401、真实模型 200、采纳、二次采纳 409、未建订单。
- 公网完整购物 E2E：11/11 PASS，13 截图，0 errors；订单 OS20261013004340PLSF 完成模拟支付、商家发货、买家确认收货、评价与 390px 移动订单。
- 购物全链路、三轮并发、44/44 安全探针、真实 Chromium 11 阶段和发布演练工具保持口径；新增测试后标准库测试总数从 22 增至 **29**。
- 代码回滚实际演练：v0.1.2 可从公网启动并健康；切回 v0.2.0 时曾因只切 symlink 未重启导致短暂继续运行旧版，已用 restart 修正并记录，未隐藏该失误。

### Known limitations / remaining boundaries

- AI 需要外部模型网络与配额，模型输出可能有不精确的定性描述；服务端 offer/SKU 身份、价格和库存是权威事实。
- 单进程内存方案存储与每 IP 窗口；重启会丢失方案/窗口，不适合多实例。没有流式 AI 输出或多件自动组合。
- 外部 GitHub/Gitee 新仓库、push 和远端 CI 在 v0.2.0 发布当时未执行；已由后续 [Unreleased] 外部发布工作关闭，见上方与 docs/repositories.md。
- TLS/反代/systemd 已实际部署；Caddy 边缘回滚仅保留逐机备份与命令，本次未执行；无生产容量/多实例/灾备/SLA。
- 商品、支付、物流皆为虚构/模拟。

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
