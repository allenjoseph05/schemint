-- 03_security_risks.sql
-- Expected: Score F, 4 CRITICAL security_risk, 7+ WARNING pii_detected
-- Triggers: security_risk (password, secret, token, api_key) -> CRITICAL
--           pii_detected (email, ssn, phone, address, credit_card) -> WARNING

CREATE TABLE user_credentials (
    id INT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(100) NOT NULL,
    password VARCHAR(255),
    secret VARCHAR(255),
    token VARCHAR(500),
    api_key VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE customer_data (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255),
    ssn VARCHAR(11),
    phone VARCHAR(20),
    address TEXT,
    date_of_birth VARCHAR(10),
    credit_card VARCHAR(19),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
