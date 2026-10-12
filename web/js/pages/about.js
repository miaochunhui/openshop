export async function render(ctx) {
  ctx.setTitle("关于 OpenShop 开集 · 开源电商");
  ctx.root.innerHTML = `
  <section class="container prose-page">
    <p class="eyebrow">ABOUT OPENSHOP</p>
    <h1>把买卖这件事，<br>讲得再清楚一点。</h1>
    <div class="prose-lede">
      <p>OpenShop（开集）是一个开源电商平台实验。它把自营、品牌旗舰与源头拼团放进同一张商品页，让价格、店铺、配送和服务可以被直接比较。</p>
      <p>我们不复制任何平台的页面、商标或商品素材。淘宝的丰富、拼多多的拼团、京东的履约确定性，只作为公开产品模式的研究参照。</p>
    </div>
    <div class="prose-grid">
      <article><span>01</span><h2>真实优先</h2><p>拼团进度来自真实订单，库存由数据库扣减，价格与优惠在服务端重算。没有虚构倒计时，也没有“手慢无”的催促。</p></article>
      <article><span>02</span><h2>能被看懂</h2><p>同一件商品的不同供给把差异摊开：谁发货、多久到、凭什么贵、遇到问题找谁。界面尽量少让人猜。</p></article>
      <article><span>03</span><h2>能自己跑</h2><p>Python 标准库 + SQLite + 原生 ES Module，无打包步骤、无第三方 CDN。下载代码，一行命令就能在本地开张。</p></article>
      <article><span>04</span><h2>明确边界</h2><p>支付是模拟的，商品与人物是虚构的，未声称支持真实资金、真实物流或真实身份证照。这是一个可读、可测、可改的样板，而不是伪装成生产系统。</p></article>
    </div>
    <div class="prose-callout">
      <h2>开源不是一句口号</h2>
      <p>代码、数据种子、设计资产、测试、发布与回滚入口一同交付。许可证为 Apache-2.0；贡献指南与安全说明见仓库根目录。</p>
      <a class="btn btn--primary" href="/" data-link>去逛开集</a>
    </div>
  </section>`;
}
