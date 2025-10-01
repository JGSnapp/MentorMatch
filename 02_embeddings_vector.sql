-- Migration: enable pgvector-backed embeddings for users, topics and roles
BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE users
    ALTER COLUMN embeddings TYPE vector(1536)
    USING NULL;

ALTER TABLE topics
    ALTER COLUMN embeddings TYPE vector(1536)
    USING NULL;

ALTER TABLE roles
    ADD COLUMN IF NOT EXISTS embeddings vector(1536);

CREATE INDEX IF NOT EXISTS idx_users_embeddings
    ON users USING ivfflat (embeddings vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_topics_embeddings
    ON topics USING ivfflat (embeddings vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_roles_embeddings
    ON roles USING ivfflat (embeddings vector_cosine_ops) WITH (lists = 100);

COMMIT;
