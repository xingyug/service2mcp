-- Tool Compiler Test Target: SQL PostgreSQL
-- Schema matching tests/fixtures/sql_schemas/catalog_live.sql
-- Provides: customers, orders tables + order_summaries view + seed data

CREATE TABLE IF NOT EXISTS customers (
    id       BIGSERIAL PRIMARY KEY,
    email    TEXT      NOT NULL UNIQUE,
    tier     TEXT      DEFAULT 'standard'
);

CREATE TABLE IF NOT EXISTS orders (
    id          BIGSERIAL    PRIMARY KEY,
    customer_id BIGINT       REFERENCES customers(id),
    total_cents INTEGER      NOT NULL,
    notes       TEXT,
    created_at  TIMESTAMPTZ  DEFAULT now()
);

CREATE OR REPLACE VIEW order_summaries AS
SELECT
    o.id          AS order_id,
    c.email       AS customer_email,
    o.total_cents AS total_cents
FROM orders o
JOIN customers c ON c.id = o.customer_id;

-- Seed data
INSERT INTO customers (email, tier) VALUES
    ('alice@example.com', 'gold'),
    ('bob@example.com', 'standard'),
    ('carol@example.com', 'platinum');

INSERT INTO orders (customer_id, total_cents, notes) VALUES
    (1, 2599, 'first order'),
    (1, 4250, 'second order with express shipping'),
    (2, 1099, 'single item'),
    (3, 7800, 'bulk purchase');
