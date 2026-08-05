CREATE OR REPLACE FUNCTION validate_item_quantity() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'updates disabled'; END; $$;
