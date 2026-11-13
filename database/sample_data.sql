-- Text-to-SQL Analytics Agent — sample data for the e-commerce schema
-- Apply after schema.sql:  psql -d analytics -f database/sample_data.sql
--
-- Customers and products are explicit rows so example questions have stable
-- answers; orders and order_items use generate_series with a fixed seed so the
-- volume is realistic (400 orders / ~1000 line items) yet reproducible.

SET search_path TO analytics, public;
SELECT setseed(0.42);

-- ---------------------------------------------------------------- customers
INSERT INTO customers (name, email, city) VALUES
    ('Ayesha Khan',      'ayesha.khan@example.com',   'Karachi'),
    ('Bilal Ahmed',      'bilal.ahmed@example.com',   'Karachi'),
    ('Sana Malik',       'sana.malik@example.com',    'Karachi'),
    ('Omar Farooq',      'omar.farooq@example.com',   'Karachi'),
    ('Zara Iqbal',       'zara.iqbal@example.com',    'Lahore'),
    ('Hamza Sheikh',     'hamza.sheikh@example.com',  'Lahore'),
    ('Hira Nawaz',       'hira.nawaz@example.com',    'Lahore'),
    ('Usman Tariq',      'usman.tariq@example.com',   'Lahore'),
    ('Fatima Noor',      'fatima.noor@example.com',   'Islamabad'),
    ('Ali Raza',         'ali.raza@example.com',      'Islamabad'),
    ('Maryam Yousuf',    'maryam.yousuf@example.com', 'Islamabad'),
    ('Daniyal Aslam',    'daniyal.aslam@example.com', 'Rawalpindi'),
    ('Rabia Bashir',     'rabia.bashir@example.com',  'Multan'),
    ('Kamran Shah',      'kamran.shah@example.com',   'Peshawar'),
    ('Nimra Saqi',       'nimra.saqi@example.com',    'Quetta'),
    ('Taha Rehman',      'taha.rehman@example.com',   'Faisalabad'),
    ('Iqra Saleem',      'iqra.saleem@example.com',   'Sialkot'),
    ('Saad Javed',       'saad.javed@example.com',    'Hyderabad'),
    ('Laiba Nadeem',     'laiba.nadeem@example.com',  'Karachi'),
    ('Musa Haider',      'musa.haider@example.com',   'Lahore'),
    ('Areeba Siddiqui',  'areeba.s@example.com',      'Islamabad'),
    ('Faizan Mehmood',   'faizan.m@example.com',      'Gujranwala'),
    ('Sadia Anwar',      'sadia.anwar@example.com',   'Multan'),
    ('Zeeshan Akhtar',   'zeeshan.akhtar@example.com','Karachi');

-- ----------------------------------------------------------------- products
INSERT INTO products (product_name, category, price) VALUES
    ('Wireless Mouse',            'Electronics',      18.90),
    ('Mechanical Keyboard',       'Electronics',      59.00),
    ('24-inch Monitor',           'Electronics',     139.00),
    ('Noise Cancelling Headset',  'Electronics',      99.50),
    ('USB-C Docking Station',     'Electronics',      74.00),
    ('Portable SSD 1TB',          'Electronics',     105.00),
    ('Smartphone Stand',          'Electronics',      12.50),
    ('Ergonomic Office Chair',    'Furniture',       189.00),
    ('Standing Desk',             'Furniture',       279.00),
    ('Bookshelf 5-Tier',          'Furniture',       118.00),
    ('Coffee Table',              'Furniture',        85.00),
    ('Storage Cabinet',           'Furniture',        96.50),
    ('Bedside Lamp',              'Furniture',        29.90),
    ('Cotton T-Shirt',            'Clothing',         14.00),
    ('Denim Jacket',              'Clothing',         54.90),
    ('Running Sneakers',          'Clothing',         68.00),
    ('Wool Sweater',              'Clothing',         42.50),
    ('Cargo Trousers',            'Clothing',         36.00),
    ('Non-Stick Cookware Set',    'Home & Kitchen',   78.00),
    ('Electric Kettle',           'Home & Kitchen',   27.50),
    ('Ceramic Dinner Plates',     'Home & Kitchen',   34.00),
    ('Air Fryer 5L',              'Home & Kitchen',  112.00),
    ('Blender 800W',              'Home & Kitchen',   55.00),
    ('Yoga Mat',                  'Sports',           22.00),
    ('Adjustable Dumbbells',      'Sports',           89.00),
    ('Badminton Racket',          'Sports',           31.50),
    ('Cycling Helmet',            'Sports',           45.00),
    ('Resistance Bands Set',      'Sports',           26.90),
    ('Clean Code',                'Books',            32.00),
    ('Design Patterns',           'Books',            38.50),
    ('Data Science Handbook',     'Books',            41.00),
    ('Deep Learning Foundations', 'Books',            49.90);

-- ------------------------------------------------------------------- orders
-- ~400 orders spanning 2025-01-01 to 2026-11-10.
INSERT INTO orders (customer_id, order_date, status)
SELECT 1 + (s % 24),
       DATE '2025-01-01' + (floor(random() * 680))::int,
       (ARRAY['completed', 'completed', 'completed', 'shipped', 'pending', 'cancelled', 'returned'])
           [1 + floor(random() * 7)::int]
FROM generate_series(1, 400) AS s;

SELECT setval(pg_get_serial_sequence('orders', 'order_id'),
               (SELECT MAX(order_id) FROM orders));

-- --------------------------------------------------------------- order_items
-- 1 to 4 line items per order, priced from the product catalogue.
INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT seeded.order_id,
       seeded.product_id,
       seeded.quantity,
       p.price
FROM (
    SELECT o.order_id,
           1 + floor(random() * (SELECT COUNT(*) FROM products))::int AS product_id,
           1 + floor(random() * 5)::int                              AS quantity
    FROM orders o
    CROSS JOIN generate_series(1, 1 + floor(random() * 4)::int) AS line_no
) AS seeded
JOIN products p ON p.product_id = seeded.product_id;

SELECT setval(pg_get_serial_sequence('order_items', 'item_id'),
               (SELECT MAX(item_id) FROM order_items));

-- PostgreSQL ANALYZE takes one optional table; bare ANALYZE refreshes all of them.
ANALYZE;
