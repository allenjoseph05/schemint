-- 07_inefficient_types.sql
-- Expected: SUGGESTION inefficient_type for boolean-like INT columns
--           and TEXT used for short string columns
-- Triggers: is_active/has_email as INT, name/status/title as TEXT

CREATE TABLE users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name TEXT,
    title TEXT,
    status TEXT,
    is_active INT DEFAULT 1,
    is_admin INT DEFAULT 0,
    has_email INT DEFAULT 0,
    has_verified INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
