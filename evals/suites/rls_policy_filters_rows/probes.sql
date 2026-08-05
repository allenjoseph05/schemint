-- name: assume_app_reader
SET ROLE app_reader;

-- name: visible_accounts
SELECT count(*) AS visible FROM accounts;

-- name: restore_admin
RESET ROLE;
