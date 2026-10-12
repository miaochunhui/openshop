# 当前状态

更新时间：2026-10-13 Asia/Shanghai
阶段：**v0.1.2 本地源码验收版**；本地 Git；**未推送远端、未公开部署**。

## 现读验收

- `OPENSHOP_ACCEPTANCE verdict=PASS`：真实 API **35 PASS / 0 FAIL**；真实 Chromium **11 阶段 PASS / 0 FAIL**；13 张截图；0 console/page error、0 外链请求。
- 并发：**3 轮 × 7 场景 PASS**。覆盖 stock=1 的 8 路抢购、并发幂等、券领取/核销竞态、拼团名额与支付、取消竞态、pay/cancel 竞态；无 500/transport，终态一致。
- 标准库 `unittest discover`：**22 tests PASS**；独立安全审计：**44/44 probes PASS，39/39 controls PASS，5/5 findings 修复回归 PASS**；发布演练负控：**10/10 controls PASS**。
- 仓库门：**PASS**，19 checks、0 failures、1 个非阻断 warning（Actions checkout 按 tag 固定而非 SHA）。
- 全链路真实路径已覆盖：浏览 → 搜索 → 商品 → 购物车 → 一键买家登录 → 新地址/优惠券 → 服务端报价 → 建单/幂等 → 模拟支付 → 商家真实发货 → 买家确认收货 → 评价 → 评价聚合。
- 18 件虚构商品、8 类、10 家店、43 个报价、55 个 SKU、54 条评价、14 个拼团报价。
- 安全审查 5 个 Low 全部修复；整合后 seller RBAC control 与三个限速 control 合法隔离，整包现读全绿；CI 已新增 44 探针步骤。
- 真实浏览器补验：自营拼团从 1/2 付款到第二位真实买家经 UI 付款成团 2/2；商家库存经 UI 写入 26→27 并持久化；两轮浏览器错误均 0。
- 最近 v0.1.2 全链路证据：`/hf3fs-jd/data/tmp/openshop/qa/final-v012-20261013T020433/acceptance-20261013T020433138177-779413/`。安全审计证据：`/hf3fs-jd/data/tmp/openshop/peer-review/v012-ci-20261013T020217/run-20261012T180226534835-728303/`。并发 durable 证据：`/hf3fs-jd/data/tmp/openshop/trade-concurrency-final/run-20261012T171926741056/`。
- 拼团真实交互：`/hf3fs-jd/data/tmp/openshop/lead/group-join-evidence/202610121746293/`；库存真实交互：`/hf3fs-jd/data/tmp/openshop/lead/group-inventory-evidence/20261012174325/`。

详细命令与证据落点见 [testing.md](testing.md)、[RELEASE-REHEARSAL.md](RELEASE-REHEARSAL.md)，安全报告见 [peer-review-core-api.md](peer-review-core-api.md)。

## 明确限制

- **尚未公开发布 / 未 push 远端 / 未在远端 GitHub 实跑 CI**：需要明确的仓库与生产入口授权。v0.1.2 与所有证据均为本地源码交付，不是线上服务。
- 并发测试只覆盖单机单进程 SQLite + loopback + 直连 DB fixture；不覆盖多进程/多机、文件系统锁超时、卖家售后审批并发、拼团过期并发或容量压测。
- 发布演练只做本地归档、解包测试、门禁与启动/登录健康；**systemd 安装、线上切换、TLS/反代、生产数据迁移与回滚演练均未执行**。
- 拼团到期无后台自动取消/退款任务；售后仅整单单店铺；无真实支付、物流、KYC、风控或灾备。
- 商品、品牌、店铺、人物、评价、订单、支付与物流均为虚构或模拟内容。

## 协作容量实情

- 本任务按 infra B1 只建立 **8 个 durable native leader + Lead**，未使用 512 并发；协作容量受固定 8 席和可用工具限制。
- 过程中多席 agent 回合发生失败/无收尾；durable 补位新席被 8 席上限拒绝，两个 fresh 强模型 subagent 也无输出。Lead 接管了失败席的集成与验收。
- 后续 Lead **恢复已有 backend-trade 席**完成 task-9：分支 `seat/trade-concurrency-v2` 提交 `b05928d`，Lead cherry-pick 为集成提交并独立复跑三轮 PASS；又**恢复已有 backend-catalog 席**完成 task-13：提交 `448a30c`，Lead 独立复跑 44/44 探针通过。这些说明至少两次将已完成任务合理分给既有空闲席，但不把它夸大为主代理之外的 512 并行。
- task-12 的本地发布演练工具与负控由 Lead 直接完成；任务板 task-1..13 全部 completed；未执行生产发布动作。完整失败/补位经过见最终交接件，不能把交付描述成“512 agent 完成”。

## 交付位置

- 产品仓库：`/data/dev/openshop`，分支 `master`。
- 本地演示服务（如仍在运行）：`http://127.0.0.1:8780/`，只绑定 loopback，不是公网入口。
- 最终验收截图与 JSON 在 `/hf3fs-jd/data/tmp/openshop/qa/` 下的全绿 run 目录；入仓的精选截图在 [docs/screenshots/](screenshots/)。
- 本地源码 tag/归档与对应 SHA 在最终封版交接记录中列出。
