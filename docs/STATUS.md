# 当前状态

更新时间：2026-10-13 Asia/Shanghai
阶段：**v0.1.1 本地源码验收版**；本地 Git；**未推送远端、未公开部署**。

## 现读验收

- `OPENSHOP_ACCEPTANCE verdict=PASS`：真实 API **35 PASS / 0 FAIL**；真实 Chromium **11 阶段 PASS / 0 FAIL**；13 张截图；0 console/page error、0 外链请求。
- 并发：**3 轮 × 7 场景 PASS**。覆盖 stock=1 的 8 路抢购、并发幂等、券领取/核销竞态、拼团名额与支付、取消竞态、pay/cancel 竞态；无 500/transport，终态一致。
- 标准库 `unittest discover`：**22 tests PASS**；发布演练负控：**10/10 controls PASS**。
- 仓库门：**PASS**，19 checks、0 failures、1 个非阻断 warning（Actions checkout 按 tag 固定而非 SHA）。
- 全链路真实路径已覆盖：浏览 → 搜索 → 商品 → 购物车 → 一键买家登录 → 新地址/优惠券 → 服务端报价 → 建单/幂等 → 模拟支付 → 商家真实发货 → 买家确认收货 → 评价 → 评价聚合。
- 18 件虚构商品、8 类、10 家店、43 个报价、55 个 SKU、54 条评价、14 个拼团报价。
- 安全审查发现的 5 个 Low 已修复并复测五项 finding probes 转 PASS；其一次性审计工具仍保留共享限速桶控制/过时 scope 的历史限制，见安全报告。
- 最近全链路证据：`/hf3fs-jd/data/tmp/openshop/qa/final-v011b-20261013T012502/acceptance-20261013T012502989731-4091857/`。并发证据：`/hf3fs-jd/data/tmp/openshop/trade-concurrency-final/run-20261012T171926741056/`。

详细命令与证据落点见 [testing.md](testing.md)、[RELEASE-REHEARSAL.md](RELEASE-REHEARSAL.md)，安全报告见 [peer-review-core-api.md](peer-review-core-api.md)。

## 明确限制

- **尚未公开发布 / 未 push 远端 / 未在远端 GitHub 实跑 CI**：需要明确的仓库与生产入口授权。当前展示的是本地运行与截图证据，不是线上服务。
- 并发测试只覆盖单机单进程 SQLite + loopback + 直连 DB fixture；不覆盖多进程/多机、文件系统锁超时、卖家售后审批并发、拼团过期并发或容量压测。
- 发布演练只做本地归档、解包测试、门禁与启动/登录健康；**systemd 安装、线上切换、TLS/反代、生产数据迁移与回滚演练均未执行**。
- 拼团到期无后台自动取消/退款任务；售后仅整单单店铺；无真实支付、物流、KYC、风控或灾备。
- 商品、品牌、店铺、人物、评价、订单、支付与物流均为虚构或模拟内容。

## 协作容量实情

- 本任务按 infra B1 只建立 **8 个 durable native leader + Lead**，未使用 512 并发；协作容量受固定 8 席和可用工具限制。
- 过程中多席 agent 回合发生失败/无收尾；durable 补位新席被 8 席上限拒绝，两个 fresh 强模型 subagent 也无输出。Lead 接管了失败席的集成与验收。
- 后续 Lead **恢复已有 backend-trade 席**完成 task-9：分支 `seat/trade-concurrency-v2` 提交 `b05928d`，Lead cherry-pick 为集成提交并独立复跑三轮 PASS；说明至少一次已完成的负载再分配，但不把它夸大为主代理之外的 512 并行。
- task-12 的本地发布演练工具与负控由 Lead 直接完成；未执行生产发布动作。完整失败/补位经过见本次交接件，不能把交付描述成“512 agent 完成”。

## 交付位置

- 产品仓库：`/data/dev/openshop`，分支 `master`。
- 本地演示服务（如仍在运行）：`http://127.0.0.1:8780/`，只绑定 loopback，不是公网入口。
- 最终验收截图与 JSON 在 `/hf3fs-jd/data/tmp/openshop/qa/` 下的全绿 run 目录；入仓的精选截图在 [docs/screenshots/](screenshots/)。
- 本地源码 tag/归档与对应 SHA 在最终封版交接记录中列出。
