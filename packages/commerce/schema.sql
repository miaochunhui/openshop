-- OpenShop schema v1. SQLite, foreign keys, WAL.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shops (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('self','brand','factory')),
  tagline TEXT NOT NULL DEFAULT '',
  story TEXT NOT NULL DEFAULT '',
  rating REAL NOT NULL DEFAULT 5.0 CHECK (rating BETWEEN 0 AND 5),
  followers_count INTEGER NOT NULL DEFAULT 0 CHECK (followers_count >= 0),
  since TEXT NOT NULL DEFAULT '',
  verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0,1)),
  response_rate TEXT NOT NULL DEFAULT '',
  ship_from TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  handle TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  phone TEXT,
  role TEXT NOT NULL CHECK (role IN ('buyer','seller')),
  password_hash TEXT,
  password_salt TEXT,
  avatar TEXT NOT NULL DEFAULT '',
  shop_id INTEGER REFERENCES shops(id),
  member_since TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  csrf TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_hash ON sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS categories (
  slug TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  subtitle TEXT NOT NULL DEFAULT '',
  icon TEXT NOT NULL DEFAULT 'store',
  sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  subtitle TEXT NOT NULL DEFAULT '',
  brand TEXT NOT NULL DEFAULT '',
  category_slug TEXT NOT NULL REFERENCES categories(slug),
  description TEXT NOT NULL DEFAULT '',
  story TEXT NOT NULL DEFAULT '',
  tags_json TEXT NOT NULL DEFAULT '[]',
  badges_json TEXT NOT NULL DEFAULT '[]',
  images_json TEXT NOT NULL DEFAULT '[]',
  cover TEXT NOT NULL,
  cover_alt TEXT NOT NULL DEFAULT '',
  highlights_json TEXT NOT NULL DEFAULT '[]',
  specs_json TEXT NOT NULL DEFAULT '[]',
  detail_json TEXT NOT NULL DEFAULT '[]',
  faq_json TEXT NOT NULL DEFAULT '[]',
  featured INTEGER NOT NULL DEFAULT 0 CHECK (featured IN (0,1)),
  is_new INTEGER NOT NULL DEFAULT 0 CHECK (is_new IN (0,1)),
  is_hero INTEGER NOT NULL DEFAULT 0 CHECK (is_hero IN (0,1)),
  sold_count INTEGER NOT NULL DEFAULT 0 CHECK (sold_count >= 0),
  rating REAL NOT NULL DEFAULT 0 CHECK (rating BETWEEN 0 AND 5),
  review_count INTEGER NOT NULL DEFAULT 0 CHECK (review_count >= 0),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','hidden')),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_slug);
CREATE INDEX IF NOT EXISTS idx_products_featured ON products(featured);

CREATE TABLE IF NOT EXISTS offers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  shop_id INTEGER NOT NULL REFERENCES shops(id),
  kind TEXT NOT NULL CHECK (kind IN ('self','brand','factory')),
  name TEXT NOT NULL,
  price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
  market_price_cents INTEGER NOT NULL CHECK (market_price_cents >= price_cents),
  shipping_fee_cents INTEGER NOT NULL DEFAULT 0 CHECK (shipping_fee_cents >= 0),
  stock INTEGER NOT NULL CHECK (stock >= 0),
  delivery_eta_days INTEGER NOT NULL CHECK (delivery_eta_days BETWEEN 1 AND 30),
  delivery_note TEXT NOT NULL DEFAULT '',
  services_json TEXT NOT NULL DEFAULT '[]',
  group_size INTEGER CHECK (group_size IS NULL OR group_size BETWEEN 2 AND 20),
  group_price_cents INTEGER CHECK (group_price_cents IS NULL OR group_price_cents >= 0),
  is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0,1)),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK ((group_size IS NULL AND group_price_cents IS NULL) OR
         (group_size IS NOT NULL AND group_price_cents IS NOT NULL AND group_price_cents < price_cents))
);
CREATE INDEX IF NOT EXISTS idx_offers_product ON offers(product_id);
CREATE INDEX IF NOT EXISTS idx_offers_shop ON offers(shop_id);

