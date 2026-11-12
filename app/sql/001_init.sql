CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE knowledge_bases (
    id uuid PRIMARY KEY,
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
    description text NOT NULL DEFAULT '',
    embedding_profile jsonb NOT NULL,
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE documents (
    id uuid PRIMARY KEY,
    kb_id uuid NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    filename text NOT NULL,
    media_type text NOT NULL CHECK (media_type IN ('pdf', 'docx')),
    sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes bigint NOT NULL CHECK (size_bytes > 0),
    source_path text NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'processing', 'ready', 'failed')),
    stage text CHECK (stage IN ('parse', 'vision', 'chunk', 'embed', 'graph', 'publish')),
    attempt integer NOT NULL DEFAULT 1 CHECK (attempt > 0),
    progress integer NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    pipeline_version text NOT NULL,
    processing_meta jsonb NOT NULL DEFAULT '{}'::jsonb,
    asset_manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_code text,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    ready_at timestamptz,
    UNIQUE (kb_id, id),
    UNIQUE (kb_id, sha256)
);

CREATE TABLE chunks (
    id uuid PRIMARY KEY,
    kb_id uuid NOT NULL,
    document_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    content_type text NOT NULL CHECK (content_type IN ('text', 'image')),
    heading_path jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence_text text NOT NULL CHECK (char_length(evidence_text) > 0),
    retrieval_text text NOT NULL,
    title_tokens text NOT NULL DEFAULT '',
    body_tokens text NOT NULL,
    source_spans jsonb NOT NULL,
    asset_id text,
    token_count integer NOT NULL CHECK (token_count BETWEEN 1 AND 512),
    embedding vector(512) NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('simple'::regconfig, title_tokens), 'A') ||
        setweight(to_tsvector('simple'::regconfig, body_tokens), 'B')
    ) STORED,
    FOREIGN KEY (kb_id, document_id)
        REFERENCES documents(kb_id, id) ON DELETE CASCADE,
    UNIQUE (kb_id, id),
    UNIQUE (document_id, ordinal)
);

CREATE TABLE entities (
    id uuid PRIMARY KEY,
    kb_id uuid NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    name text NOT NULL,
    normalized_name text NOT NULL CHECK (char_length(normalized_name) > 0),
    entity_type text NOT NULL CHECK (entity_type IN
        ('person', 'organization', 'project', 'product', 'location', 'concept', 'other')),
    UNIQUE (kb_id, id),
    UNIQUE (kb_id, entity_type, normalized_name)
);

CREATE TABLE relations (
    id uuid PRIMARY KEY,
    kb_id uuid NOT NULL,
    source_entity_id uuid NOT NULL,
    predicate text NOT NULL CHECK (char_length(predicate) BETWEEN 1 AND 100),
    target_entity_id uuid NOT NULL,
    evidence_chunk_id uuid NOT NULL,
    quote text NOT NULL CHECK (char_length(quote) > 0),
    quote_start integer NOT NULL CHECK (quote_start >= 0),
    quote_end integer NOT NULL CHECK (quote_end > quote_start),
    CHECK (source_entity_id <> target_entity_id),
    FOREIGN KEY (kb_id, source_entity_id)
        REFERENCES entities(kb_id, id) ON DELETE CASCADE,
    FOREIGN KEY (kb_id, target_entity_id)
        REFERENCES entities(kb_id, id) ON DELETE CASCADE,
    FOREIGN KEY (kb_id, evidence_chunk_id)
        REFERENCES chunks(kb_id, id) ON DELETE CASCADE,
    UNIQUE (kb_id, source_entity_id, predicate, target_entity_id,
            evidence_chunk_id, quote_start, quote_end)
);

CREATE INDEX documents_queue_idx ON documents (created_at, id)
    WHERE status = 'queued';
CREATE INDEX chunks_kb_doc_idx ON chunks (kb_id, document_id);
CREATE INDEX chunks_search_idx ON chunks USING GIN (search_vector);
CREATE INDEX relations_source_idx ON relations (kb_id, source_entity_id);
CREATE INDEX relations_target_idx ON relations (kb_id, target_entity_id);
CREATE INDEX relations_evidence_idx ON relations (kb_id, evidence_chunk_id);
