CREATE MATERIALIZED VIEW order_totals AS
SELECT user_id, sum(amount) AS total FROM orders GROUP BY user_id;
