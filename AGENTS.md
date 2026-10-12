# OpenShop 开发约定

- 中文交流；一切命令使用 `sudo -n -u infra` 身份路径，不改凭据、登录方法与 harness。
- 每个并行开发席必须使用独立 worktree 与独立分支；写集互斥，Lead 负责集成与最终验收。
- 本仓外层 infra 规则仍适用：脚本先落盘再执行；禁止 inline Python/JS 执行；编辑优先补丁；临时产物外置到 `/hf3fs-jd/data/tmp/openshop/`。
- 服务只依赖 Python 标准库与 SQLite；前端零第三方运行时依赖、零外部 CDN。
- 不得声称未实测的完成；失败与限制必须留在文档与测试中。
- 提交使用按路径的 `git add`，禁止 `git add -A`/`git add .`；提交前扫敏感信息。
- 默认不推远端、不改生产入口；发布须由 Lead 在明确授权链内执行。

## 常用命令

```bash
# 标准检查（实现完成后生效）
sudo -n -u infra python3 -B tools/check_repository.py --check
sudo -n -u infra python3 -B -m unittest discover -s tests -v

# 本地服务
sudo -n -u infra python3 -B apps/storefront/server.py --host 127.0.0.1 --port 8780 --data-dir /hf3fs-jd/data/tmp/openshop/local-db
```
