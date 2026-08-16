CREATE TABLE integrations (
    id BIGINT PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    api_token VARCHAR(255) NOT NULL,
    webhook_url VARCHAR(2048) NOT NULL,
    refresh_token TEXT,
    created_at TIMESTAMP NOT NULL
);

