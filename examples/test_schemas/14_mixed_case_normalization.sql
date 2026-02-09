-- 14_mixed_case_normalization.sql
-- Expected: All identifiers normalized to lowercase
-- Test that: Users -> users, UserName -> username, Email -> email
-- Analysis should work correctly regardless of SQL casing

CREATE TABLE Users (
    ID INT PRIMARY KEY AUTO_INCREMENT,
    UserName VARCHAR(100) NOT NULL,
    Email VARCHAR(255) NOT NULL UNIQUE,
    Password_Hash VARCHAR(255) NOT NULL,
    IsActive BOOLEAN DEFAULT TRUE,
    Created_At TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    Updated_At TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE Orders (
    ID INT PRIMARY KEY AUTO_INCREMENT,
    User_Id INT NOT NULL,
    Total DECIMAL(10,2) NOT NULL,
    Status VARCHAR(20) NOT NULL DEFAULT 'pending',
    Created_At TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    Updated_At TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (User_Id) REFERENCES Users(ID) ON DELETE CASCADE
);
