# 部署模板（仅供审阅，未执行）

> **本目录只包含模板和文档。** 没有在本任务中安装 unit、创建系统用户、启动服务、修改数据、改反向代理或发布版本。任何生产安装、变更窗口和回滚演练都必须由具备授权的负责人批准。严禁用本模板触碰现有 oss-* 服务、Caddy、DNS 或证书。

本模板把 OpenShop 作为**非 root、只监听回环地址、只读代码树、外置数据目录**的 systemd 服务运行。对外 TLS、域名和反向代理不在模板范围内，必须单独评审和授权。

## 文件

| 文件 | 用途 |
| --- | --- |
| [systemd/openshop.service](systemd/openshop.service) | 固定版本 unit 模板；固定 127.0.0.1:8780，数据目录 /var/lib/openshop |
| [healthcheck.py](healthcheck.py) | 仅回环、禁止重定向的启动健康检查，unit 的 ExecStartPost 会调用 |
| 本文档 | 安装、更新、回滚与授权边界样例 |

## 固定部署形态

| 项 | 固定值 |
| --- | --- |
| 服务 unit | `openshop.service`（不要改 oss-* unit） |
| 发布树 | `/opt/openshop/releases/0.1.2`，当前符号链接 `/opt/openshop/current` |
| 代码所有者 | `root:root`，组/其他只读，运行时目录只读 |
| 服务账号 | `openshop` 系统用户/组，nologin，无 sudo |
| 监听地址 | `127.0.0.1:8780`，固定非 0 端口，仅本机可达 |
| 数据目录 | `/var/lib/openshop`（systemd StateDirectory，0700，外置于发布树） |
| 数据库 | `/var/lib/openshop/openshop.sqlite3` |
| 日志 | journald，`journalctl -u openshop.service` |
| 健康检查 | `GET http://127.0.0.1:8780/healthz` 必须为 200 且响应体为 `ok` |
| HTTPS 入口 | 不在本模板内；由获授权运营者另行配置并审查 |

unit 使用 `ProtectSystem=strict`、`ProtectHome`、`PrivateTmp`、空 capability 集合、systemd StateDirectory 写入、回环网络白名单、system call filter、资源上限和 journal 日志。发布树保持只读；数据库只在外部状态目录写入。

## 前置检查（安装前，只读）

在维护窗口和书面授权之前，只做检查，不启动服务：

```bash
python3 --version
ls -ld /opt/openshop /var/lib/openshop /etc/systemd/system/openshop.service
systemctl cat openshop.service
systemd-analyze verify deploy/systemd/openshop.service
```

- Python 必须是 3.10+，并且 unit 中的 `/usr/bin/python3` 实际存在。
- 如果 `openshop.service` 已存在，先确认它属于本项目且变更已授权；否则停止，不要覆盖。
- 确认单元里的服务账号尚不存在或已经正确配置；不要复用有 sudo/登录权限的账号。
- `systemd-analyze verify` 只是静态审阅辅助，不代表安全审计或安装验证。
- 确认不修改任何 oss-* unit、Caddy 配置、DNS 或证书。这一步属于单独变更。

## 安装样例（需明确授权；本次未执行）

以下命令仅为可复核样例。执行前把路径、版本、账号和备份位置逐项核准；不要直接复制到生产主机。

```bash
# 1. 创建不可登录的系统账号。已存在时先检查，不要重复创建。
sudo useradd --system --user-group --no-create-home --home-dir /var/lib/openshop --shell /usr/sbin/nologin openshop

# 2. 把已校验的固定版本归档解到不可变发布树。
sudo install -d -o root -g root -m 0755 /opt/openshop/releases
sudo tar -xzf /path/to/openshop-0.1.2.tar.gz -C /opt/openshop/releases
sudo mv /opt/openshop/releases/openshop-0.1.2 /opt/openshop/releases/0.1.2
sudo chown -R root:root /opt/openshop/releases/0.1.2
sudo chmod -R u=rwX,go=rX /opt/openshop/releases/0.1.2
sudo ln -s /opt/openshop/releases/0.1.2 /opt/openshop/current

# 3. 在授权窗口内安装模板并启动。
sudo install -m 0644 deploy/systemd/openshop.service /etc/systemd/system/openshop.service
sudo systemctl daemon-reload
sudo systemctl enable --now openshop.service
```

