# 安装与本地运行

本页只覆盖**本地/受控环境**运行 OpenShop 的复现步骤。项目尚未发布上线；生产安装、TLS 入口、服务常驻和回滚仍待独立授权，模板见 [deploy/README.md](../deploy/README.md)，发布门槛见 [docs/RELEASE.md](RELEASE.md)。

## 前置条件

| 项 | 要求 |
| --- | --- |
| Python | 3.10+，含 `http.server`、`sqlite3` 标准库 |
| 第三方 Python 包 | 不需要，也不应安装 |
| Node/npm | 运行产品不需要；只有仓库门的 JS 语法检查需要系统 `node` |
| 数据目录 | 仓库外部的独立目录（会生成 `openshop.sqlite3`） |
| 网络 | 本地运行不需要外部网络或 CDN |
| 端口 | 文档与部署样例固定使用本机回环 `127.0.0.1:8780` |

检查 Python：

```bash
python3 --version
```

## 本地启动

从该版本源码根目录执行。显式传入 `--data-dir` 和固定 `--port`，避免误用默认目录或不明端口：

```bash
python3 -B apps/storefront/server.py --host 127.0.0.1 --port 8780 --data-dir "$HOME/.local/share/openshop-demo"
```

首次启动会创建数据目录、建表并从 `data/catalog.json` 幂等播种。之后重复启动同一个数据目录会复用现有数据库；不要把数据库、WAL/SHM 文件或种子锁文件提交进 Git。

请在浏览器打开 <http://127.0.0.1:8780/>。前台运行时用 Ctrl-C 停止；不要在公网接口直接暴露这个开发服务。

如果服务因 seed mismatch 退出，说明该数据目录已经属于不同的种子/版本。保留原目录，改用一个新的空目录复现；不要在不清楚内容的情况下删除数据库。

## 健康检查

```bash
curl -fsS http://127.0.0.1:8780/healthz
curl -fsS http://127.0.0.1:8780/api/health
```

预期依次为纯文本 `ok`、以及包含 `"ok":true` / `"demo":true` 的 JSON。若失败，先查看启动终端日志；报告问题时删除任何 Cookie、CSRF token 或个人信息。

## 演示账号

入口是页面右上角的登录浮层“一键演示”。种子账号没有需要用户输入的密码；所有身份都是虚构的：

| 角色 | handle | 用途 |
| --- | --- | --- |
| 买家 | `demo_buyer` | 购物车、地址、模拟结算、订单与评价 |
| 第二买家 | `demo_buyer_two` | 拼团第二人验证 |
| 商家 | `demo_seller` | 自己店铺的库存、发货与售后 |

不要录入真实手机号、地址、姓名、邮箱或凭据。演示支付不是真实扣款。

## 配置项

命令行参数优先；仅本地开发时才建议用环境变量。始终显式确认数据目录和端口。

| 参数 / 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `--host` / `OPENSHOP_HOST` | `127.0.0.1` | 保持回环地址；公网入口必须由授权的反向代理提供并评审 |
| `--port` / `OPENSHOP_PORT` | `8780` | 固定非 0 端口；部署 unit 中不得写成 0 |
| `--data-dir` / `OPENSHOP_DATA_DIR` | 用户本地目录 | 必须显式指定为仓库外目录；部署用 `/var/lib/openshop` |
| `--catalog` / `OPENSHOP_CATALOG` | `data/catalog.json` | 只使用仓库内虚构种子 |
| `--log-level` / `OPENSHOP_LOG_LEVEL` | `INFO` | 终端/journal 日志级别 |
| `OPENSHOP_SECURE_COOKIES` | 未设置 | 在已授权的 HTTPS 终结场景设为 `1`；本地 HTTP 开发不要开启 |

生产 unit 通过 systemd 的 `StateDirectory=openshop` 把状态放在 `/var/lib/openshop`，并设置 Secure Cookie；公网使用前必须有 HTTPS 终结，相关入口变更不在本任务范围内。

## 测试与仓库门

```bash
python3 -B -m unittest discover -s tests -v
python3 -B tools/check_repository.py --selftest
python3 -B tools/check_repository.py --check
```

- 这些测试不需要网络，也不安装依赖。
- `--selftest` 用临时正/负控 fixture 验证门禁本身；`--check` 对当前树执行真实检查。
- 门禁需要系统 Node 才完整检查 JS；Node 缺失会明确失败，不会跳过。
- 未完成树上的门禁可以是红的。发布前必须让它变绿的方式是修缺件；禁止放宽规则或删检查来伪造通过。
- 当前分支若没有 `tests/` 或仍有占位模块，测试/门禁会失败；这本身就是发布阻断证据。发布检查见 [docs/RELEASE.md](RELEASE.md)。

## 生产与回滚

**不属于本地安装步骤。** 任何生产发布必须由负责人书面授权，按 [deploy/README.md](../deploy/README.md) 的模板评审、备份、原子切链与健康检查流程执行。当前仓库任务没有安装 systemd unit，也没有触碰现有 oss-* 服务、Caddy、DNS 或证书。

数据是单个 SQLite 数据库；备份应在适当时机（例如由运营者停止服务后）对外置数据目录做一致性快照，并在升级前确认 schema 兼容性。没有自动回滚、迁移或生产 SLA 承诺。安全边界与支持范围见 [SECURITY.md](../SECURITY.md)。