CREATE TABLE IF NOT EXISTS skus (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  options_json TEXT NOT NULL DEFAULT '{}',
  price_delta_cents INTEGER NOT NULL DEFAULT 0 CHECK (price_delta_cents >= 0),
  sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_skus_product ON skus(product_id);

CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  order_item_id INTEGER UNIQUE REFERENCES order_items(id) ON DELETE SET NULL,
  author TEXT NOT NULL,
  rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
  title TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL,
  tags_json TEXT NOT NULL DEFAULT '[]',
  verified INTEGER NOT NULL DEFAULT 1 CHECK (verified IN (0,1)),
  helpful_count INTEGER NOT NULL DEFAULT 0 CHECK (helpful_count >= 0),
  sku_label TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_product ON reviews(product_id, created_at);

CREATE TABLE IF NOT EXISTS carts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cart_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cart_id INTEGER NOT NULL REFERENCES carts(id) ON DELETE CASCADE,
  offer_id INTEGER NOT NULL REFERENCES offers(id),
  sku_id INTEGER NOT NULL REFERENCES skus(id),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  selected INTEGER NOT NULL DEFAULT 1 CHECK (selected IN (0,1)),
  buy_mode TEXT NOT NULL DEFAULT 'standard' CHECK (buy_mode IN ('standard','group')),
  group_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cart_item_unique
  ON cart_items(cart_id, offer_id, sku_id, buy_mode, IFNULL(group_code, ''));
CREATE INDEX IF NOT EXISTS idx_cart_items_cart ON cart_items(cart_id);

CREATE TABLE IF NOT EXISTS favorites (
  owner_key TEXT NOT NULL,
  product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  PRIMARY KEY (owner_key, product_id)
);

CREATE TABLE IF NOT EXISTS addresses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  label TEXT NOT NULL DEFAULT '',
  receiver TEXT NOT NULL,
  phone TEXT NOT NULL,
  province TEXT NOT NULL,
  city TEXT NOT NULL,
  district TEXT NOT NULL,
  detail TEXT NOT NULL,
  is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0,1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_addresses_user ON addresses(user_id);

CREATE TABLE IF NOT EXISTS coupons (
  code TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  threshold_cents INTEGER NOT NULL DEFAULT 0 CHECK (threshold_cents >= 0),
  discount_cents INTEGER NOT NULL CHECK (discount_cents > 0),
  scope TEXT NOT NULL DEFAULT 'all',
  valid_days INTEGER NOT NULL CHECK (valid_days > 0),
  auto_claim_new_user INTEGER NOT NULL DEFAULT 0 CHECK (auto_claim_new_user IN (0,1)),
  quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
  claimed_count INTEGER NOT NULL DEFAULT 0 CHECK (claimed_count >= 0),
  created_at TEXT NOT NULL,
  CHECK (discount_cents <= threshold_cents OR threshold_cents = 0)
);

CREATE TABLE IF NOT EXISTS user_coupons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  coupon_code TEXT NOT NULL REFERENCES coupons(code),
  status TEXT NOT NULL DEFAULT 'AVAILABLE' CHECK (status IN ('AVAILABLE','USED','EXPIRED')),
  claimed_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  order_id INTEGER,
  UNIQUE (user_id, coupon_code)
);
CREATE INDEX IF NOT EXISTS idx_user_coupons_user ON user_coupons(user_id, status);

