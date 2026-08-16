CREATE TABLE invoices (
    id BIGINT PRIMARY KEY,
    customer_id BIGINT NOT NULL,
    amount FLOAT NOT NULL,
    tax FLOAT NOT NULL,
    created_at TIMESTAMP NOT NULL
);

