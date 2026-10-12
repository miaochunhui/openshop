# 变更记录

本项目当前状态为 **v0.1.1 本地源码验收版**。真实 API、真实 Chromium、三轮并发、标准库测试、仓库门与本地发布演练自检均已通过；**未公开部署、未推送远端仓库、未在远端 GitHub 实际执行 CI**。正式生产发布仍需 [docs/RELEASE.md](docs/RELEASE.md) 中的授权、远端 CI、部署与回滚演练。

记录日期：2026-10-13（Asia/Shanghai）。公开发布日期：待定。

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
