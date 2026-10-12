// 我的开集 page stub — owned by flow seat (订单、地址、优惠券与收藏).
export async function render(ctx) {
  ctx.setTitle("我的开集 · OpenShop 开集");
  ctx.root.innerHTML = `<section class="container route-placeholder">
    <span class="eyebrow">IN PROGRESS</span>
    <h1>我的开集</h1>
    <p class="lede">订单、地址、优惠券与收藏 正在由 flow 席实现。这里的接口、设计与壳已经就位。</p>
    <div class="cluster mt-3"><a class="btn btn--primary" href="/" data-link>返回首页</a></div>
  </section>`;
}
