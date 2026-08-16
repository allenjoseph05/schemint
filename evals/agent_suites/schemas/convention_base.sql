CREATE TABLE customers (
    id INT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    old_email VARCHAR(255),
    balance FLOAT,
    created_at TIMESTAMP NOT NULL
);

