# 参与 OpenShop 开集

感谢你愿意改进 OpenShop。这个项目的重点是**可运行、可审阅、数据诚实**，而不是用漂亮文案掩盖未完成。

参与本项目即表示你同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。安全问题请按 [SECURITY.md](SECURITY.md) 私下报告，不要先开公开 issue。

## 开始之前

1. 阅读 [README.md](README.md) 与 [docs/INSTALL.md](docs/INSTALL.md)，先用独立数据目录把应用跑起来。
2. 阅读 [docs/api-contract.md](docs/api-contract.md) 与 [docs/data-contract.md](docs/data-contract.md)。这两个契约已冻结，前端与后端不能各自猜字段。
3. 涉及接口或数据契约的改动，先开 issue/提案说明动机、兼容性和测试计划，由维护者协调后再动代码；不要在一个实现 PR 里静默改共享契约。
4. 涉及 UI 的改动先读 [docs/design.md](docs/design.md)，沿用设计 token 与无障碍要求。

## 本地开发

运行环境只需 Python 3.10+、标准库与 SQLite。不要安装第三方运行时包，也不要引入前端构建链。Node 仅用于仓库门中的 JavaScript 语法检查。

```bash
python3 -B apps/storefront/server.py --host 127.0.0.1 --port 8780 --data-dir "$HOME/.local/share/openshop-dev"

python3 -B -m unittest discover -s tests -v
python3 -B tools/check_repository.py --selftest
python3 -B tools/check_repository.py --check
```

- 数据目录必须在仓库之外，且不要复用他人的运行数据库。
- 不要提交 `openshop.sqlite3`、WAL/SHM、日志、截图临时件、`__pycache__/`、测试报告或凭据。
- 如果仓库门在你的树上失败，先确认失败是否是真实的缺件/回归。修问题，不要放宽断言、删测试或给扫描器加白名单来换绿色。
- PR 描述中附原始命令和结果；仅有“手动测过”不算证据。

## 实现约定

- **零第三方运行时依赖**：服务端只用 Python 标准库 + SQLite；前端只用浏览器原生能力。HTML、CSS、JS、SVG 不得从 CDN、第三方域名或外部字体加载内容，也不要添加 npm/pip 依赖。
- **服务端是价格权威**：金额、优惠、库存与订单可执行状态由服务端重算/判定；前端不得自行计算最终应付或伪造成功状态。
- **内容诚实**：不得写虚构倒计时、虚假拼团人数、虚假销量或“全网最低”等无法证明的宣称。商品、店铺、人物与评价保持虚构并与真实主体无关。
- **原创素材**：商品图、品牌图标和插画应放在 `web/assets/` 并随仓库提交，且由贡献者原创或拥有可再分发许可。不要复制真实平台的商标、截图、商品图、字体或文案。
- **安全与隐私**：写操作必须带 CSRF 保护；不要信任前端传入的角色、归属或金额；不要把真实密码、地址、身份信息、令牌或生产凭据写进代码、测试、issue 或日志。
- **无障碍**：键盘可完成主路径，图标按钮有可读名称，图片有恰当 alt，焦点环和 `prefers-reduced-motion` 行为不可移除。
- **CSS token**：设计 token 只在受控位置定义，不要在多个 `:root` 中重复同名 token；页面级局部变量可继续使用，但发布门会检查根 token 重复。
- **不要内联脚本**：不添加内联 `<script>`、`javascript:` URL 或 `onclick=` 内联事件属性；CSP 不允许它们工作。

## 测试要求

- 单元/API 测试使用标准库 `unittest`，可离线运行，不得依赖真实网络、真实支付或外部服务。
- 改变行为时同时补测试；修复 bug 时先让测试能复现问题（除非确实无法自动化的浏览器视觉检查，此时在 PR 中写明复现步骤和证据）。
- 浏览器验收至少覆盖主路径；证据应可复核，不能只贴“通过”二字。
- 运行 `python3 -B tools/check_repository.py --check`；如失败，发布/合并应被阻止。`--selftest` 必须保持通过，不能为了产品门变绿而弱化它。
- 变更部署模板时，明确说明是否实际安装/演练过；未演练不得写“已验证上线”。

## 提交与 Pull Request

- 小步提交，一个 PR 解决一个主题；提交信息建议使用 `feat:`、`fix:`、`docs:`、`test:`、`chore:` 前缀。
- PR 描述包含：问题、改动范围、风险、测试命令与原始结果、未覆盖边界、界面截图（如涉及）。
- 不要提交无关格式化、生成数据库、临时文件或凭据。不要绕过 CI。
- 维护者会优先审查安全、数据诚实和可复核性；拒绝不是对你个人的评价。
- 合并前 CI 必须通过。CI 使用只读权限、无凭据、无网络安装；不要在 CI 中引入包安装或发布动作。

## 许可

除另有明确说明外，贡献以 [Apache License 2.0](LICENSE) 授权。提交 PR 即表示你有权按该许可提供内容。本项目没有单独的 CLA。

请只提交原创或兼容许可的文本、代码和素材；不要提交第三方商标、受版权保护的图片、客户数据或生产凭据。生产安装与发布不在普通贡献范围，必须由维护者在明确授权内执行；详见 [docs/RELEASE.md](docs/RELEASE.md) 与 [deploy/README.md](deploy/README.md)。
