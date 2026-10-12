export async function render(ctx) {
  ctx.setTitle("帮助与规则 · OpenShop 开集");
  ctx.root.innerHTML = `
  <section class="container prose-page">
    <p class="eyebrow">HELP & RULES</p>
    <h1>先说清楚，<br>再放心买。</h1>
    <div class="help-list">
      <details open><summary>这是真实商店吗？</summary><p>不是。OpenShop 是开源产品演示，所有商品、品牌、店铺、人物、订单和评价均为虚构；支付为模拟流程，不产生真实扣款。</p></details>
      <details><summary>三种买法有什么区别？</summary><p>官方自营由 OpenShop 自营仓发货，通常配送更快；品牌旗舰由品牌仓发货，提供品牌直营服务；源头工厂店以集单换取更低价格，发货时间更长。具体以商品页的配送和服务说明为准。</p></details>
      <details><summary>拼团怎么算成功？</summary><p>发起或加入拼团后，需要完成模拟支付。支付成功的人数达到成团人数后，拼团才显示“已成团”，订单进入待发货。未成团不会伪装成成功。</p></details>
      <details><summary>优惠券如何使用？</summary><p>每次结算最多使用一张优惠券。满减门槛按商品小计计算，不包含模拟运费。已用于订单的券会在取消订单且符合条件时退回。</p></details>
      <details><summary>多久能收到？</summary><p>商品页会给出预计时效。演示环境中的物流单号为虚构数据，仅用于展示订单状态流转。</p></details>
      <details><summary>订单怎么取消、确认收货与评价？</summary><p>在“我的开集 → 订单”中操作。待付款可取消；商家发货后可确认收货；交易完成后可对每件商品发表一次评价。需要售后时从订单详情发起退款申请。</p></details>
      <details><summary>我的数据存在哪里？</summary><p>本地 SQLite 数据库，路径由服务启动参数指定。演示环境不接入第三方分析或广告脚本。请勿提交真实个人信息。</p></details>
    </div>
    <div class="prose-callout"><h2>还没解决？</h2><p>本演示不提供人工客服。开发者可以在项目仓库提交 issue 或 pull request。</p><a class="btn btn--ghost" href="/account" data-link>查看我的订单</a></div>
  </section>`;
}
