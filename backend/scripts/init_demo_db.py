"""Create and seed the demo analytics database (SQLite by default).

    python scripts/init_demo_db.py                # ./data/analytics.db
    python scripts/init_demo_db.py --url sqlite:///./x.db
    python scripts/init_demo_db.py --orders 400

The generated questions in this project (top customers, revenue by category,
monthly trend, inactive customers) need enough orders across 2025 and 2026 for
the answers to be meaningful, so the seeder spreads dates over both years and
priced line items are produced from the product catalogue.
"""

from __future__ import annotations

import argparse
import datetime as dt
import random
from pathlib import Path

from sqlalchemy import create_engine, text

DDL = [
    """
    CREATE TABLE IF NOT EXISTS customers (
        customer_id INTEGER PRIMARY KEY,
        name        TEXT NOT NULL,
        email       TEXT NOT NULL UNIQUE,
        city        TEXT NOT NULL,
        created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS products (
        product_id   INTEGER PRIMARY KEY,
        product_name TEXT NOT NULL,
        category     TEXT NOT NULL,
        price        NUMERIC(10, 2) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        order_id    INTEGER PRIMARY KEY,
        customer_id INTEGER NOT NULL REFERENCES customers (customer_id),
        order_date  DATE NOT NULL,
        status      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS order_items (
        item_id    INTEGER PRIMARY KEY,
        order_id   INTEGER NOT NULL REFERENCES orders (order_id),
        product_id INTEGER NOT NULL REFERENCES products (product_id),
        quantity   INTEGER NOT NULL,
        unit_price NUMERIC(10, 2) NOT NULL
    )
    """,
]

# Reporting helper the Postgres schema also defines; SQLite gets a plain view.
VIEW = (
    "CREATE VIEW IF NOT EXISTS order_revenue AS "
    "SELECT o.order_id, o.customer_id, o.order_date, o.status, "
    "COALESCE(SUM(oi.quantity * oi.unit_price), 0) AS revenue "
    "FROM orders o LEFT JOIN order_items oi ON oi.order_id = o.order_id "
    "GROUP BY o.order_id, o.customer_id, o.order_date, o.status"
)

CUSTOMERS = [
    ("Ayesha Khan", "Karachi"), ("Bilal Ahmed", "Karachi"), ("Sana Malik", "Karachi"),
    ("Omar Farooq", "Karachi"), ("Laiba Nadeem", "Karachi"), ("Zeeshan Akhtar", "Karachi"),
    ("Zara Iqbal", "Lahore"), ("Hamza Sheikh", "Lahore"), ("Hira Nawaz", "Lahore"),
    ("Usman Tariq", "Lahore"), ("Musa Haider", "Lahore"),
    ("Fatima Noor", "Islamabad"), ("Ali Raza", "Islamabad"), ("Maryam Yousuf", "Islamabad"),
    ("Areeba Siddiqui", "Islamabad"),
    ("Daniyal Aslam", "Rawalpindi"), ("Rabia Bashir", "Multan"), ("Kamran Shah", "Peshawar"),
    ("Nimra Saqi", "Quetta"), ("Taha Rehman", "Faisalabad"), ("Iqra Saleem", "Sialkot"),
    ("Saad Javed", "Hyderabad"), ("Faizan Mehmood", "Gujranwala"), ("Sadia Anwar", "Multan"),
]

