# 公网部署与回滚记录

更新时间：2026-10-13 08:55（Asia/Shanghai）
公网入口：https://openshop.ai4everyone.top/
运行版本：0.2.0
阶段：**已公网运行，滚动切换已演练；外部 Git 托管/远端 CI 未执行。**

本文只记录**已执行**的生产变更与可复核证据，不包含模型 API key、cookie 或任何凭据。规范说明与未覆盖边界在文末。

## 拓扑

    Internet (43.135.70.136:443, TLS 终止)
      -> gitlab-edge Caddy，192.168.1.183 / 192.168.1.184 两台上游
           openshop.ai4everyone.top:18445 {
             bind 127.0.0.1
             tls /opt/docs-site/runtime/tls/openshop.crt|key
             header Strict-Transport-Security max-age=31536000
             reverse_proxy http://192.168.1.30:8790
           }
      -> hadoop6 192.168.1.30:8790
           openshop.service (User=openshop, Group=openshop)
           /opt/openshop/current -> /opt/openshop/releases/<fixed-release>
           /var/lib/openshop/openshop.sqlite3 (StateDirectory, 0700)
      -> 仅服务端出站：OpenAI-compatible 模型网关

应用对公网的监听不是无限制的：systemd 为 openshop.service 配置
IPAddressDeny=any 与显式 IPAddressAllow，仅允许回环（健康检查）、两台上游
边缘 192.168.1.183/184、模型网关 192.168.208.213 与 DNS 解析器
192.168.208.223 / 114.114.114.114。边缘两个 IP 同时被列为可信反代；
应用只从这些 TCP peer 接受 X-Forwarded-For，以免伪造头绕过按 IP 的限速。

实测：在 hadoop6 用其 LAN 地址访问 192.168.1.30:8790 超时（拒绝非白名单
源）；从 .183 与 .184 直连 /healthz 均 200。公网请求经 Caddy 进入，XFF 被
应用按真实客户端解析。健康检查和 systemd ExecStartPost 走 127.0.0.1。

## 发布对象

- DNS：openshop.ai4everyone.top A 43.135.70.136，DNSPod RecordId
  2429400643。先 dry-run 再 apply；工具在**初次 Describe、创建后再 Describe**
  两次记账，公共 DNS 与权威查询一致。
  证据：/hf3fs-jd/data/tmp/openshop/deploy/dns-20261013T081753/
- TLS：Let’s Encrypt，CN/SAN 均为 openshop.ai4everyone.top，issuer
  Let’s Encrypt YE1，notBefore 2026-10-12T23:22:02Z，notAfter
  2027-01-10T23:22:01Z。证书与私钥在 .183 和 .184 两台的
  /opt/docs-site/runtime/tls/openshop.crt|key；证书 644、私钥 600
  且属主 docsweb。使用既有 r640-cert-install.py 与 NO_RESTART=1 签发/推送，
  没有在其他边缘服务窗口执行重启。
  R640_CERT_OK installed=1 skipped=0 failed=0 peers=192.168.1.184。
- Caddy：源片段已落入
  /data/dev/infra/ops/oss-alt-web/edge-block-openshop.txt。
  2026-10-13 08:39:07 对 .183、08:39:27 对 .184 执行：
  先 Caddy validate（均为 VALIDATE_OK）、备份现网配置、安装、SIGUSR1
  热重载。两个边缘 gitlab-edge 主 PID 均未改变，服务保持 active。
  两机现网 Caddyfile 的 sha256 同为
  9f3589b61d79bd6ee1f207a429171188。
  线上备份：
    /etc/gitlab-edge/Caddyfile.bak.20261013T083907  (.183)
    /etc/gitlab-edge/Caddyfile.bak.20261013T083927  (.184)
  干跑/应用输出：/hf3fs-jd/data/tmp/openshop/deploy/edge-verify-20261013T083842/
- systemd：安装 openshop.service，非 root 专用用户；服务只读 release 树，
  外置 SQLite；AI key 不在环境文件，通过 systemd LoadCredential 从
  /etc/openshop/openshop_ai.token 加载；非秘密 AI 配置在
  /etc/openshop/openshop.env（0600）。密钥源文件 0600，只有 manager 读取。
- 当前 release：/opt/openshop/releases/0.2.0-candidate-cd08847c3e2b
  （最终 v0.2.0 tag 的归档在发布收口后使用同一代码安装到固定 release 目录）。
  /opt/openshop/releases/0.1.2 保留为已验证回滚目标。
- 文件哈希、归档和最终 tag 信息见同日最终交接与 release record。

## 在线验收证据

- 首装服务（2026-10-13 08:37:10）：active，ExecStartPost
  HEALTHCHECK verdict=PASS url=http://127.0.0.1:8790/healthz；
  公网 /api/health 返回 version=0.2.0。
- 公网完整购物 E2E（真实 Chromium，无 mock）：
  11/11 PASS，13 截图，0 console/page/external/api errors。
  搜索→商品→购物车→登录合并→新地址/优惠券→服务端报价→
  模拟支付→商家发货→买家确认收货→评价，以及 390px 移动订单。
  订单 OS20261013004340PLSF。
  证据：/hf3fs-jd/data/tmp/openshop/deploy/public-full-e2e-20261013T084320/
