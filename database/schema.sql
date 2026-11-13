-- Text-to-SQL Analytics Agent — PostgreSQL schema
-- E-commerce sample model: customers, products, orders, order_items
--
-- Apply with:  psql -d analytics -f database/schema.sql

CREATE SCHEMA IF NOT EXISTS analytics;
SET search_path TO analytics, public;

-- ---------------------------------------------------------------- customers
CREATE TABLE IF NOT EXISTS customers (
    customer_id   SERIAL PRIMARY KEY,
    name          TEXT    NOT NULL,
    email         TEXT    NOT NULL UNIQUE,
    city          TEXT    NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_customers_city ON customers (city);

-- ----------------------------------------------------------------- products
CREATE TABLE IF NOT EXISTS products (
    product_id    SERIAL PRIMARY KEY,
    product_name  TEXT    NOT NULL,
    category      TEXT    NOT NULL,
    price         NUMERIC(10, 2) NOT NULL CHECK (price >= 0)
);

CREATE INDEX IF NOT EXISTS idx_products_category ON products (category);

-- ------------------------------------------------------------------- orders
CREATE TABLE IF NOT EXISTS orders (
    order_id      SERIAL PRIMARY KEY,
    customer_id   INTEGER NOT NULL REFERENCES customers (customer_id),
    order_date    DATE    NOT NULL,
    status        TEXT    NOT NULL
                  CHECK (status IN ('pending', 'shipped', 'completed', 'cancelled', 'returned'))
);

CREATE INDEX IF NOT EXISTS idx_orders_customer  ON orders (customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_date      ON orders (order_date);
CREATE INDEX IF NOT EXISTS idx_orders_status    ON orders (status);

-- --------------------------------------------------------------- order_items
CREATE TABLE IF NOT EXISTS order_items (
    item_id       SERIAL PRIMARY KEY,
    order_id      INTEGER NOT NULL REFERENCES orders (order_id),
    product_id    INTEGER NOT NULL REFERENCES products (product_id),
    quantity      INTEGER NOT NULL CHECK (quantity > 0),
    unit_price    NUMERIC(10, 2) NOT NULL CHECK (unit_price >= 0)
);

CREATE INDEX IF NOT EXISTS idx_order_items_order   ON order_items (order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_product ON order_items (product_id);

-- --------------------------------------------------------- reporting helpers
CREATE OR REPLACE VIEW order_revenue AS
SELECT o.order_id,
       o.customer_id,
       o.order_date,
       o.status,
       COALESCE(SUM(oi.quantity * oi.unit_price), 0) AS revenue
FROM orders o
LEFT JOIN order_items oi ON oi.order_id = o.order_id
GROUP BY o.order_id, o.customer_id, o.order_date, o.status;

-- ------------------------------------------------- read-only analytics account
-- The agent connects with this role: it can only read the tables it needs.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'analytics_reader') THEN
        CREATE ROLE analytics_reader LOGIN PASSWORD 'analytics_reader_pw';
    END IF;
END
$$;

GRANT USAGE ON SCHEMA analytics TO analytics_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO analytics_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO analytics_reader;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON ALL TABLES IN SCHEMA analytics FROM analytics_reader;

-- Both roles must resolve the analytics schema by default, otherwise the
-- backend's SQLAlchemy inspector looks in `public` and finds nothing.
ALTER ROLE analytics_writer SET search_path TO analytics, public;
ALTER ROLE analytics_reader SET search_path TO analytics, public;

COMMENT ON TABLE customers   IS 'Customers with contact details and city.';
COMMENT ON TABLE products    IS 'Product catalogue with category and list price.';
COMMENT ON TABLE orders      IS 'One row per customer order; status tracks fulfilment.';
COMMENT ON TABLE order_items IS 'Line items of an order; quantity priced at purchase time.';
