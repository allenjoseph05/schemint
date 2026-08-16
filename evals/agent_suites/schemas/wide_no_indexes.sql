CREATE TABLE events (
    id BIGINT PRIMARY KEY,
    tenant_id BIGINT NOT NULL,
    actor_id BIGINT NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    payload TEXT,
    source VARCHAR(100),
    occurred_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL
);

