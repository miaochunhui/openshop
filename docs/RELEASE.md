# 发布与回滚流程

更新时间：2026-10-13 08:55（Asia/Shanghai）
目标版本：**v0.2.0**（公网演示已上线；最终 annotated tag、完整 SHA 与归档 SHA 以最终交接为准）

## 当前状态（先读）

- 公网入口：https://openshop.ai4everyone.top/，HTTPS/HSTS/CSP，真实购物 E2E 11/11 与真实 AI 闭环已在公网通过。
- 公开代码仓：https://github.com/miaochunhui/openshop · https://gitee.com/sdfwefqaa/openshop；远端 CI：https://github.com/miaochunhui/openshop/actions/workflows/ci.yml。
- 本站只读源码：https://openshop.ai4everyone.top/web/source/openshop-0.2.0.tar.gz（与同名 .sha256）。
- 本地与在线门：29 标准库测试、3×7 并发、API 35/35、Chromium 11/11、44/44 安全探针、仓库门 PASS。
- 用户已明确提供本次公网/开源发布授权。部署者操作既有 infra 链；边缘配置投放沿用仓库既有工具要求的 zyj2 身份。
- 外部 Git 由独立工具与 operator-supplied token 文件发布为两个**新**公开仓；既有 OpenMath GitHub helper、Gitee SSH、全局 Git config 与 token 文件未被修改。GitHub Actions 的逐 job 终态以 Actions 页面和当次外部证据为准。
- Gitee 服务端禁止空仓公开（422）；launch 必须先向 private Gitee 仓推送 master+tag，有内容后再切公开；不得以隐式重试或替代凭据绕过。
- 本站源码/演示不是生产级安全、容量或 SLA 认证；所有商品、人物与支付/物流都是虚构/模拟。

## 发布前置门

在固定、干净 commit 上原样运行：

    git status --short
    git log -1 --format="%H %s"
    python3 -B -m unittest discover -s tests -v
    python3 -B tests/test_trade_concurrency.py
    python3 -B tests/test_ai_mediator.py -v
    python3 -B tests/test_peer_review_probes.py
    python3 -B tools/check_repository.py --selftest
    python3 -B tools/check_repository.py --check

要求：

1. 工作树干净；没有运行数据库、日志、缓存、截图临时件或凭据进入归档。
2. 测试、并发、44 探针、仓库门全部真实 PASS；不得用白名单、skip 或放宽断言求绿。
3. AI 集成测试使用本地 fake provider，不依赖真实模型/密钥/外网；真实 AI 另以公网浏览器与 API 验收，保存原始记录。
4. 主路径浏览器和公网购物 E2E 均已通过：13 张截图、0 console/page/external/API errors，包含模拟支付、商家发货、确认收货与评价。
5. systemd unit、健康检查、IP 地址白名单、可信反代、DNS/TLS/边缘和回滚都已现读演练或记录；Caddy 边缘回滚除外，备份和命令存在但本次未执行。
6. 版本必须与 APP_VERSION、页脚、CHANGELOG、tag 和健康接口一致（v0.2.0）。

任何失败都停止发布；保留第一失败证据，修问题后重新跑全部门，而不是删除/放宽门。

## 固定制品

- annotated tag：v0.2.0（在最终集成提交上创建）。
- 源码归档由 git archive 从 tag 生成，不能从脏树 tar 打包。
- 发布演练：tools/rehearse_release.py --tag v0.2.0 --out-dir <external-evidence>；
  它验证干净树、tag 指向 HEAD、归档边界、SHA-256、解包测试/门禁、
  真实启动健康、demo 登录、目录和干净停止。summary 和日志外置。
- 本站 source 归档必须是最终 tag 的同一字节集；旁边提供 SHA-256。
- 实际生产固定 release 目录为 /opt/openshop/releases/<tag 或固定 SHA>，
  root:root 只读；/opt/openshop/current 原子切换。

## 部署形态（已执行）

- DNS：openshop.ai4everyone.top A 43.135.70.136，DNSPod RecordId 2429400643。
- TLS：Let’s Encrypt 单域证书，两台上游 /opt/docs-site/runtime/tls/openshop.crt|key，
  crt 644/key 600/docsweb；有效期至 2027-01-10。
- Caddy：独立子域块，.183/.184 双机 validate 后 SIGUSR1 热重载，主 PID 不变；
  每机有 Caddyfile.bak。<timestamp> 回滚备份。
