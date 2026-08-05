CREATE VIEW active_users AS
SELECT id, email FROM users WHERE status = 'active';
