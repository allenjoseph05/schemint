-- 02_terrible_schema.sql
-- Expected: Score <30 (Grade F), multiple critical issues
-- Triggers: missing_primary_key, wrong_data_type, security_risk,
--           missing_timestamps, missing_foreign_key, pii_detected,
--           missing_not_null, reserved_word, naming_convention

CREATE TABLE users (
    id INT,
    name VARCHAR(100),
    email VARCHAR(100),
    password VARCHAR(255),
    ssn VARCHAR(11),
    phone VARCHAR(20),
    type INT,
    status VARCHAR(20),
    user INT
);

CREATE TABLE orders (
    id INT,
    user INT,
    total FLOAT,
    price FLOAT,
    created VARCHAR(255),
    date VARCHAR(50)
);

CREATE TABLE order_items (
    order_id INT,
    product INT,
    qty INT,
    amount FLOAT
);
