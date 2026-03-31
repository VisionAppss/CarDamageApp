-- schema.sql
-- Выполняется автоматически при первом запуске контейнера PostgreSQL

-- Профили пользователей
CREATE TABLE IF NOT EXISTS profiles (
  id             TEXT PRIMARY KEY,      -- user_id из Supabase Auth (UUID)
  email          TEXT UNIQUE NOT NULL,
  created_at     TIMESTAMPTZ DEFAULT NOW(),
  analyses_count INTEGER DEFAULT 0,
  is_paid        BOOLEAN DEFAULT FALSE
);

-- История анализов
CREATE TABLE IF NOT EXISTS analyses (
  id              BIGSERIAL PRIMARY KEY,
  user_id         TEXT REFERENCES profiles(id) ON DELETE CASCADE,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  result          JSONB,
  photo_name      TEXT,
  inspection_type TEXT
);

CREATE INDEX IF NOT EXISTS analyses_user_id_idx ON analyses(user_id);
CREATE INDEX IF NOT EXISTS analyses_created_at_idx ON analyses(created_at DESC);

-- Аналитика событий
CREATE TABLE IF NOT EXISTS events (
  id         BIGSERIAL PRIMARY KEY,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  event      TEXT NOT NULL,
  params     JSONB DEFAULT '{}',
  user_id    TEXT,
  url        TEXT,
  ua         TEXT
);

CREATE INDEX IF NOT EXISTS events_event_idx      ON events(event);
CREATE INDEX IF NOT EXISTS events_created_at_idx ON events(created_at DESC);
CREATE INDEX IF NOT EXISTS events_user_id_idx    ON events(user_id);
