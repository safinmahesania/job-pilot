-- jobpilot_schema_postgres.sql
--
-- Multi-user Postgres schema for JobPilot (Supabase).
--
--   * jobs          — a SHARED pool of postings. No per-user columns: no score, no
--                     status. Every user sees the same job rows.
--   * user_jobs     — one row per (user, job) the user has been shown: their score,
--                     status, notes and follow-up state. This is where "my feed" lives.
--   * user_profiles — each user's profile as JSONB (identity/contact/history/skills).
--   * users         — mirrors auth.users, plus an is_admin flag for admin-only routes.
--
-- Everything user-scoped carries RLS as a backstop (using auth.uid()); the FastAPI
-- backend connects directly and filters by user_id itself. Applying this file assumes
-- an `auth` schema with `auth.users(id uuid, email text)` and `auth.uid()` already
-- exist — Supabase provides them, and the test harness shims them in before applying.
--
-- Idempotent: safe to run more than once (IF NOT EXISTS, DROP POLICY IF EXISTS).

create extension if not exists pgcrypto;

-- ── users: a public mirror of auth.users ────────────────────────────────────
create table if not exists public.users (
    id         uuid primary key references auth.users (id) on delete cascade,
    email      text,
    is_admin   boolean not null default false,
    created_at timestamptz not null default now()
);

-- Auto-create the public.users row whenever someone signs up in auth.users.
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
    insert into public.users (id, email) values (new.id, new.email)
    on conflict (id) do nothing;
    return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();

-- ── user_profiles: the whole profile as one JSONB document ───────────────────
create table if not exists public.user_profiles (
    user_id    uuid primary key references public.users (id) on delete cascade,
    profile    jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now()
);

-- ── user_settings: per-user key/value (e.g. score_threshold override) ────────
create table if not exists public.user_settings (
    user_id uuid not null references public.users (id) on delete cascade,
    key     text not null,
    value   text,
    primary key (user_id, key)
);

-- ── app_settings: global key/value the admin controls ────────────────────────
create table if not exists public.app_settings (
    key   text primary key,
    value text
);
insert into public.app_settings (key, value) values
    ('score_threshold',   '60'),
    ('scheduler_enabled', '0'),
    ('run_interval_hours','8'),
    ('notify_enabled',    '1'),
    ('scoring_enabled',   '1'),
    ('generation_enabled','1')
on conflict (key) do nothing;

-- ── jobs: the shared pool (posting + LLM-extracted fields, no per-user data) ──
create table if not exists public.jobs (
    id               bigint generated always as identity primary key,
    dedupe_hash      text unique not null,
    source           text,
    source_url       text,
    apply_url        text,
    title            text,
    company          text,
    location         text,
    remote           boolean,
    salary_min       integer,
    salary_max       integer,
    description      text,
    posted_date      text,
    fetched_at       timestamptz default now(),
    job_type         text,
    deadline         text,
    language         text,
    quality_flags    jsonb,
    -- Structured fields pulled out of the description by the LLM extraction pass.
    -- NULL = never extracted; '' = looked, found nothing. extracted_at is the marker.
    work_mode        text,
    seniority_level  text,
    location_detail  text,
    salary_text      text,
    benefits         text,
    responsibilities text,
    requirements     text,
    nice_to_have     text,
    tech_stack       text,
    about_company    text,
    instructions     text,
    extracted_at     text
);

-- ── user_jobs: the per-user view of a pool job ───────────────────────────────
create table if not exists public.user_jobs (
    user_id         uuid not null references public.users (id) on delete cascade,
    job_id          bigint not null references public.jobs (id) on delete cascade,
    score           real,
    skills_score    real,
    seniority_score real,
    domain_score    real,
    rationale       text,
    status          text not null default 'surfaced',
    applied_on      date,
    notes           text,
    last_viewed_at  timestamptz,
    followed_up_on  timestamptz,
    followup_snooze date,
    served_at       timestamptz default now(),
    primary key (user_id, job_id)
);
create index if not exists idx_user_jobs_user   on public.user_jobs (user_id);
create index if not exists idx_user_jobs_status on public.user_jobs (user_id, status);

