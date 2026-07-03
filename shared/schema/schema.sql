CREATE TABLE IF NOT EXISTS document_results (
    id            BIGSERIAL   PRIMARY KEY,
    radicado      TEXT        NOT NULL,
    cedula        TEXT,
    request_json  JSONB,
    response_json JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_document_results_radicado ON document_results (radicado);
CREATE INDEX IF NOT EXISTS idx_document_results_cedula   ON document_results (cedula);
