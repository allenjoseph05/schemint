CREATE FUNCTION keep_session_token() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RETURN NEW;
END;
$$;

CREATE TRIGGER sessions_keep_token
BEFORE UPDATE ON sessions
FOR EACH ROW EXECUTE FUNCTION keep_session_token();