systemd 的 `StateDirectory=openshop` 会创建并收紧 `/var/lib/openshop`；服务只在启动健康检查通过后才算按模板成功。不要把数据目录预置为共享或 world-writable。

## 启动后验证

```bash
systemctl status openshop.service --no-pager
systemctl show openshop.service -p User -p Group -p ExecStart -p StateDirectory -p ProtectSystem -p NoNewPrivileges
curl -fsS http://127.0.0.1:8780/healthz
curl -fsS http://127.0.0.1:8780/api/health
sudo journalctl -u openshop.service -n 80 --no-pager
```

健康输出应为 `ok`；API 健康 JSON 带 `"demo":true`。日志只写 journald，不把应用日志落到发布树中。

unit 的 `ExecStartPost` 会运行 `deploy/healthcheck.py --url http://127.0.0.1:8780/healthz --wait-seconds 10`；脚本拒绝非回环地址、非 http 和重定向，失败会让启动序列失败。systemd 具体的失败/停止行为、资源限制兼容性和重启策略必须在目标主机的获授权演练中现读验证；本任务没有运行 unit。

本模板**不包含**自动对外反代、证书签发、DNS 或 Caddy 变更。浏览器登录使用 Secure Cookie（unit 设置了 `OPENSHOP_SECURE_COOKIES=1`），所以对公网使用前必须由获授权运营者配置 HTTPS 终结；直接 HTTP 公网暴露不可接受。

## 更新样例

1. 在维护窗口前备份外置数据，记录当前 unit、当前符号链接和版本 SHA。
2. 先上传并校验新归档的 SHA-256；在 staging 或等价环境验证测试、仓库门和语法。
3. 把新版本装入新的 `/opt/openshop/releases/<version>`（不要原地覆盖旧版本）。
4. 原子切换链接并重启：

```bash
sudo ln -s /opt/openshop/releases/0.1.2 /opt/openshop/current.next
sudo mv -Tf /opt/openshop/current.next /opt/openshop/current
sudo systemctl restart openshop.service
curl -fsS http://127.0.0.1:8780/healthz
sudo journalctl -u openshop.service -n 80 --no-pager
```

5. 失败则按下方回滚流程处理并保留日志。不要删旧 release；不要在服务写入时直接覆盖 SQLite 文件。

## 回滚样例（需授权；本次未执行）

回滚分为“仅代码”与“代码加数据”两种。数据库没有自动向下兼容/逆向迁移保证；如果新版本写入过旧版本不认识的 schema/data，仅切换代码可能造成进一步损坏。务必先停止服务、保留日志与数据库副本，再由获授权负责人决定恢复范围。

```bash
# 1. 停止本服务并保留一致性数据快照。只操作 openshop.service。
sudo systemctl stop openshop.service
sudo install -d -o root -g root -m 0700 /var/backups/openshop
sudo tar -C /var/lib -czf "/var/backups/openshop/openshop-data-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" openshop

# 2. 切回已验证的旧 release（原子替换 current 符号链接）。
sudo ln -s /opt/openshop/releases/0.1.1 /opt/openshop/current.next
sudo mv -Tf /opt/openshop/current.next /opt/openshop/current

# 3. 仅在确认数据/schema 与旧版本兼容时重新启动；否则先恢复备份并复核。
sudo systemctl start openshop.service
curl -fsS http://127.0.0.1:8780/healthz
sudo journalctl -u openshop.service -n 100 --no-pager
```

回滚演练、数据恢复和生产发布都必须有明确授权与记录。若无法确认 schema 兼容性，保持服务停止并升级处理，不要用“先跑起来再说”代替判断。

## 本次任务的保守边界

- 未执行用户创建、安装、`systemctl`、健康检查（针对已安装 unit）、更新、回滚、网络或生产操作。
- 未触碰 oss-* 服务、Caddy、DNS、证书或任何生产凭据。
- 模板尚未在目标 systemd 版本上演练；不能视为“已验证上线”。
- 生产发布、TLS 入口和回滚演练仍需独立授权和现读证据。发布门槛见 [docs/RELEASE.md](../docs/RELEASE.md)，本地运行见 [docs/INSTALL.md](../docs/INSTALL.md)。
