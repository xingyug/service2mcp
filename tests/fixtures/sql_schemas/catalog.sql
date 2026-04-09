CREATE TABLE customers (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    tier TEXT NOT NULL DEFAULT 'standard'
);

CREATE TABLE orders (
    id BIGSERIAL PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES customers(id),
    total_cents INTEGER NOT NULL,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE VIEW order_summaries AS
SELECT
    orders.id AS order_id,
    customers.email AS customer_email,
    orders.total_cents
FROM orders
JOIN customers ON customers.id = orders.customer_id;
