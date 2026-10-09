-- ====================================================================
-- Viral Zone 🔥 — PostgreSQL Safe Schema Setup & Migration Script
-- ====================================================================
-- This script is 100% SAFE: No DROP TABLE, No TRUNCATE, No Data Loss.
-- Resolves: NotNullViolationError in column "name" of relation "contacts"

-- 1. Contacts / Users Table
CREATE TABLE IF NOT EXISTS contacts (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE,
    name TEXT NOT NULL DEFAULT 'Viral Zone User',
    username TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Ensure telegram_id column exists if table was created previously
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='contacts' AND column_name='telegram_id'
    ) THEN
        ALTER TABLE contacts ADD COLUMN telegram_id BIGINT UNIQUE;
    END IF;
END $$;

-- Fix NotNullViolation on 'name' column: ensure default and NOT NULL safety
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='contacts' AND column_name='name'
    ) THEN
        ALTER TABLE contacts ADD COLUMN name TEXT NOT NULL DEFAULT 'Viral Zone User';
    ELSE
        -- Update any existing NULL names to default before enforcing constraint
        UPDATE contacts SET name = 'Viral Zone User' WHERE name IS NULL;
        ALTER TABLE contacts ALTER COLUMN name SET DEFAULT 'Viral Zone User';
    END IF;
END $$;

-- 2. Categories Table
CREATE TABLE IF NOT EXISTS categories (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    icon TEXT DEFAULT '🎬',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Insert Default Categories safely
INSERT INTO categories (name, icon) VALUES 
('🎵 গান', '🎵'),
('🎭 নাটক', '🎭'),
('🎬 ভিডিও', '🎬'),
('📸 ফটো', '📸'),
('🎥 মুভি', '🎥')
ON CONFLICT (name) DO NOTHING;

-- 3. Contents Table
CREATE TABLE IF NOT EXISTS contents (
    id SERIAL PRIMARY KEY,
    file_id TEXT NOT NULL,
    media_type TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    category TEXT NOT NULL,
    thumbnail_file_id TEXT,
    views INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Ensure views column exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='contents' AND column_name='views'
    ) THEN
        ALTER TABLE contents ADD COLUMN views INT DEFAULT 0;
    END IF;
END $$;
