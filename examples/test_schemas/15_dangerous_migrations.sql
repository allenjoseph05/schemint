-- 15_dangerous_migrations.sql
-- Expected: CRITICAL blocking_migration, destructive_change findings
-- These are caught by the dangerous pattern detector (not RuleAnalyzer)
-- Use with: handler._check_dangerous_patterns() or full CI ingest

-- Blocking: ADD COLUMN with DEFAULT on existing table (locks table)
ALTER TABLE users ADD COLUMN is_verified BOOLEAN DEFAULT false;
ALTER TABLE orders ADD COLUMN discount DECIMAL(10,2) DEFAULT 0.00;

-- Destructive: DROP COLUMN (data loss)
ALTER TABLE users DROP COLUMN legacy_field;
ALTER TABLE products DROP COLUMN old_description;

-- Destructive: DROP TABLE (data loss)
DROP TABLE IF EXISTS old_sessions;
DROP TABLE temp_data;

-- Unsafe: ADD NOT NULL without DEFAULT (fails on existing rows)
ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL;
