-- 04_security_best_practices.sql
-- Expected: 0 security_risk, 0 pii_detected findings
-- Shows the CORRECT way to handle sensitive data

CREATE TABLE user_credentials (
    id INT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(100) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    secret_encrypted VARCHAR(500) NOT NULL,
    token_hash VARCHAR(255),
    api_key_encrypted VARCHAR(500),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE customer_data (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL,
    email_encrypted VARCHAR(255) NOT NULL,
    ssn_hash VARCHAR(64) NOT NULL,
    phone_masked VARCHAR(20),
    address_encrypted TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