PRODUCTS = [
    ("Wireless Mouse", "Electronics", 18.90), ("Mechanical Keyboard", "Electronics", 59.00),
    ("24-inch Monitor", "Electronics", 139.00), ("Noise Cancelling Headset", "Electronics", 99.50),
    ("USB-C Docking Station", "Electronics", 74.00), ("Portable SSD 1TB", "Electronics", 105.00),
    ("Smartphone Stand", "Electronics", 12.50),
    ("Ergonomic Office Chair", "Furniture", 189.00), ("Standing Desk", "Furniture", 279.00),
    ("Bookshelf 5-Tier", "Furniture", 118.00), ("Coffee Table", "Furniture", 85.00),
    ("Storage Cabinet", "Furniture", 96.50), ("Bedside Lamp", "Furniture", 29.90),
    ("Cotton T-Shirt", "Clothing", 14.00), ("Denim Jacket", "Clothing", 54.90),
    ("Running Sneakers", "Clothing", 68.00), ("Wool Sweater", "Clothing", 42.50),
    ("Cargo Trousers", "Clothing", 36.00),
    ("Non-Stick Cookware Set", "Home & Kitchen", 78.00), ("Electric Kettle", "Home & Kitchen", 27.50),
    ("Ceramic Dinner Plates", "Home & Kitchen", 34.00), ("Air Fryer 5L", "Home & Kitchen", 112.00),
    ("Blender 800W", "Home & Kitchen", 55.00),
    ("Yoga Mat", "Sports", 22.00), ("Adjustable Dumbbells", "Sports", 89.00),
    ("Badminton Racket", "Sports", 31.50), ("Cycling Helmet", "Sports", 45.00),
    ("Resistance Bands Set", "Sports", 26.90),
    ("Clean Code", "Books", 32.00), ("Design Patterns", "Books", 38.50),
    ("Data Science Handbook", "Books", 41.00), ("Deep Learning Foundations", "Books", 49.90),
]

STATUSES = ["completed", "completed", "completed", "shipped", "pending", "cancelled", "returned"]
# Electronics and Home & Kitchen carry the catalogue so "highest revenue
# category" has a clear winner rather than a statistical tie.
CATEGORY_WEIGHTS = {"Electronics": 6, "Home & Kitchen": 4, "Furniture": 3, "Clothing": 3, "Sports": 2, "Books": 1}


def seed(url: str, orders: int, seed_value: int = 42) -> None:
    rng = random.Random(seed_value)
    if url.startswith("sqlite"):
        path = url.split(":///")[-1].split("?")[0]
        if path and path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, future=True)
    with engine.begin() as connection:
        for statement in DDL:
            connection.execute(text(statement))
        connection.execute(text(VIEW))

        if connection.execute(text("SELECT COUNT(*) FROM customers")).scalar():
            print("Demo database already has data; skipping.")
            return

        for index, (name, city) in enumerate(CUSTOMERS, start=1):
            connection.execute(
                text("INSERT INTO customers (customer_id, name, email, city) VALUES (:id, :name, :email, :city)"),
                {
                    "id": index,
                    "name": name,
                    "email": f"{name.split()[0].lower()}.{name.split()[1].lower()}@example.com",
                    "city": city,
                },
            )

        catalogue: list[tuple[int, float, str]] = []
        for index, (product_name, category, price) in enumerate(PRODUCTS, start=1):
            connection.execute(
                text(
                    "INSERT INTO products (product_id, product_name, category, price) "
                    "VALUES (:id, :name, :category, :price)"
                ),
                {"id": index, "name": product_name, "category": category, "price": price},
            )
            catalogue.append((index, price, category))

        weighted_products = [item for item in catalogue for _ in range(CATEGORY_WEIGHTS[item[2]])]
        start, end = dt.date(2025, 1, 1), dt.date(2026, 10, 31)
        span = (end - start).days

        for order_id in range(1, orders + 1):
            connection.execute(
                text("INSERT INTO orders (order_id, customer_id, order_date, status) VALUES (:id, :cust, :date, :status)"),
                {
                    "id": order_id,
                    "cust": rng.randint(1, len(CUSTOMERS)),
                    "date": (start + dt.timedelta(days=rng.randint(0, span))).isoformat(),
                    "status": rng.choice(STATUSES),
                },
            )
            for _ in range(rng.randint(1, 4)):
                product_id, price, _ = rng.choice(weighted_products)
                connection.execute(
                    text(
                        "INSERT INTO order_items (order_id, product_id, quantity, unit_price) "
                        "VALUES (:order, :product, :quantity, :price)"
                    ),
                    {
                        "order": order_id,
                        "product": product_id,
                        "quantity": rng.randint(1, 5),
                        "price": price,
                    },
                )

    totals = {
        table: engine.connect().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        for table in ("customers", "products", "orders", "order_items")
    }
    engine.dispose()
    print(f"Seeded {url} -> {totals}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="sqlite:///./data/analytics.db")
    parser.add_argument("--orders", type=int, default=400)
    args = parser.parse_args()
    seed(args.url, args.orders)


if __name__ == "__main__":
    main()
