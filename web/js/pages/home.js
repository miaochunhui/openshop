// 首页 page stub — owned by discovery seat (主页、分类、活动与发现).
export async function render(ctx) {
  ctx.setTitle("首页 · OpenShop 开集");
  ctx.root.innerHTML = `<section class="container route-placeholder">
    <span class="eyebrow">IN PROGRESS</span>
    <h1>首页</h1>
    <p class="lede">主页、分类、活动与发现 正在由 discovery 席实现。这里的接口、设计与壳已经就位。</p>
    <div class="cluster mt-3"><a class="btn btn--primary" href="/" data-link>返回首页</a></div>
  </section>`;
}
