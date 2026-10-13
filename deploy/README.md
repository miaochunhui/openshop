# 部署与运维手册

公网入口：https://openshop.ai4everyone.top/
状态与完整证据：../docs/deployment.md、../docs/RELEASE.md、../docs/STATUS.md
公开只读源码：https://openshop.ai4everyone.top/web/source/openshop-0.2.0.tar.gz

这是 **2026-10-13 已实际执行的 v0.2.0 部署形态**，不是自动部署器。
更新/回滚前必须备份并核对目标。没有修改任何 oss-* 服务，也没有把运行
数据或 API key 放进发布树或 Git。

## 文件

| 文件 | 用途 |
| --- | --- |
| systemd/openshop.service | 已安装的生产 unit；非 root、只读代码、外置 SQLite、IP 白名单、LoadCredential |
| healthcheck.py | 仅回环、禁止重定向的启动健康检查 |
| openshop.env.example | 非秘密运行时配置样例；实际文件在 /etc/openshop/openshop.env（0600 root） |

## 实际固定形态

| 项 | 值 |
| --- | --- |
| Unit | openshop.service（只操作该 unit） |
| Release | /opt/openshop/releases/<固定 tag>，root:root 只读；符号链接 /opt/openshop/current |
| 当前旧 release | /opt/openshop/releases/0.1.2（已验证回滚目标） |
| 服务账号 | openshop 系统用户，nologin，无 sudo |
| 监听 | 0.0.0.0:8790；只允许 systemd 地址白名单与两台上游访问 |
| 边缘上游 | 192.168.1.183 / 192.168.1.184 -> http://192.168.1.30:8790 |
| 数据 | /var/lib/openshop（StateDirectory，0700），openshop.sqlite3 |
| 日志 | journald，journalctl -u openshop.service |
| 公网 HTTPS | 两台 Caddy 上游，静态 Let’s Encrypt 证书，HSTS max-age=31536000 |
| AI 配置 | /etc/openshop/openshop.env（0600 root） |
| AI key | /etc/openshop/openshop_ai.token（0600 root），由 LoadCredential 注入 |

unit 网络限制：

    IPAddressAllow=127.0.0.1/32 ::1/128 192.168.1.183/32 192.168.1.184/32 192.168.208.213/32 192.168.208.223/32 114.114.114.114/32
    IPAddressDeny=any
    Environment=OPENSHOP_TRUSTED_PROXIES=192.168.1.183,192.168.1.184

这些 IP 分别覆盖回环健康检查、两台上游、AI 模型网关与系统 DNS。
若网关或 DNS 地址变化，必须同步修改白名单并验证。systemd 限制是单机
网络控制，不是 DDoS 防护；公网唯一入口是两台边缘的 openshop 子域。

只对两个边缘 IP 接受 X-Forwarded-For；应用从 XFF 链右边取第一个非可信
地址作为真实客户端 IP，用于 auth/AI 限速。擅自放宽可信代理会破坏限速可信度。

## 安装（重建/新机）

1. 校验固定源码归档 SHA，解到 root:root 只读 release 树。
2. 创建专用 openshop 系统用户。
3. 安装 unit 到 /etc/systemd/system/openshop.service。
4. 安装 /etc/openshop/openshop.env；把模型 key 以 0600 root 放到
   /etc/openshop/openshop_ai.token。key 不进入仓库、环境文件或报告。
5. systemd-analyze verify；daemon-reload；enable --now。
6. 验证 HEALTHCHECK PASS、/api/health 版本、公网 HTTPS、AI 状态；
   从两台上游 curl 直连健康 200，并从非白名单源确认连接超时。

非秘密 AI 环境示例见 openshop.env.example。实际线上限额是每账号每日 4 次、
每 IP 3 次/10 分钟、全站每日 60 次。模型网关地址只存在生产配置，不在公开仓库。

## 更新

1. 按 docs/RELEASE.md 跑全部门并生成固定 tag 与发布演练。
2. 解新 tag 到新的只读 release 树，保留旧树和 unit/current 备份。
3. 若 unit 变了，先静态 verify 再 daemon-reload；保持 secret 不落 Git。
4. 原子切换 current；随后 **必须 systemctl restart openshop.service**。
   只切 symlink 后 systemctl start 对已运行进程是 no-op（本次演练实际踩过）。
5. 验证 ExecStartPost/回环/公网健康、版本、AI 状态和首页；失败立即回滚。
6. 生成与 tag 同字节的源码 tar 和 .sha256，上传至本站 source 路径。

## 回滚

已完成一次代码回滚演练，备份与原始记录见 docs/deployment.md：

    systemctl stop openshop.service
    tar -C /var/lib -czf <backup>/openshop-data.tar.gz openshop
    ln -s /opt/openshop/releases/0.1.2 /opt/openshop/current.next
    mv -Tf /opt/openshop/current.next /opt/openshop/current
    systemctl start openshop.service
    # 验证旧版；切回 likewise

切回新版本也要 restart。若 schema 不兼容，保持停止并恢复数据备份，不要盲跑。
数据备份示例：
/var/backups/openshop/rollback-20261013T085046/openshop-data.tar.gz
sha256 903c7f29dca90c8643d9fff8ac9511c4941d97ec8c4bb112979d60da868a7987

两台 Caddy 每机已有 Caddyfile.bak.<ts>；边缘回滚命令：

    sudo -n -u zyj2 bash ops/oss-alt-web/apply-edge-block.sh openshop 192.168.1.183 --rollback
    sudo -n -u zyj2 bash ops/oss-alt-web/apply-edge-block.sh openshop 192.168.1.184 --rollback

本次没有执行边缘回滚，也没有删除旧 release/备份。

## 边界

- 外部 GitHub/Gitee 新仓、push、远端 CI **未执行**。本站只读源码归档是当前公开入口；
  既有受限凭据没有跨项目使用。需要维护者给出新的公开仓库目标或授权路径。
- 单实例、单进程 AI 方案/限额；无多实例、容量、灾备或 SLA。
- AI 依赖外部模型网络/配额；模型定性描述可能不精确；价格/库存/供给以服务端为准。
- 所有商品、人物、评价、支付与物流均为虚构/模拟；支付不产生真实扣款。
- TLS 证书到期 2027-01-10，续期由既有 acme 机制管理；不要把私钥放入 Git。
