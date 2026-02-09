-- 08_missing_constraints.sql
-- Expected: SUGGESTION missing_constraint for email without UNIQUE,
--           status/type without ENUM
-- Also: WARNING missing_not_null for nullable name/email/status

CREATE TABLE users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    email VARCHAR(255),
    name VARCHAR(100),
    username VARCHAR(50),
    status VARCHAR(20),
    type VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
