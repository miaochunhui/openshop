# OpenShop · 开集

一个可本地运行、可审计的开源多边电商平台。它把淘宝式丰富供给、拼多多式拼团、京东式自营与履约信任放进同一套简洁的购物体验里。

> **开集**，取自“开市集”。不追逐算法幻觉，先把商品、价格、库存、订单和每一次点击的真实性做好。

## 当前状态

开发中。真实完成度以 `docs/STATUS.md`、自动测试和现读接口为准，不以本文愿景代替验收。

## 快速开始

```bash
python3 -B apps/storefront/server.py --host 127.0.0.1 --port 8780
```

打开 <http://127.0.0.1:8780/>。运行时只依赖 Python 3.10+ 标准库与 SQLite，无 Node 构建步骤，无外部 CDN。

演示账号可在登录弹窗一键进入；订单支付为明确标注的模拟流程，不产生真实交易。

## 为什么是 OpenShop

- **三种买法，一套账本**：官方自营、品牌旗舰、源头拼团同场比较，价格与履约条件透明呈现。
- **可信优先**：服务端重算价格、优惠与库存；订单、退款、评价和商家操作都有可追溯状态。
- **没有暗黑模式**：不使用虚构倒计时、虚假抢购人数或强制勾选；拼团进度来自真实订单。
- **真正可运行的开源**：标准库后端、零 CDN 前端、SQLite 持久化、固定发布与回滚入口。

## 目录

| 路径 | 用途 |
| --- | --- |
| `apps/storefront/` | HTTP 服务、路由与静态资源入口 |
| `packages/commerce/` | 数据、领域逻辑、序列化与安全原语 |
| `web/` | 无构建步骤的 ES Module 前端 |
| `data/` | 可审阅的商品内容与生成规则 |
| `tests/` | 单元、API、安全与真实浏览器验收 |
| `docs/` | 产品、架构、API 与交付证据 |
| `tools/` | 生成、检查、发布工具 |
| `deploy/` | systemd 与反代的固定版本部署样例 |

## 许可与声明

代码以 Apache License 2.0 发布。商品、品牌、店铺、用户均为虚构演示数据；项目中不使用淘宝、拼多多、京东的名称、商标或素材。它们只作为产品能力与交互模式的公开研究参照。

## English

OpenShop is an audit-friendly, self-hosted open-source marketplace demo: discovery, offer comparison, group buying, self-operated fulfillment, cart, checkout, orders, reviews, after-sales and a merchant console. Stdlib-only Python backend, SQLite, vanilla ES modules, no build step and no third-party CDN. Payments are simulated. All catalog content is fictional.
