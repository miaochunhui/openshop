// 商家工作台 page stub — owned by merchant seat (经营概览、库存、发货与售后).
export async function render(ctx) {
  ctx.setTitle("商家工作台 · OpenShop 开集");
  ctx.root.innerHTML = `<section class="container route-placeholder">
    <span class="eyebrow">IN PROGRESS</span>
    <h1>商家工作台</h1>
    <p class="lede">经营概览、库存、发货与售后 正在由 merchant 席实现。这里的接口、设计与壳已经就位。</p>
    <div class="cluster mt-3"><a class="btn btn--primary" href="/" data-link>返回首页</a></div>
  </section>`;
}
