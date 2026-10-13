# 发布演练与负控

本页说明 [tools/rehearse_release.py](../tools/rehearse_release.py) 的本地范围、复跑命令、负控、证据和明确不做的事。它不是公网发布记录，也不替代 [docs/RELEASE.md](RELEASE.md) 中的授权、远端 CI、systemd 和回滚门槛。

## 目标与非目标

**目标**：从固定 annotated tag 生成可审计的源码归档，在**解包后的树**上重新执行测试与仓库门，再启动解包服务检查真实 HTTP 路径；同时用临时夹具证明工具会在脏树、坏 tag、危险归档和不健康服务上 fail closed。

**非目标**：push、远端 CI、公网入口、DNS/TLS/Caddy、systemd 安装、生产数据、真实支付/物流、自动回滚或 SLA。这个本地 rehearsal 工具本身不执行这些动作；实际外部源码发布和 GitHub Actions 由 [docs/repositories.md](repositories.md) 描述的独立流程负责。

## 负控自检

不需要 tag、Node 或 Chromium，只使用临时目录：

```bash
python3 -B tools/rehearse_release.py --selftest \
  --out-dir /path/to/empty-evidence
```

通过时应输出 `REHEARSAL_SELFTEST verdict=PASS` 并写入 `release-selftest-summary.json`。10 个夹具控制：

| 控制 | 预期 |
| --- | --- |
| 干净临时 Git 树 | 接受 |
| 未跟踪文件造成的脏树 | 拒绝 |
| annotated tag 且指向 HEAD | 接受 |
| tag 指向历史 commit、不在 HEAD | 拒绝 |
| lightweight tag | 拒绝 |
| 不存在的 tag | 拒绝 |
| 边界完整的安全 tar fixture | 接受 |
| 含 `openshop.sqlite3` 的运行产物归档 | 拒绝 |
| 含 `../` 路径的归档 | 拒绝 |
| 未启动服务的 loopback 端口 | 拒绝（健康检查超时） |

同时由 [tests/test_release_rehearsal.py](../tests/test_release_rehearsal.py) 验证这 10 个控制必须全部出现且通过。任何负控未触发时工具会返回 1，并在 `selftest-first-failure.txt` 留下首个 traceback，而不是把红项当绿。

本轮自检证据（2026-10-13）：

```text
/hf3fs-jd/data/tmp/openshop/lead/final2-release-selftest-20261013T013142/
  release-selftest-summary.json
controls=10  verdict=PASS
```

## 真实本地源码演练

需要：干净工作树、annotated tag 指向当前 HEAD、系统 Node（仓库门使用）、本机空闲 loopback 端口。示例：

```bash
VERSION=vX.Y.Z   # 例如一个尚未创建的候选 annotated tag
git status --short
git tag -a "$VERSION" -m "OpenShop $VERSION"
python3 -B tools/rehearse_release.py --tag "$VERSION" \
  --out-dir /path/to/empty-release-evidence
```

工具会 fail closed 地执行：

1. 验证工作树干净、tag 为 annotated 且指向 HEAD；
2. 用 `git archive` 生成带版本前缀的 tar.gz，计算 SHA-256 并写出文件清单；
3. 审计归档路径边界、必需文件、危险后缀、运行数据库/日志/凭据形状，再解包；
4. 在解包树运行 `unittest discover`、仓库门 selftest 与 `--check`；三套后端测试会获得隔离的临时端口，避免和并行 agent/开发者占用的 8861/8862/8865 冲突；
5. 用新数据目录在 loopback 启动解包服务，等待 `/healthz`，检查 `/api/health`（`demo=true` 且服务版本与 tag 一致）、`/api/session`→demo buyer 登录→`/api/products`、公开首页与 `/api/home`；
6. 干净停止服务并扫描 server log 无 traceback；
7. 写出 `release-summary.json`、`archive-manifest.txt`、SHA 文件和各类原始日志。

[tests/test_release_rehearsal.py](../tests/test_release_rehearsal.py) 已进入标准库测试集合，CI 的 `unittest discover` 会执行对应负控。

预期判定行的形态：

```text
REHEARSAL verdict=PASS tag=<tag> commit=<sha> sha256=<sha> checks=10 out=<dir>
```

`release-summary.json` 中 `published=false` 是事实声明：本工具从不发布。它不是 GitHub Release、线上部署或“安全审计通过”的证明。

## 限制

- 没有实现/执行 systemd 安装、线上切换、第二个 commit 的 switch-back 或数据不兼容回滚；这些需要部署授权和单独演练。
- 只在单机、loopback、单进程 SQLite 上跑一次；没有压测、故障注入或容量承诺。
- 不验证远端 CI，不代表所有 GitHub Actions 配置已在目标远端成功。
- 不接触凭据、DNS、证书、反代或其他生产对象。
- 完整浏览器 E2E 由 [docs/testing.md](testing.md) 中的 QA harness 负责；本工具只为“解包后可启动”提供额外真启动检查。

本页与工具只记录可以现读的本地事实；任何“已发布/已上线”声明仍必须等明确授权、部署和线上复验。
