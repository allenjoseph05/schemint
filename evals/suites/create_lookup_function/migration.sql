CREATE FUNCTION user_email(user_key INTEGER) RETURNS TEXT
LANGUAGE sql STABLE AS $$
    SELECT email FROM users WHERE id = user_key
$$;