- 应用：openshop.service，非 root 用户 openshop，0.0.0.0:8790；
  systemd IPAddressDeny=any + 白名单（回环、.183/.184、模型网关和 DNS 解析器）。
  本机非白名单 LAN 源访问 8790 实测超时；.183/.184 直连健康 200。
- 可信反代：OPENSHOP_TRUSTED_PROXIES=.183/.184；只对这些源接受 XFF。
- 数据：/var/lib/openshop/openshop.sqlite3，外置 StateDirectory，0700。
- AI 凭据：/etc/openshop/openshop_ai.token（0600 root）通过 systemd LoadCredential；
  服务里只出现 0400 credential。非秘密环境在 /etc/openshop/openshop.env（0600 root）。
  密钥不在 Git/报告/本页中。
- 线上限额：每登录账号每日 4 次，每 IP 3 次/10 分钟，全站每日 60 次；
  最多 2 个并行上游请求。全局/用户额度持久化 SQLite，IP 窗口/方案为单进程内存。

详细拓扑、证据与命令见 docs/deployment.md。

## 切换（必须在改动 current 后 restart）

1. 备份数据和当前 unit/current 目标；记录旧 release。
2. 将已验证归档解入新 root:root 只读 release 目录。
3. 原子更新 /opt/openshop/current。
4. **systemctl restart openshop.service**。只切 symlink 后 systemctl start 对已运行进程是 no-op；本次演练真实踩过，保留记录。
5. 等 ExecStartPost 健康检查 PASS；核 /api/health 版本、/api/ai/status、公网浏览器。
6. 失败则按回滚操作恢复旧 release 并 restart；必要时恢复数据备份。

## 回滚

已执行一次代码回滚演练（2026-10-13 08:50）：

- service stop → 一致数据备份
  /var/backups/openshop/rollback-20261013T085046/openshop-data.tar.gz
  sha256 903c7f29dca90c8643d9fff8ac9511c4941d97ec8c4bb112979d60da868a7987；
- current → /opt/openshop/releases/0.1.2，start 后公网健康 version=0.1.2，
  /api/ai/status 404（旧版无 AI，符合预期），首页仍可用；
- 切回 0.2.0 时第一次忘了 restart，symlink 已变但进程仍为 0.1.2；
  随后 systemctl restart，公网恢复 0.2.0、AI live。该人为操作失误未隐瞒。
- 数据 schema 与购物数据在切换中保留；演练动作完成后服务 0.2.0 active。

规范化步骤：

    systemctl stop openshop.service
    tar -C /var/lib -czf <backup>/openshop-data.tar.gz openshop
    # 记录 unit、current target、journal
    ln -s /opt/openshop/releases/0.1.2 /opt/openshop/current.next
    mv -Tf /opt/openshop/current.next /opt/openshop/current
    systemctl start openshop.service
    # 验证；切回新版本同样必须 restart
    systemctl restart openshop.service

若 schema 不兼容，不要盲跑旧代码；保持停止并恢复数据备份。

边缘回滚（本次未执行，维护窗口执行）：

    sudo -n -u zyj2 bash ops/oss-alt-web/apply-edge-block.sh openshop 192.168.1.183 --rollback
    sudo -n -u zyj2 bash ops/oss-alt-web/apply-edge-block.sh openshop 192.168.1.184 --rollback

使用逐机最近 Caddyfile.bak.*；回滚前先验证目标备份、保留输出并重新探公网。

## 发布后证据

已保留/需继续保留：

- commit/tag、归档、SHA-256、release-summary；
- 单元/并发/安全/门禁/浏览器原始 verdict；
- DNS Describe 前后证据、证书 SAN/有效期、两机 Caddy 备份与 validate 输出；
- unit/EAI env 配置摘要（不含密钥）、IP 白名单、两机 PID 和 public health；
- 公网完整购物 E2E、AI 浏览器、AI API 证据；
- 回滚数据备份 SHA、切换/restart 输出；
- 外部 Git 两仓的创建 receipt、push receipt、匿名 refs 复核、GitHub Actions run/job 终态；
- 外部发布失败/修复首因、token 未泄漏扫描、只读 rollback-plan 输出。

## 明确限制 / 未覆盖

- 外部 Git 已发布到新建公开仓；GitHub Actions 逐 job 结果以 Actions 页面和外部证据为准，不用本静态文档冒充。
- 本页/本站归档不包含 token；两个 token 文件与现有 credential helper/SSH 未变更。
- 只跑了单机单进程；没有容量、多机、分布式限速/会话或灾备证明。
- Caddy 边缘回滚没有执行；模型供应商网络/配额是外部依赖。
- 所有商品、支付、物流均为演示。
