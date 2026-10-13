# 安装与本地运行

本页覆盖**本地/受控环境**运行 OpenShop 的复现步骤，并说明已经部署的 v0.2.0 公网形态。线上入口、系统配置、回滚与证据见 [docs/deployment.md](deployment.md)、[deploy/README.md](../deploy/README.md) 和 [docs/RELEASE.md](RELEASE.md)。

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
| `OPENSHOP_TRUSTED_PROXIES` | 未设置 | 逗号分隔的可信反代 IP/CIDR；只有来自这些 peer 时才接受 X-Forwarded-For |
| `OPENSHOP_AI_ENABLED` | 由 endpoint/key 决定 | AI 中介开关；缺少任一依赖即显式 unavailable |
| `OPENSHOP_AI_BASE_URL` | 未设置 | OpenAI 兼容网关的 v1 根地址，仅服务端使用 |
| `OPENSHOP_AI_MODEL` / `_MODEL_LABEL` | deepseek-flash / DeepSeek Flash | 请求模型与页面展示名 |
| `OPENSHOP_AI_API_KEY_FILE` | 未设置 | key 文件路径；或使用 CREDENTIALS_DIRECTORY 下的 openshop_ai_token |
| `OPENSHOP_AI_*_DAILY_LIMIT` / `_IP_WINDOW_LIMIT` | 代码默认 80/6/4 | 全站/每用户每日与每 IP 窗口限额；生产值在服务器环境文件 |
| `OPENSHOP_AI_TIMEOUT_SECONDS` / `_MAX_OUTPUT_TOKENS` | 55 / 4000 | 上游超时与输出上限；推理型模型过低会返回空 content |

AI 的 endpoint、真实 key 和线上额度不应提交进 Git；生产示例见 [deploy/openshop.env.example](../deploy/openshop.env.example) 与 [deploy/systemd/openshop.service](../deploy/systemd/openshop.service)。生产 unit 通过 systemd 的 `StateDirectory=openshop` 把状态放在 `/var/lib/openshop`，设置 Secure Cookie，并用 LoadCredential 注入模型 key；公网入口必须是 HTTPS。AI 是否真实可用由 `/api/ai/status` 与真实调用验收，不以页面文案判断。

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

**生产状态**：本项目的 v0.2.0 已于 2026-10-13 部署到 https://openshop.ai4everyone.top/；实证、DNS/TLS/Caddy、systemd IP 白名单、回滚与源码归档见 [docs/deployment.md](deployment.md) 和 [docs/RELEASE.md](RELEASE.md)。重新部署必须由负责人明确授权、备份并核对目标。

数据是单个 SQLite 数据库；备份应在运营者停止服务后对外置数据目录做一致性快照，并在升级前确认 schema 兼容性。没有自动迁移或多实例部署能力，也没有生产 SLA。生产更新在切换 release 后**必须 restart**，否则运行进程仍执行旧代码（本次演练实际踩到）。安全边界与支持范围见 [SECURITY.md](../SECURITY.md)。
