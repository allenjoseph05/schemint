CREATE TABLE tenants (id BIGINT PRIMARY KEY, name VARCHAR(255) NOT NULL, created_at TIMESTAMP NOT NULL);
CREATE TABLE users (id BIGINT PRIMARY KEY, tenant_id BIGINT NOT NULL, email VARCHAR(255) NOT NULL, created_at TIMESTAMP NOT NULL, FOREIGN KEY (tenant_id) REFERENCES tenants(id));
CREATE TABLE teams (id BIGINT PRIMARY KEY, tenant_id BIGINT NOT NULL, name VARCHAR(255) NOT NULL, FOREIGN KEY (tenant_id) REFERENCES tenants(id));
CREATE TABLE memberships (id BIGINT PRIMARY KEY, team_id BIGINT NOT NULL, user_id BIGINT NOT NULL, FOREIGN KEY (team_id) REFERENCES teams(id), FOREIGN KEY (user_id) REFERENCES users(id));
CREATE TABLE projects (id BIGINT PRIMARY KEY, tenant_id BIGINT NOT NULL, owner_id BIGINT NOT NULL, name VARCHAR(255) NOT NULL, FOREIGN KEY (tenant_id) REFERENCES tenants(id), FOREIGN KEY (owner_id) REFERENCES users(id));
CREATE TABLE tasks (id BIGINT PRIMARY KEY, project_id BIGINT NOT NULL, assignee_id BIGINT, title VARCHAR(255) NOT NULL, status VARCHAR(32) NOT NULL, FOREIGN KEY (project_id) REFERENCES projects(id), FOREIGN KEY (assignee_id) REFERENCES users(id));
CREATE TABLE comments (id BIGINT PRIMARY KEY, task_id BIGINT NOT NULL, author_id BIGINT NOT NULL, body TEXT NOT NULL, FOREIGN KEY (task_id) REFERENCES tasks(id), FOREIGN KEY (author_id) REFERENCES users(id));
CREATE TABLE attachments (id BIGINT PRIMARY KEY, task_id BIGINT NOT NULL, storage_url VARCHAR(2048) NOT NULL, FOREIGN KEY (task_id) REFERENCES tasks(id));
CREATE TABLE audit_events (id BIGINT PRIMARY KEY, tenant_id BIGINT NOT NULL, actor_id BIGINT, event_type VARCHAR(100) NOT NULL, payload TEXT, occurred_at TIMESTAMP NOT NULL, FOREIGN KEY (tenant_id) REFERENCES tenants(id));
CREATE TABLE invoices (id BIGINT PRIMARY KEY, tenant_id BIGINT NOT NULL, amount FLOAT NOT NULL, created_at TIMESTAMP NOT NULL, FOREIGN KEY (tenant_id) REFERENCES tenants(id));
CREATE TABLE api_credentials (id BIGINT PRIMARY KEY, tenant_id BIGINT NOT NULL, api_token VARCHAR(255) NOT NULL, created_at TIMESTAMP NOT NULL, FOREIGN KEY (tenant_id) REFERENCES tenants(id));
CREATE TABLE feature_flags (id BIGINT PRIMARY KEY, tenant_id BIGINT NOT NULL, flag_name VARCHAR(100) NOT NULL, enabled BOOLEAN NOT NULL, FOREIGN KEY (tenant_id) REFERENCES tenants(id));

