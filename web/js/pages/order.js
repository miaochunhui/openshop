// 订单详情 page stub — owned by flow seat (订单状态、物流、评价与售后).
export async function render(ctx) {
  ctx.setTitle("订单详情 · OpenShop 开集");
  ctx.root.innerHTML = `<section class="container route-placeholder">
    <span class="eyebrow">IN PROGRESS</span>
    <h1>订单详情</h1>
    <p class="lede">订单状态、物流、评价与售后 正在由 flow 席实现。这里的接口、设计与壳已经就位。</p>
    <div class="cluster mt-3"><a class="btn btn--primary" href="/" data-link>返回首页</a></div>
  </section>`;
}
