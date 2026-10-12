# 变更记录

本项目处于 **v0.1.0 发布候选**阶段。下面条目记录准备内容，**不代表已经打标签、发布、部署或公开上线**。正式发布必须完成 [docs/RELEASE.md](docs/RELEASE.md) 的门槛与授权流程。

准备日期：2026-10-12。发布日期：待定，成功授权并按版本发布后才填写。

## [Unreleased]

- 集成期间修复与验收证据在维护者指定的集成分支汇合后补充。

## [0.1.0] — 发布候选（未发布）

### Added

- 全新的 [README.md](README.md)：开集定位、三种供给、真实边界、显式数据目录/固定端口快速开始、演示账号、功能/架构、测试、目录与许可。
- 开源项目治理文档：[CONTRIBUTING.md](CONTRIBUTING.md)、[SECURITY.md](SECURITY.md)、本行为准则 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) 与 [docs/OPEN-SOURCE.md](docs/OPEN-SOURCE.md)。
- [docs/INSTALL.md](docs/INSTALL.md)：Python 3.10+ 本地运行、独立数据目录、健康检查、测试与生产边界。
- [.github/workflows/ci.yml](.github/workflows/ci.yml)：只读权限的 Python 标准库测试、仓库门自检与仓库门；不访问凭据、不在 CI 中安装外部包。
- [tools/check_repository.py](tools/check_repository.py)：真实仓库门，含正/负控 `--selftest`。检查第三方运行时资源、内联脚本、秘密形态、占位残留、JSON/JS/Python/HTML 完整性、资产引用、文档链接、CSS `:root` token 重复、CI/部署策略与运行时第三方依赖。
- [deploy/](deploy/)：非 root systemd 模板、外置状态目录、只读释放树、加固选项、journal 日志、启动健康检查以及安装/回滚样例。
- [docs/RELEASE.md](docs/RELEASE.md)：固定版本候选、检查清单、制品/校验和步骤以及发布/回滚尚未执行的声明。

### Changed

- README 不再使用默认数据目录进行快速开始；文档与部署统一采用固定本地端口 `8780` 和显式 `--data-dir`。
- 明确说明项目未公开部署，不提供生产容量结论；所有演示身份与内容均为虚构，支付为模拟。

### Security

- CI 顶层权限为只读、取消持久化 checkout 凭据，不引用 secrets。
- 仓库门 fail closed：发现疑似凭据只打印文件/行号与模式名，不打印凭据值。
- 发布与部署模板要求明确授权，禁止触碰既有 oss-* 服务、Caddy、DNS 或证书。

### Known limitations / not claimed

- 不声称通过安全审计、生产压测或公网发布；真实支付、物流和身份/KYC 均不在此实现范围。
- 当前产品的完整功能、浏览器验收与测试通过必须以实际 CI/集成结果为准；本变更集只负责开源发布工程部分。
- systemd 模板未被本次任务安装或执行，回滚过程仍待授权演练。
- 本候选在尚未合入全部产品实现的树上，仓库门会如实 FAIL（缺失测试、路由/页面占位等）；不得放宽门禁求绿。

### Verification

- 仓库门自检、当前树门禁、本地烟雾测试、语法检查与原始判定输出保存在发布证据目录，并由负责人随发布审查归档。
- 许可保持 Apache-2.0，未修改 [LICENSE](LICENSE)。