- 公网 AI 浏览器：
  真实 deepseek-flash，登录→AI 委托→服务端复价→采纳；
  17.3 秒模型调用、1 次模型请求、Secure/HttpOnly Cookie、HSTS/CSP，
  桌面 1440 与移动 390 无溢出，0 console/page/external errors。
  服务日志来源为边缘 IP 192.168.1.183；没有直接暴露应用端口。
  证据：/hf3fs-jd/data/tmp/openshop/deploy/public-ai-browser-20261013T084044/
- 公网 AI API 直连：
  CSRF 403；未登录 401；真实模型 200；一次性采纳；
  重复采纳 409；未创建订单；脚本以 HTTPS 安全 Cookie 全链路运行。
  证据：/hf3fs-jd/data/tmp/openshop/deploy/public-ai-api-evidence-20261013T084504/api-live.json
  注：一次相同流程已完成但在写证据文件时因目录不可写退出（产品请求本身成功），
  已保留此运维失败并用可写目录重跑得到上面结果。
- 本轮完整本地门：29/29 标准库、3×7 并发、44/44 安全探针、
  API 35/35、Chromium 11/11、仓库门 PASS。对应日志：
  /hf3fs-jd/data/tmp/openshop/lead/v020-final-unit-20261013T083052.log
  /hf3fs-jd/data/tmp/openshop/lead/v020-final-concurrency-20261013T083052.log
  /hf3fs-jd/data/tmp/openshop/lead/v020-final-security-20261013T083052.log
  /hf3fs-jd/data/tmp/openshop/qa/final-v020-20261013T083310/

## 回滚演练（真实执行，含一次操作失误）

- 2026-10-13 08:50:46 停止 openshop.service；保留一致的数据快照：
  /var/backups/openshop/rollback-20261013T085046/openshop-data.tar.gz
  SHA-256 903c7f29dca90c8643d9fff8ac9511c4941d97ec8c4bb112979d60da868a7987
  同时保存切换前 unit 副本。
- 将 /opt/openshop/current 原子切换到 /opt/openshop/releases/0.1.2，
  启动后：
    local/public /api/health -> version 0.1.2；
    GET /api/ai/status -> 404，符合旧版无 AI 路由的预期；
    gitlab-edge 未变，浏览首页仍可用。
- 切回 0.2.0 时，第一次只切换符号链接并调用 systemctl start；服务并未退出，
  start 因此是 no-op，公网仍返回 0.1.2，AI 仍为 404。**这是操作失误，不是产品通过。**
  随后执行 systemctl restart，公网恢复 version=0.2.0、AI mode=live。
- 结论：代码回滚目标可用，但回滚/前进都必须显式 restart（或 stop 后 start），
  不能仅切 symlink；该要求已写进部署步骤。
- 边缘本身有独立回滚入口，本次没有执行（避免无谓改动两台上游）：
    sudo -n -u zyj2 bash ops/oss-alt-web/apply-edge-block.sh openshop 192.168.1.183 --rollback
    sudo -n -u zyj2 bash ops/oss-alt-web/apply-edge-block.sh openshop 192.168.1.184 --rollback
  各自使用逐机最近 Caddyfile.bak.*；应在维护窗口执行并保留输出。

## 更新步骤（固定版本）

1. 在干净 master 上完成代码、测试、文档提交；创建 annotated tag。
2. 运行 tools/rehearse_release.py --tag <tag>，确认归档自检、解包单测/仓库门、
   启动健康、demo 登录/目录和干净停止全部 PASS；保存 release-summary.json。
3. 将归档解到新的 root:root 只读 /opt/openshop/releases/<tag>；不要原地覆盖旧树。
4. 若 unit 有变化，先 systemd-analyze verify，再安装并 daemon-reload；
   保持 /etc/openshop 下的 secret 不进 Git。
5. 原子切换 current -> 新 release，**systemctl restart openshop.service**；
   等 ExecStartPost 健康检查通过，再核公网 /api/health 版本与 /api/ai/status。
6. 生成并上传与该 tag 一致的源码离线包（当前为
   /web/source/openshop-0.2.0.tar.gz）及其 .sha256；源码包不含运行数据库和凭据。
7. 记录公网证据；出现任何失败按上一节回滚。

## 外部 Git / CI 边界

本次没有创建外部 GitHub/Gitee 新仓库，也没有 push 或远端 CI。现有 GitHub
凭据被既有工具限制在 miaochunhui/openmath 私有仓，Gitee SSH 只对既有仓库
有写权限，此次没有获得新建公开 OpenShop 仓库的授权目标/凭据。没有读取或
绕过这些凭据。代替形态是本站公开只读源码归档和本地 annotated tag/Git 历史。
若维护者提供新的公开仓库目标或授权链，可补做 push、远端 CI 现读核验和发布链接。

## 这个部署不是生产级保证

单进程 SQLite、单实例应用、共享上游模型配额、无多实例会话/限速、无正式
容量/SLA、无独立 TLS/反代生产审计、未部署自动拼团到期退款；模型供应商依赖
外部网络与配额。OpenShop 的所有商品/人物/支付/物流仍为虚构或模拟。
