-- RADIO STATION STUDIO PRO — migração 001 (Fase 1)
-- SQL portável: executa em PostgreSQL 14+ (produção) e SQLite 3.35+ (desenvolvimento/testes).
-- Datas em TEXT ISO-8601 UTC (ordenáveis lexicograficamente); booleanos em INTEGER 0/1.
-- Toda tabela de dados de emissora carrega station_id para isolamento entre tenants.

CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_superadmin INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    mfa_secret_enc TEXT,
    mfa_enabled INTEGER NOT NULL DEFAULT 0,
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE refresh_tokens (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    user_agent TEXT,
    ip TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_refresh_user ON refresh_tokens(user_id);

CREATE TABLE stations (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    slogan TEXT,
    description TEXT,
    timezone TEXT NOT NULL DEFAULT 'America/Sao_Paulo',
    primary_color TEXT NOT NULL DEFAULT '#1f8bff',
    show_history INTEGER NOT NULL DEFAULT 1,
    plan TEXT NOT NULL DEFAULT 'demo',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE station_members (
    station_id TEXT NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (station_id, user_id)
);
CREATE INDEX ix_members_user ON station_members(user_id);

CREATE TABLE stream_configs (
    station_id TEXT PRIMARY KEY REFERENCES stations(id) ON DELETE CASCADE,
    server_type TEXT NOT NULL DEFAULT 'icecast',
    host TEXT NOT NULL DEFAULT '127.0.0.1',
    port INTEGER NOT NULL DEFAULT 8000,
    use_tls INTEGER NOT NULL DEFAULT 0,
    mount TEXT NOT NULL DEFAULT '/live',
    source_user TEXT NOT NULL DEFAULT 'source',
    source_password_enc TEXT,
    format TEXT NOT NULL DEFAULT 'mp3',
    bitrate_kbps INTEGER NOT NULL DEFAULT 128,
    sample_rate INTEGER NOT NULL DEFAULT 44100,
    channels INTEGER NOT NULL DEFAULT 2,
    public_listen_url TEXT,
    status_url TEXT,
    stream_name TEXT,
    stream_genre TEXT,
    stream_description TEXT,
    enabled INTEGER NOT NULL DEFAULT 0,
    fallback_media_id TEXT,
    normalize INTEGER NOT NULL DEFAULT 1,
    target_lufs DOUBLE PRECISION NOT NULL DEFAULT -16.0,
    rotation_fallback INTEGER NOT NULL DEFAULT 1,
    no_repeat_window INTEGER NOT NULL DEFAULT 10,
    updated_at TEXT NOT NULL
);

CREATE TABLE media (
    id TEXT PRIMARY KEY,
    station_id TEXT NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'music',
    title TEXT NOT NULL,
    artist TEXT,
    album TEXT,
    genre TEXT,
    year INTEGER,
    bpm DOUBLE PRECISION,
    rating INTEGER,
    tags TEXT NOT NULL DEFAULT '[]',
    storage_key TEXT NOT NULL,
    cover_key TEXT,
    original_filename TEXT,
    container TEXT,
    size_bytes BIGINT NOT NULL,
    sha256 TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    codec TEXT,
    bitrate_kbps INTEGER,
    sample_rate INTEGER,
    channels INTEGER,
    loudness_lufs DOUBLE PRECISION,
    true_peak_db DOUBLE PRECISION,
    lead_silence_ms INTEGER,
    trail_silence_ms INTEGER,
    cue_in_ms INTEGER NOT NULL DEFAULT 0,
    cue_out_ms INTEGER,
    fade_in_ms INTEGER NOT NULL DEFAULT 0,
    fade_out_ms INTEGER NOT NULL DEFAULT 0,
    analysis_status TEXT NOT NULL DEFAULT 'pending',
    analysis_error TEXT,
    license_status TEXT NOT NULL DEFAULT 'unknown',
    rights_holder TEXT,
    license_ref TEXT,
    license_expires_at TEXT,
    isrc TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (station_id, sha256)
);
CREATE INDEX ix_media_station ON media(station_id, kind);

CREATE TABLE queue_items (
    id TEXT PRIMARY KEY,
    station_id TEXT NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    media_id TEXT NOT NULL REFERENCES media(id),
    position DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    source TEXT NOT NULL DEFAULT 'manual',
    added_by TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT
);
CREATE INDEX ix_queue_station ON queue_items(station_id, status, position);

CREATE TABLE play_log (
    id TEXT PRIMARY KEY,
    station_id TEXT NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    media_id TEXT,
    queue_item_id TEXT,
    title TEXT NOT NULL,
    artist TEXT,
    kind TEXT,
    isrc TEXT,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    planned_ms INTEGER,
    played_ms INTEGER,
    end_reason TEXT,
    session_id TEXT
);
CREATE INDEX ix_playlog_station ON play_log(station_id, started_at);

CREATE TABLE playout_state (
    station_id TEXT PRIMARY KEY REFERENCES stations(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'offline',
    session_id TEXT,
    pid INTEGER,
    hostname TEXT,
    encoder_connected INTEGER NOT NULL DEFAULT 0,
    encoder_since TEXT,
    reconnects INTEGER NOT NULL DEFAULT 0,
    now_media_id TEXT,
    now_play_id TEXT,
    now_title TEXT,
    now_artist TEXT,
    now_source TEXT,
    now_started_at TEXT,
    now_duration_ms INTEGER,
    next_media_id TEXT,
    next_title TEXT,
    listeners INTEGER,
    listeners_at TEXT,
    last_error TEXT,
    host_load DOUBLE PRECISION,
    buffer_ms INTEGER,
    heartbeat_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE playout_commands (
    id TEXT PRIMARY KEY,
    station_id TEXT NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    command TEXT NOT NULL,
    payload TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_by TEXT,
    created_at TEXT NOT NULL,
    processed_at TEXT
);
CREATE INDEX ix_cmd_station ON playout_commands(station_id, status);

CREATE TABLE listener_snapshots (
    id TEXT PRIMARY KEY,
    station_id TEXT NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    taken_at TEXT NOT NULL,
    listeners INTEGER NOT NULL,
    peak INTEGER,
    source TEXT NOT NULL
);
CREATE INDEX ix_listeners_station ON listener_snapshots(station_id, taken_at);

CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    station_id TEXT,
    payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    last_error TEXT,
    run_after TEXT NOT NULL,
    locked_at TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX ix_jobs_status ON jobs(status, run_after);

CREATE TABLE audit_log (
    id TEXT PRIMARY KEY,
    station_id TEXT,
    user_id TEXT,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    details TEXT,
    ip TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_audit_station ON audit_log(station_id, created_at);
