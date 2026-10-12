# 发布与回滚流程

## 当前状态（务必先读）

- 目标版本：`v0.1.1`，**本地源码验收版**。22 个标准库测试、三轮并发、真实 Chromium 验收、仓库门与发布演练负控已通过；详见 [testing.md](testing.md) 和 [RELEASE-REHEARSAL.md](RELEASE-REHEARSAL.md)。
- **未公开部署、未 push、未在远程 GitHub 运行 CI，也未安装/演练 systemd 服务。** 本地源码 tag/归档不等同于生产发布。
- OpenShop 是虚构数据 + 模拟支付的非生产级参考实现。不能用本地启动、HTTP 200 或“服务还活着”代替发布验收。
- 任何公开部署、DNS、证书、反向代理、生产入口、远端推送或生产数据变更均需项目负责人的**明确授权**，不因本地验收通过而自动获得。

发布前必须逐条完成下列门槛并留下可复核原始输出。门禁或测试失败时只能修问题、等待产品完成或取消发布；不得放宽检查求绿。

## 版本与负责人

| 项 | 要求 |
| --- | --- |
| 版本 | 语义化版本，本次候选 v0.1.1；必须与 `APP_VERSION`、页脚、CHANGELOG、tag 和文档一致 |
| 发布提交 | 维护者指定的集成分支上经审阅的固定 commit SHA，不能是未提交工作树 |
| 发布负责人 | 由维护者在明确授权下指定，负责签署/批准部署与回滚决策 |
| 发布窗口 | 明确开始/结束时间、影响面和回滚点；不得与无关生产变更混做 |

## 发布前门槛

1. 所有产品变更已合入指定集成分支；没有未合入的实现被 README 或发布说明宣称完成。
2. 工作树干净，无运行数据库、日志、截图临时件、`__pycache__/` 或未跟踪凭据混入归档。
3. 固定提交上原样运行：

```bash
git status --short
git log -1 --format="%H %s"

python3 -B -m unittest discover -s tests -v
python3 -B tests/test_trade_concurrency.py
python3 -B tools/check_repository.py --selftest
python3 -B tools/check_repository.py --check
```

4. 上述命令的原始 verdict 全部成功；三套测试的真实 HTTP 负控、三轮并发和仓库门正/负控必须通过，不能因白名单被“调绿”。
5. CI 配置覆盖标准库测试、三轮并发、仓库门自检与门禁。**本轮只在本机按相同命令模拟全绿，尚未在远端 GitHub 实际触发 CI**；生产发布前仍必须在目标提交、目标远端留下可追溯的 CI 成功记录。
6. 主路径浏览器验收已完成并留存真实 Chromium、13 张截图和 0 错误证据；生产发布前仍需由授权验收人补做独立的键盘/无障碍与部署验收。标准库门禁不能替代浏览器/部署验收。
7. 用新的数据目录做一次从零启动烟雾检查，健康端点返回 200/`ok` 和 `demo:true`；日志里无 unexpected traceback。
8. 文档中所有本地链接有效；许可证保持 Apache-2.0；商品/人物均为虚构、支付标记为模拟、没有上线或 512 并发等未经证实声明。
9. systemd unit、健康检查脚本、安装与回滚步骤需完成安全评审，并在 staging/等效环境演练并记录原样输出。本轮未完成这一部署演练。

任何一项不满足即停止发布。

## 制品

- 本轮可以并已经做的是**本地源码封版**：在固定 `master` commit 上创建本地 annotated tag、生成 `git archive` tar 包与 SHA-256，并在隔离目录复验测试、门禁、登录与健康。它不构成远程 GitHub release 或线上部署。
- 没有上传任何制品、没有修改远端仓库、没有生成生产 systemd release。
- 生产发布只能从同一个固定 commit 重新构建并保存原始输出，不得以本地脏树代替：

```bash
git status --short
git tag -a v0.1.1 -m "OpenShop v0.1.1"
git archive --format=tar.gz --prefix=openshop-0.1.1/ -o /path/to/openshop-0.1.1.tar.gz v0.1.1
sha256sum /path/to/openshop-0.1.1.tar.gz > /path/to/openshop-0.1.1.tar.gz.sha256
tar -tzf /path/to/openshop-0.1.1.tar.gz
```

