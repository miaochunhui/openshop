// 商品详情 page stub — owned by discovery seat (商品详情、报价比较与 SKU 选择).
export async function render(ctx) {
  ctx.setTitle("商品详情 · OpenShop 开集");
  ctx.root.innerHTML = `<section class="container route-placeholder">
    <span class="eyebrow">IN PROGRESS</span>
    <h1>商品详情</h1>
    <p class="lede">商品详情、报价比较与 SKU 选择 正在由 discovery 席实现。这里的接口、设计与壳已经就位。</p>
    <div class="cluster mt-3"><a class="btn btn--primary" href="/" data-link>返回首页</a></div>
  </section>`;
}
