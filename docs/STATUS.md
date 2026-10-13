# 当前状态

更新时间：2026-10-13 12:22（Asia/Shanghai）
阶段：**v0.2.0 公网演示在线**（非生产级）；本地 Git；外部公开仓与 GitHub Actions CI 已接入。
公网入口：https://openshop.ai4everyone.top/
公开代码仓：https://github.com/miaochunhui/openshop · https://gitee.com/sdfwefqaa/openshop
远端 CI：https://github.com/miaochunhui/openshop/actions/workflows/ci.yml
本站只读源码：https://openshop.ai4everyone.top/web/source/openshop-0.2.0.tar.gz（同名 .sha256）

## 现读验收

- 公网真实 API/浏览器全购物旅程：**API 35/35**；公网 Chromium **11/11**（搜索→商品→购物车→登录→地址/券→报价→模拟支付→商家发货→确认收货→评价；另含 390px 移动订单）。13 张公网截图，0 console/page/external/API errors。订单 OS20261013004340PLSF。证据：/hf3fs-jd/data/tmp/openshop/deploy/public-full-e2e-20261013T084320/。
- 公网 AI：真实 deepseek-flash 浏览器闭环 PASS（17.3 秒；登录→AI→服务端复价→采纳），Secure/HttpOnly Cookie、HSTS/CSP，桌面 1440 与移动 390 无溢出，0 errors。证据：/hf3fs-jd/data/tmp/openshop/deploy/public-ai-browser-20261013T084044/。
- 公网 AI API：CSRF 403 → 未登录 401 → 真实模型 200 → 采纳成功 → 二次采纳 409 → 未建订单。证据：/hf3fs-jd/data/tmp/openshop/deploy/public-ai-api-evidence-20261013T084504/api-live.json。一次同流程因证据目录不可写退出（请求成功、写出失败），保留后换可写目录重跑。
- /api/health 现读 version=0.2.0；/api/ai/status 现读 mode=live。服务由 systemd 管，公网健康与 HSTS 已验证。
- 本地上线前门：29/29 标准库（含 AI 假 provider 真实 HTTP/安全集成 + 可信代理 IP 单测）、3×7 并发、44/44 安全探针（39 controls）、API 35/35、Chromium 11/11、仓库门 PASS（19 checks、0 failures；非阻断 warnings 为 Actions tag-pin 与离线外链未抓取，数量以当次输出为准）。证据日志：
  /hf3fs-jd/data/tmp/openshop/lead/v020-final-unit-20261013T083052.log
  /hf3fs-jd/data/tmp/openshop/lead/v020-final-concurrency-20261013T083052.log
  /hf3fs-jd/data/tmp/openshop/lead/v020-final-security-20261013T083052.log
  /hf3fs-jd/data/tmp/openshop/qa/final-v020-20261013T083310/
- AI 安全/限额：按登录账号每日 4 次、每 IP 3 次/10 分钟、全站每日 60 次；模型输出只作推荐，必须回到服务端真实 offer/SKU 与价格校验。全局/用户额度持久化 SQLite；IP 窗口和一次性 15 分钟方案为单进程内存，服务重启会清空方案/IP 窗口。密钥通过 systemd LoadCredential，不进入 Git/环境文件明文。
- 历史 v0.1.2 拼团 FULL 2/2、商家库存 26→27 补验与 44 探针证据仍保留。

## 公网变更

- DNS：openshop.ai4everyone.top A 43.135.70.136，DNSPod RecordId 2429400643；DNSPod Describe 与公共解析复核一致。
- TLS：Let’s Encrypt SAN=openshop.ai4everyone.top，issuer YE1，有效期至 2027-01-10；crt 644、key 600，crt/key 已同步 .183/.184 两台上游。NO_RESTART=1 签发，未重启其他边缘。
- Caddy：openshop 独立 SNI 块在 .183/.184 热重载；两次 VALIDATE_OK，主进程 PID 未变，备份见 /etc/gitlab-edge/Caddyfile.bak.20261013T083907 与 .083927。公网响应带 HSTS、CSP、nosniff、frame deny 等。
- 应用：openshop.service（专用非 root 用户，/opt/openshop/current，/var/lib/openshop）。systemd IPAddressAllow 只含回环、两台上游 .183/.184、模型网关与 DNS，IPAddressDeny=any。未授权本机 LAN 源直连 8790 超时；两台上游直连健康 200。边缘 IP 同时是可信反代，以便从 XFF 解析真实客户端用于限速。
- 回滚：v0.1.2 回滚实际执行并恢复 0.2.0；过程发现“仅切 current + systemctl start 不会重启已运行进程”，已用 restart 修正。数据快照 /var/backups/openshop/rollback-20261013T085046/ (sha256 903c7f29…)。Caddy 两机各有独立回滚备份与命令；本次未执行边缘回滚。详见 docs/deployment.md。

## 明确限制 / 未完成边界

- **外部 Git 发布采用独立工具与 operator-supplied token 文件**，已创建 GitHub/Gitee 两个新公开仓并接入 GitHub Actions；仓库链接、发布顺序、凭据边界与只读回滚见 [repositories.md](repositories.md)。没有改动既有 OpenMath GitHub helper、Gitee SSH 登录、全局 Git 配置或 token 文件。
- Gitee 服务端要求**先有内容再公开**：空仓改公开会被 422 拒绝。发布流程因此在 GitHub 公开仓创建后，先向仍是 private 的新 Gitee 仓推 master+annotated tag，再推 GitHub，最后将 Gitee 改公开。失败会停在首因并保留 receipt，没有隐式重试。
- 远端 CI 逐 job 结论以 GitHub Actions 现读为准；不在本静态页把“服务在线”冒充 CI 通过。
- 单进程应用 + SQLite；无多实例会话/限速、无容量/SLA、无灾备。systemd 白名单和可信代理解析是单机控制，不是 DDoS 防护。
- AI 依赖外部模型网络/配额；公网冷调用约 17–19 秒、无流式；模型可能产生不精确的定性描述，价格/库存以服务端为准。所有 AI 采纳不会自动下单或支付。
- 无生产支付、真实物流、KYC/风控；拼团无到期自动退款；售后仅整单单店铺。
- 商品、品牌、人物、评价、订单、支付与物流均为虚构或模拟内容。
- 本次部署由现有 infra 操作链执行；边缘配置投放使用仓库既有 apply-edge-block.sh 的 zyj2 身份要求。没有更改既有凭据、OpenMath/FreeChat/oss-* 服务。

## 协作容量实情

- infra B1 固定上限 **8 个 durable 强模型 leader + Lead**，无私有 worker 池；**没有 512 并发，也不声称 8 席持续满载**。
- 本轮可核对活跃峰值：Lead + 1 个既有 storefront-flow 席；该席认领 task-14 后未产出即 inactive，Lead 接管 AI 前端实现。没有证据支持更高的实际并发峰值。
- 成功补位/复用：此前 backend-trade 完成 task-9（Lead cherry-pick 并复跑三轮），backend-catalog 完成 task-13（Lead 覆盖复跑 44/44）。累计可核对成功补位 2 席次；task-14 的一次补位失败，Lead 接管。
- 早期多席 agent 失败/无收尾，durable 新席创建被 8 席上限拒绝，两个 fresh 强模型 subagent 无输出；详见最终交接件。
- 不能把本交付描述成“512 agent 完成”。

详细命令、门与证据见 docs/testing.md、docs/deployment.md；安全报告见 docs/peer-review-core-api.md。