-- ── seen: the global fetch-dedup log (which hashes we've already decided on) ──
create table if not exists public.seen (
    dedupe_hash text primary key,
    decision    text not null,
    score       real,
    first_seen  timestamptz default now()
);

-- ── source_health: per-board fetch health (admin-facing) ─────────────────────
create table if not exists public.source_health (
    name         text primary key,
    ats          text,
    fetched      integer default 0,
    kept         integer default 0,
    status       text,
    error        text,
    last_run     text,
    zero_streak  integer default 0,
    error_streak integer default 0,
    last_ok      text,
    alerted      boolean default false
);

-- ── runs: the fetch-run log ──────────────────────────────────────────────────
create table if not exists public.runs (
    id         bigint generated always as identity primary key,
    started_at timestamptz default now(),
    kind       text,
    fetched    integer default 0,
    seen       integer default 0,
    dropped    integer default 0,
    trashed    integer default 0,
    kept       integer default 0,
    errors     integer default 0
);

-- ── errors: kept exceptions, so a failure is readable after the fact ─────────
create table if not exists public.errors (
    id        bigint generated always as identity primary key,
    at        timestamptz default now(),
    where_    text,
    kind      text,
    message   text,
    traceback text,
    notified  boolean default false
);
create index if not exists idx_errors_at on public.errors (at desc);

-- ── llm_usage: per-day, per-provider token/request usage ─────────────────────
create table if not exists public.llm_usage (
    day      text not null,
    provider text not null,
    tokens   integer default 0,
    requests integer default 0,
    primary key (day, provider)
);

-- ── application_answers: what you answered on a form, per user + job ─────────
create table if not exists public.application_answers (
    id         bigint generated always as identity primary key,
    user_id    uuid not null references public.users (id) on delete cascade,
    job_id     bigint not null references public.jobs (id) on delete cascade,
    question   text not null,
    answer     text not null,
    created_at timestamptz default now(),
    unique (user_id, job_id, question)
);

-- ── materials: the resume/cover a user generated for a job ───────────────────
create table if not exists public.materials (
    id         bigint generated always as identity primary key,
    user_id    uuid not null references public.users (id) on delete cascade,
    job_id     bigint not null references public.jobs (id) on delete cascade,
    kind       text not null,
    content    text not null,
    provider   text,
    created_at timestamptz default now(),
    unique (user_id, job_id, kind)
);

-- ── notifications: a copy of what the app told a user ────────────────────────
create table if not exists public.notifications (
    id         bigint generated always as identity primary key,
    user_id    uuid not null references public.users (id) on delete cascade,
    text       text not null,
    created_at timestamptz default now(),
    seen       boolean default false
);
create index if not exists idx_notifications_user on public.notifications (user_id, id desc);

-- ── Row-Level Security (backstop; the API also filters by user_id) ───────────
-- The shared pool is readable by any signed-in user; per-user tables are private.
alter table public.jobs                enable row level security;
alter table public.user_jobs           enable row level security;
alter table public.user_profiles       enable row level security;
alter table public.user_settings       enable row level security;
alter table public.materials           enable row level security;
alter table public.application_answers enable row level security;
alter table public.notifications       enable row level security;
alter table public.users               enable row level security;

drop policy if exists jobs_read on public.jobs;
create policy jobs_read on public.jobs
    for select to authenticated using (true);

drop policy if exists users_self on public.users;
create policy users_self on public.users
    for select to authenticated using (id = auth.uid());

do $$
declare t text;
begin
    foreach t in array array['user_jobs','user_profiles','user_settings',
                             'materials','application_answers','notifications']
    loop
        execute format('drop policy if exists %I_owner on public.%I', t, t);
        execute format(
            'create policy %I_owner on public.%I for all to authenticated '
            'using (user_id = auth.uid()) with check (user_id = auth.uid())', t, t);
    end loop;
end $$;
