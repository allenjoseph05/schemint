ALTER TABLE orders DROP CONSTRAINT orders_user_id_fkey, ADD CONSTRAINT orders_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