- 在干净目录校验 SHA-256 后解包，再跑发布前门槛中的测试与门禁。
- 检查归档只包含该版本跟踪的源码/文档，不包含数据库、日志、凭据、缓存或临时证据。
- 不要为了赶时间从临时准备分支直接发布；发布提交必须经过集成和回归。
- 生产发布前还必须让远端 CI 在该固定 commit 成功、完成部署与回滚演练，并由授权负责人批准。

### 本地演练工具

标记完成后可运行 [tools/rehearse_release.py](../tools/rehearse_release.py)。它只操作本地文件与 loopback：

```bash
# 负控自检：不需要 release tag / Node / Chromium
python3 -B tools/rehearse_release.py --selftest --out-dir /path/to/empty-selftest-evidence

# 真实源码演练：要求干净树和指向 HEAD 的 annotated tag
python3 -B tools/rehearse_release.py --tag v0.1.1 --out-dir /path/to/empty-release-evidence
```

该工具会验证工作树干净、tag 是 annotated 且指向 HEAD、归档边界无脏文件，计算 SHA-256 并写出文件清单，从解包树重新跑标准库测试和仓库门，再从解包树启动真实服务并检查 `/healthz`、`/api/health`、`/api/session`→demo buyer 登录→目录 API、`/api/home` 与首页 shell，干净停止后写出 `release-summary.json` 与原始日志。**它不会 push、上传、安装 systemd 或修改任何网络入口。**

负控范围、证据、失败语义和未覆盖的部署/回滚边界见 [docs/RELEASE-REHEARSAL.md](RELEASE-REHEARSAL.md)。

## 部署

完整安装与回滚样例见 [deploy/README.md](../deploy/README.md)。部署阶段的强制约束：

- 只操作 `openshop.service`；不得改动任何现有 oss-* 服务、Caddy、DNS 或证书配置。
- 代码释放到 root 所有、只读的 `/opt/openshop/releases/0.1.1`，通过 `/opt/openshop/current` 切换；非 root `openshop` 账号运行，只监听 `127.0.0.1:8780`。
- 数据目录外置为 `/var/lib/openshop`。升级前做一致性备份，保留当前 unit、旧 release 和日志。
- 公网 HTTPS 终结由获授权运营者在独立变更中提供；服务保持回环监听。
- 部署不是本发布流程自动执行的步骤。任何命令执行前必须完成授权和备份。

## 回滚

回滚同样需要明确授权，并且要区分“只回代码”和“数据也需要恢复”：

1. 停止 `openshop.service`，记录当前 commit/unit/数据和 journal。
2. 备份 `/var/lib/openshop`；不要只复制正在写入的 SQLite 主文件而忽略 WAL。
3. 若数据仍然兼容，按 deploy/README.md 原子地把 `/opt/openshop/current` 指向已知旧版本，重启该 unit 并重新做健康检查。
4. 若数据库/schema 不兼容，保持服务停止，恢复已校验的数据备份；不要盲跑旧代码去碰新数据。
5. 在事件记录中写清触发条件、命令、原始输出、恢复点、残留风险和后续修复负责人。

回滚演练尚未执行。没有自动迁移保证，也没有生产 SLA。

## 发布后证据归档

至少保留以下内容，访问权限要限制且不得含凭据：

- 发布 commit SHA、tag、对应集成分支与审批记录；
- CI 原样输出；标准库测试、`--selftest`、`--check` 的原始 verdict；
- 浏览器验收记录（命令/截图/DOM 或人工步骤与结果）；
- 制品 SHA-256、校验结果、归档文件清单；
- 从零启动健康输出、部署后健康输出、unit 配置与 journal 摘要；
- 回滚点、数据备份位置与恢复演练结果；
- 未覆盖边界和已知限制。

只有上述证据完整且版本负责人明确批准后，才能把版本称为已发布。“已在本地跑起来”不等于已发布；未经实际授权和证据，不得写线上地址、上线时间或生产可用性。

## 本次准备交付的边界

本次交付可运行的源码、22 个标准库测试、三轮并发验收、真实浏览器验收证据、开源文档、CI 配置、仓库门与发布演练工具/负控。Lead 会完成本地源码 tag、归档、SHA-256 与解包启动复验；但**没有**公开上线、push 远端、运行远端 CI、安装 systemd unit、配置 DNS/TLS/Caddy、接触生产凭据或执行回滚。生产发布与回滚仍待明确授权、操作负责人和演练窗口。
