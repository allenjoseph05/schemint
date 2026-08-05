ALTER TABLE items ADD CONSTRAINT quantity_upper_bound CHECK (quantity <= 1000);
