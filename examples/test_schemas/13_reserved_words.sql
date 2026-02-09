-- 13_reserved_words.sql
-- Expected: WARNING reserved_word for columns/tables using SQL reserved words
-- Triggers: table name "user", column names "password", "type", "status", "data", "key", "order"

CREATE TABLE user (
    id INT PRIMARY KEY AUTO_INCREMENT,
    password VARCHAR(255) NOT NULL,
    type INT,
    status VARCHAR(20),
    data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
