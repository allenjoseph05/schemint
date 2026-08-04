ALTER TYPE user_status RENAME TO user_status_old;
CREATE TYPE user_status AS ENUM ('active');
ALTER TABLE users
    ALTER COLUMN status DROP DEFAULT,
    ALTER COLUMN status TYPE user_status USING status::text::user_status;
DROP TYPE user_status_old;
