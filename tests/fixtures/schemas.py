"""Test fixtures and sample schemas."""

# Schema with many issues
BAD_SCHEMA = """
CREATE TABLE users (
    id INT,
    name VARCHAR(100),
    email VARCHAR(100),
    password VARCHAR(255),
    type INT
);

CREATE TABLE orders (
    id INT,
    user INT,
    total FLOAT,
    created VARCHAR(255)
);

CREATE TABLE order_items (
    order_id INT,
    product INT,
    qty INT,
    price FLOAT
);
"""

# Well-designed schema
GOOD_SCHEMA = """
CREATE TABLE users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    name VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE orders (
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT NOT NULL,
    total DECIMAL(10,2) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX idx_orders_user_id ON orders(user_id);
CREATE INDEX idx_orders_status ON orders(status);
"""

# Simple schema for basic tests
SIMPLE_SCHEMA = """
CREATE TABLE products (
    id INT PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    price DECIMAL(10,2) NOT NULL
);
"""
