// 搜索 page stub — owned by discovery seat (搜索、筛选、结果与空态).
export async function render(ctx) {
  ctx.setTitle("搜索 · OpenShop 开集");
  ctx.root.innerHTML = `<section class="container route-placeholder">
    <span class="eyebrow">IN PROGRESS</span>
    <h1>搜索</h1>
    <p class="lede">搜索、筛选、结果与空态 正在由 discovery 席实现。这里的接口、设计与壳已经就位。</p>
    <div class="cluster mt-3"><a class="btn btn--primary" href="/" data-link>返回首页</a></div>
  </section>`;
}
