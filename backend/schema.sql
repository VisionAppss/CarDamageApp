-- schema.sql
-- Выполняется автоматически при первом запуске контейнера PostgreSQL

-- Профили пользователей
CREATE TABLE IF NOT EXISTS profiles (
  id             TEXT PRIMARY KEY,      -- user_id из Supabase Auth (UUID)
  email          TEXT UNIQUE NOT NULL,
  created_at     TIMESTAMPTZ DEFAULT NOW(),
  analyses_count INTEGER DEFAULT 0,
  is_paid        BOOLEAN DEFAULT FALSE,
  consent_given  BOOLEAN DEFAULT FALSE
);

-- История анализов
CREATE TABLE IF NOT EXISTS analyses (
  id              BIGSERIAL PRIMARY KEY,
  user_id         TEXT REFERENCES profiles(id) ON DELETE CASCADE,
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  result          JSONB,
  photo_name      TEXT,
  inspection_type TEXT,
  photo_data      TEXT,
  photo_mime      TEXT
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

-- Платежи YooKassa
CREATE TABLE IF NOT EXISTS payments (
  id         TEXT PRIMARY KEY,           -- YooKassa payment ID
  user_id    TEXT REFERENCES profiles(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  status     TEXT DEFAULT 'pending',
  amount     NUMERIC(10,2)
);

CREATE INDEX IF NOT EXISTS payments_user_id_idx ON payments(user_id);

-- OTP-коды для входа
CREATE TABLE IF NOT EXISTS auth_codes (
  id         BIGSERIAL PRIMARY KEY,
  email      TEXT NOT NULL,
  code       TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  used       BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS auth_codes_email_idx ON auth_codes(email, expires_at);

-- Сессии пользователей
CREATE TABLE IF NOT EXISTS sessions (
  token      TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  expires_at TIMESTAMPTZ NOT NULL,
  last_used  TIMESTAMPTZ DEFAULT NOW(),
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS sessions_user_id_idx ON sessions(user_id);

-- Миграции для существующих БД (игнорируют ошибки если колонка уже есть)
DO $$ BEGIN
  ALTER TABLE profiles ADD COLUMN IF NOT EXISTS consent_given BOOLEAN DEFAULT FALSE;
EXCEPTION WHEN others THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE analyses ADD COLUMN IF NOT EXISTS photo_data TEXT;
EXCEPTION WHEN others THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE analyses ADD COLUMN IF NOT EXISTS photo_mime TEXT;
EXCEPTION WHEN others THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE profiles ADD COLUMN IF NOT EXISTS analysis_credits INT NOT NULL DEFAULT 0;
EXCEPTION WHEN others THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE profiles ADD COLUMN IF NOT EXISTS pdf_credits INT NOT NULL DEFAULT 0;
EXCEPTION WHEN others THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE payments ADD COLUMN IF NOT EXISTS type TEXT DEFAULT 'analysis';
EXCEPTION WHEN others THEN NULL; END $$;
