CREATE TABLE audit_events (
    id BIGSERIAL PRIMARY KEY,
    external_id TEXT NOT NULL,
    success BOOLEAN NOT NULL DEFAULT FALSE,
    payload JSONB,
    amount NUMERIC(12,2) NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL
);

CREATE VIEW audit_event_rollups AS
SELECT external_id, success, amount
FROM audit_events;
