-- name: assume_app_reader_subtle
SET ROLE app_reader;
-- name: tenant_visibility_contract
SELECT array_agg(name ORDER BY name) FROM accounts;
-- name: restore_admin_subtle
RESET ROLE;