CREATE TABLE IF NOT EXISTS browse_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_key TEXT NOT NULL,
  product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL CHECK (event_type IN ('view','search')),
  query TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_browse_owner ON browse_events(owner_key, created_at);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_no TEXT NOT NULL UNIQUE,
  user_id INTEGER NOT NULL REFERENCES users(id),
  status TEXT NOT NULL CHECK (status IN ('PENDING_PAYMENT','GROUP_FORMING','PAID','SHIPPED','COMPLETED','CANCELLED','REFUNDED')),
  subtotal_cents INTEGER NOT NULL CHECK (subtotal_cents >= 0),
  discount_cents INTEGER NOT NULL DEFAULT 0 CHECK (discount_cents >= 0),
  shipping_cents INTEGER NOT NULL DEFAULT 0 CHECK (shipping_cents >= 0),
  total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
  market_subtotal_cents INTEGER NOT NULL DEFAULT 0 CHECK (market_subtotal_cents >= 0),
  coupon_code TEXT,
  user_coupon_id INTEGER,
  address_json TEXT NOT NULL,
  shipping_method TEXT NOT NULL,
  payment_method TEXT,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  paid_at TEXT,
  shipped_at TEXT,
  completed_at TEXT,
  cancelled_at TEXT,
  refunded_at TEXT,
  idempotency_key TEXT NOT NULL,
  group_code TEXT,
  last_state_before_refund TEXT,
  UNIQUE (user_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_orders_group ON orders(group_code);

CREATE TABLE IF NOT EXISTS groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  offer_id INTEGER NOT NULL REFERENCES offers(id),
  sku_id INTEGER NOT NULL REFERENCES skus(id),
  initiator_user_id INTEGER NOT NULL REFERENCES users(id),
  required_size INTEGER NOT NULL CHECK (required_size BETWEEN 2 AND 20),
  group_price_cents INTEGER NOT NULL CHECK (group_price_cents >= 0),
  status TEXT NOT NULL DEFAULT 'FORMING' CHECK (status IN ('FORMING','FULL','CANCELLED','EXPIRED')),
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS group_members (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id),
  order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id) ON DELETE CASCADE,
  paid_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (group_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_group_members_group ON group_members(group_id);

CREATE TABLE IF NOT EXISTS order_packages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  shop_id INTEGER NOT NULL REFERENCES shops(id),
  status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SHIPPED','DELIVERED','CANCELLED')),
  carrier TEXT,
  tracking_no TEXT,
  shipping_method TEXT NOT NULL DEFAULT 'standard',
  shipped_at TEXT,
  delivered_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_packages_order ON order_packages(order_id);
CREATE INDEX IF NOT EXISTS idx_packages_shop ON order_packages(shop_id, status);

CREATE TABLE IF NOT EXISTS order_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  package_id INTEGER NOT NULL REFERENCES order_packages(id) ON DELETE CASCADE,
  product_id INTEGER NOT NULL REFERENCES products(id),
  offer_id INTEGER NOT NULL REFERENCES offers(id),
  sku_id INTEGER NOT NULL REFERENCES skus(id),
  shop_id INTEGER NOT NULL REFERENCES shops(id),
  title TEXT NOT NULL,
  cover TEXT NOT NULL,
  sku_label TEXT NOT NULL,
  unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  buy_mode TEXT NOT NULL DEFAULT 'standard' CHECK (buy_mode IN ('standard','group')),
  offer_kind TEXT NOT NULL,
  services_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_offer ON order_items(offer_id);

CREATE TABLE IF NOT EXISTS order_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  package_id INTEGER REFERENCES order_packages(id) ON DELETE SET NULL,
  label TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_order_events_order ON order_events(order_id, created_at);

CREATE TABLE IF NOT EXISTS refunds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  order_no TEXT NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id),
  shop_id INTEGER REFERENCES shops(id),
  kind TEXT NOT NULL CHECK (kind IN ('refund_only','return_refund')),
  reason TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
  status TEXT NOT NULL DEFAULT 'REQUESTED' CHECK (status IN ('REQUESTED','APPROVED','REJECTED','CANCELLED')),
  decision_note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_refunds_order ON refunds(order_id);
CREATE INDEX IF NOT EXISTS idx_refunds_shop ON refunds(shop_id, status);

CREATE TABLE IF NOT EXISTS rate_limits (
  bucket TEXT PRIMARY KEY,
  count INTEGER NOT NULL,
  window_start TEXT NOT NULL
);

-- FK from user_coupons.order_id to orders declared after orders table exists is not supported
-- by ALTER in old SQLite across all builds; it remains an intentionally unenforced audit link.
