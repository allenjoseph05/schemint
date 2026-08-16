CREATE TABLE payments (
    id BIGINT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    credit_card_number VARCHAR(32) NOT NULL,
    cvv VARCHAR(8) NOT NULL,
    amount DECIMAL(12,2) NOT NULL,
    created_at TIMESTAMP NOT NULL
);

