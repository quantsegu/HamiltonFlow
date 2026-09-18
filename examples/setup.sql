CREATE TABLE orders AS SELECT * FROM (VALUES
 (1, 10, TIMESTAMP '2026-01-01', 100.0, 'paid'),
 (2, 10, TIMESTAMP '2026-01-02', 50.0, 'paid'),
 (3, 20, TIMESTAMP '2026-01-02', 200.0, 'paid'),
 (4, 30, TIMESTAMP '2026-01-03', 0.0, 'cancelled')
) AS t(order_id, customer_id, ordered_at, amount, status);
CREATE TABLE customers AS SELECT * FROM (VALUES (10,'CH'), (20,'DE'), (30,'CH')) AS t(customer_id,country);
CREATE TABLE time_spine AS SELECT CAST(d AS DATE) AS date_day FROM generate_series(DATE '2026-01-01', DATE '2026-01-05', INTERVAL 1 DAY) t(d);
