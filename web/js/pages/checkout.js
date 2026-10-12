// 结算 page stub — owned by flow seat (地址、优惠券、运费、模拟支付).
export async function render(ctx) {
  ctx.setTitle("结算 · OpenShop 开集");
  ctx.root.innerHTML = `<section class="container route-placeholder">
    <span class="eyebrow">IN PROGRESS</span>
    <h1>结算</h1>
    <p class="lede">地址、优惠券、运费、模拟支付 正在由 flow 席实现。这里的接口、设计与壳已经就位。</p>
    <div class="cluster mt-3"><a class="btn btn--primary" href="/" data-link>返回首页</a></div>
  </section>`;
}
