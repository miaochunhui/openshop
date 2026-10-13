# 公开代码仓库与远端 CI

更新：2026-10-13（Asia/Shanghai）

OpenShop v0.2.0 的源码已发布到两个独立公开代码仓库：

- **GitHub**：<https://github.com/miaochunhui/openshop>
  - **GitHub Actions CI**：<https://github.com/miaochunhui/openshop/actions/workflows/ci.yml>
- **Gitee**：<https://gitee.com/sdfwefqaa/openshop>

固定发布 tag 为 **v0.2.0**，tag target
`94c93261e637caa4f86669fc059edf5288bd27fe`。GitHub/Gitee 的 master
还会包含发布后的事实更新和发布工具；tag 对象本身不移动。公开只读源码归档
仍保留在 <https://openshop.ai4everyone.top/web/source/openshop-0.2.0.tar.gz>。

## 远端 CI

GitHub Actions 工作流位于 `.github/workflows/ci.yml`，由 master/tag 链路中的
GitHub push 触发，执行：

1. 标准库单元测试（29 项，含 AI fake-provider 真实 HTTP/HTTPS 边界之外
   的本机 HTTP 集成测试与可信代理解析）；
2. 三轮、七场景真实 HTTP 并发验收；
3. 44 探针独立 API 安全审计；
4. 仓库门自检与当前树仓库门。

运行记录和逐 job 结论以 GitHub Actions 页面为准；本仓库不把静态文字当作
CI 通过证明。

## 发布工具与凭据边界

发布使用 [tools/publish_public_repos.py](../tools/publish_public_repos.py)：

- 默认 dry-run；远端写操作必须显式传 `--apply`；
- 目标 owner/repo 与 release tag target 均写死在工具中，不能通过参数指向
  其他仓库；不 push 其他分支，不 force、不删除；
- 两个 token 由部署操作者通过外部文件路径提供，只在该进程内读取，绝不写入
  Git、Git config、URL 参数日志、报告、截图或交接；工具不会修改 token、
  现有 GitHub credential helper、Gitee SSH 登录或全局 Git 配置；
- GitHub 的 HTTPS 认证只通过一条命令级、repo-scoped credential helper
  pipe；命令退出即无持久化认证设置；
- 推送前对当前 master/tag 可达的全部 Git 对象和工作树文件扫描两个 token
  的字面值，任一命中即中止；
- 只创建新的目标仓；若目标已存在则在创建阶段拒绝覆盖。

Gitee 服务端不允许**空仓库**设为公开（实测会返回
`空仓库不支持设置为公开仓库`）。因此初始化流程必须：

1. 创建公开的 GitHub 空仓；
2. 将 master + v0.2.0 推到仍是 **private** 的新 Gitee 仓；
3. 推同样的 refs 到公开 GitHub 仓；
4. Gitee 有内容后，才对其执行带 id/权限/空仓前置校验的 visibility PATCH；
5. 对两端做匿名 Git refs 与公开性复核。

以上由 `launch` 子命令一次完成；失败即停并保存首因，不隐式重试。
本页不记录任何 token 路径或 token 值。

## 回滚

两个仓库都是 **本次新建** 的公开仓，发布前状态和目标 id 已记录在外部
create receipt 中；既有 GitHub/Gitee 仓库没有被修改。若需要撤销外部公开：

- 可将这两个新仓在 provider 设置中改回 private 或删除；这不会回滚本机
  Git 历史、v0.2.0 tag、已部署的公网站点或本站源码归档；
- `rollback-plan --create-receipt <path>` 会只读核对 live repo id、权限和
  create receipt，并输出逐 provider 的回滚计划，不执行删除；
- 发布后如仅需更新 master 文档，可走同一工具的 fast-forward-only `push` 路径；
  它同样不做 force、不删除 refs。

本页只说明公开入口；现读操作证据、每次运行的 receipt 与失败记录位于
操作者的外部证据目录，不在本 Git 仓中。
