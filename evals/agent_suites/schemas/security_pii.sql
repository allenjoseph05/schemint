CREATE TABLE patients (
    id BIGINT PRIMARY KEY,
    full_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL,
    ssn VARCHAR(32) NOT NULL,
    diagnosis TEXT,
    created_at TIMESTAMP NOT NULL
);

