-- ============================================================
-- PULSE – Supabase Migration
-- Run this in Supabase → SQL Editor
-- ============================================================

-- ── 1. Races catalog ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.races (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name             text NOT NULL,
  modality         text NOT NULL
                     CHECK (modality IN ('sprint','olympic','half_ironman','full_ironman')),
  race_date        date,
  location         text,
  region           text,
  country          text,
  website          text,
  description      text,
  swim_distance    integer,   -- metres
  bike_distance    integer,   -- metres
  run_distance     integer,   -- metres
  elevation_gain   integer,   -- metres
  max_participants integer,
  created_at       timestamptz DEFAULT now()
);

-- Allow public read; only service role can insert/update
ALTER TABLE public.races ENABLE ROW LEVEL SECURITY;

CREATE POLICY "races_public_read" ON public.races
  FOR SELECT USING (true);

CREATE POLICY "races_service_write" ON public.races
  FOR ALL USING (auth.role() = 'service_role');

-- ── 2. Race registrations ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.race_registrations (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  race_id    uuid NOT NULL REFERENCES public.races(id) ON DELETE CASCADE,
  user_id    uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  goal_time  text,
  created_at timestamptz DEFAULT now(),
  UNIQUE (race_id, user_id)
);

ALTER TABLE public.race_registrations ENABLE ROW LEVEL SECURITY;

-- Users can read all registrations (to show athlete lists)
CREATE POLICY "rr_public_read" ON public.race_registrations
  FOR SELECT USING (true);

-- Users can insert their own registration
CREATE POLICY "rr_own_insert" ON public.race_registrations
  FOR INSERT WITH CHECK (auth.uid() = user_id);

-- Users can delete their own registration
CREATE POLICY "rr_own_delete" ON public.race_registrations
  FOR DELETE USING (auth.uid() = user_id);

-- ── 3. Profiles: add missing columns ─────────────────────────
-- The ranking endpoints read xp, level, and preferred_discipline
-- from the profiles table.

ALTER TABLE public.profiles
  ADD COLUMN IF NOT EXISTS xp                  integer DEFAULT 0,
  ADD COLUMN IF NOT EXISTS level               text    DEFAULT 'beginner',
  ADD COLUMN IF NOT EXISTS preferred_discipline text;

-- ── 4. Friendships table (needed by friends ranking) ─────────
-- Skip if it already exists — check in Supabase dashboard first.

CREATE TABLE IF NOT EXISTS public.friendships (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  requester_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  addressee_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','accepted','declined')),
  created_at   timestamptz DEFAULT now(),
  UNIQUE (requester_id, addressee_id)
);

ALTER TABLE public.friendships ENABLE ROW LEVEL SECURITY;

CREATE POLICY "friendships_read" ON public.friendships
  FOR SELECT USING (auth.uid() IN (requester_id, addressee_id));

CREATE POLICY "friendships_insert" ON public.friendships
  FOR INSERT WITH CHECK (auth.uid() = requester_id);

CREATE POLICY "friendships_update" ON public.friendships
  FOR UPDATE USING (auth.uid() = addressee_id);

-- ── 5. Seed data – sample races ──────────────────────────────

INSERT INTO public.races (name, modality, race_date, location, region, country, website, description,
  swim_distance, bike_distance, run_distance, elevation_gain, max_participants)
VALUES

-- Full Ironmans
('IRONMAN Lanzarote',         'full_ironman', '2026-05-23',
 'Puerto del Carmen, Lanzarote', 'Islas Canarias', 'España',
 'https://www.ironmanlanzarote.com',
 'Uno de los Ironman más duros del mundo. Viento y volcanes.',
 3800, 180000, 42195, 2700, 1500),

('IRONMAN Barcelona',         'full_ironman', '2026-10-04',
 'Calella, Barcelona', 'Cataluña', 'España',
 'https://www.ironman.com/im-barcelona',
 'Circuito rápido y llano. Ideal para debutar en la distancia.',
 3800, 180000, 42195, 900, 2500),

('IRONMAN 70.3 Cascais',      'half_ironman',  '2026-09-06',
 'Cascais', 'Lisboa', 'Portugal',
 'https://www.ironman.com/im703-cascais',
 'Costa atlántica portuguesa. Tramo de bici técnico.',
 1900, 90000, 21097, 1200, 2000),

('IRONMAN 70.3 Marbella',     'half_ironman',  '2026-04-19',
 'Marbella', 'Andalucía', 'España',
 'https://www.ironman.com/im703-marbella',
 'Aguas del Mediterráneo y Sierra Nevada de fondo.',
 1900, 90000, 21097, 800, 1800),

-- Olympics
('Triatlón Olímpico Madrid',  'olympic',       '2026-06-14',
 'Casa de Campo, Madrid', 'Comunidad de Madrid', 'España',
 'https://www.triathlonmadrid.com',
 'El clásico de la capital. Circuito urbano exigente.',
 1500, 40000, 10000, 300, 1200),

('Triatlón de Vitoria-Gasteiz', 'olympic',     '2026-07-19',
 'Vitoria-Gasteiz', 'País Vasco', 'España',
 'https://triathlonvitoria.com',
 'Sede histórica del campeonato de Europa ITU.',
 1500, 40000, 10000, 350, 800),

-- Sprints
('Triatlón Sprint Valencia',  'sprint',        '2026-05-10',
 'Port Olímpic, Valencia', 'Comunitat Valenciana', 'España',
 'https://triathlonvalencia.com',
 'Inauguración de la temporada mediterránea. Circuito técnico.',
 750, 20000, 5000, 120, 600),

('Triatlón Sprint Gijón',     'sprint',        '2026-09-20',
 'Playa de Poniente, Gijón', 'Asturias', 'España',
 'https://triathlongijón.com',
 'Aguas frescas del Cantábrico. Cierre de temporada.',
 750, 20000, 5000, 150, 500)

ON CONFLICT DO NOTHING;

-- ── 6. Gym sessions (virtual coach tracking) ──────────────────

CREATE TABLE IF NOT EXISTS public.gym_sessions (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  exercise_id      text NOT NULL,
  sets_completed   integer DEFAULT 0,
  reps_per_set     integer[],
  weight_kg        decimal,
  rpe_per_set      integer[],
  duration_minutes integer,
  started_at       timestamptz,
  completed_at     timestamptz,
  notes            text,
  created_at       timestamptz DEFAULT now()
);

ALTER TABLE public.gym_sessions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "gym_sessions_own" ON public.gym_sessions
  USING (auth.uid() = user_id);

CREATE POLICY "gym_sessions_insert" ON public.gym_sessions
  FOR INSERT WITH CHECK (auth.uid() = user_id);
