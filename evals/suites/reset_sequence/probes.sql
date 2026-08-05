-- name: ticket_sequence_state
SELECT last_value, is_called FROM ticket_id_seq;
