-- AIPT pipeline schema. Idempotent: safe to run more than once.
-- Apply via: uv run python -m pipeline apply-schema   (needs SUPABASE_DB_URL)
-- or paste into the Supabase SQL editor.

create table if not exists companies (
  cik              bigint primary key,
  ticker           text not null,
  name             text not null,
  exchange         text,
  sic              text,
  sic_description  text,
  sector_bucket    text not null default 'other'
                   check (sector_bucket in ('saas','fintech','edtech','healthcare','other')),
  market_cap       numeric,
  employee_count   integer,
  website          text,
  hq_state         text,
  ipo_date         date,
  status           text not null default 'new'
                   check (status in ('new','enriched','scored','qualified','disqualified','contacts_found')),
  profile          text
                   check (profile in ('laggard','adopter','hybrid','unclear')),
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index if not exists companies_status_idx on companies (status);
create index if not exists companies_ticker_idx on companies (ticker);
create index if not exists companies_sector_idx on companies (sector_bucket);
-- migration: sector_bucket becomes free-form text (profile packs bring their
-- own sector vocabulary) — widening only, existing rows unaffected.
alter table companies drop constraint if exists companies_sector_bucket_check;
-- v3: L1 pre-screen + tiering — dq_reason carries why a prescreen-failed row
-- was written as 'disqualified'; tier (T1-T4) is computed at score commit
-- (or 'T4' at prescreen time) and mirrored here so ordering queries don't
-- need to join scores. NULL tier (pre-v3 rows) is treated as T3 in app code.
alter table companies add column if not exists dq_reason text not null default '';
alter table companies add column if not exists tier text;
-- v3 phase 4: time-in-stage — stamped by db.set_status on every transition
-- going forward; backfilled from updated_at for existing rows (approximate:
-- updated_at also moves on any column update, not just a status change —
-- analytics.py labels durations computed from backfilled rows accordingly).
alter table companies add column if not exists status_changed_at timestamptz;
update companies set status_changed_at = updated_at where status_changed_at is null;
-- default for rows inserted after this migration (backfill above already
-- covers pre-existing rows, so ordering this after it keeps them untouched).
alter table companies alter column status_changed_at set default now();

create table if not exists signals (
  id             bigint generated always as identity primary key,
  company_cik    bigint not null references companies (cik) on delete cascade,
  source         text not null check (source in ('edgar','parallel','derived')),
  type           text not null,
  title          text not null,
  detail         text default '',
  evidence_url   text,
  evidence_quote text,
  observed_at    date,
  weight         numeric not null default 0,
  raw            jsonb not null default '{}'::jsonb,
  created_at     timestamptz not null default now()
);
create index if not exists signals_company_idx on signals (company_cik);
create index if not exists signals_type_idx on signals (type);

create table if not exists scores (
  id              bigint generated always as identity primary key,
  company_cik     bigint not null references companies (cik) on delete cascade,
  run_id          text,
  base_score      numeric,
  intent          integer,
  capability_gap  integer,
  timing          integer,
  commercial_fit  integer,
  total           integer not null,
  profile         text,
  service_fit     jsonb not null default '[]'::jsonb,
  reasoning       text not null default '',
  why_now         text not null default '',
  evidence_cited  jsonb not null default '[]'::jsonb,
  confidence      text default 'medium',
  model           text,
  created_at      timestamptz not null default now()
);
create index if not exists scores_company_idx on scores (company_cik);
-- migration for pre-existing installs (create-if-not-exists won't add columns)
alter table scores add column if not exists why_now text not null default '';
alter table scores add column if not exists angle_ranking jsonb not null default '[]'::jsonb;
alter table scores add column if not exists primary_angle jsonb;
alter table scores add column if not exists gate_reason text not null default '';
-- v3: tier (T1-T4, mirrors companies.tier at the time of this score) and
-- priority (composite ordering key — see scoring.priority_score)
alter table scores add column if not exists tier text;
alter table scores add column if not exists priority numeric;

create table if not exists angles (
  id             bigint generated always as identity primary key,
  company_cik    bigint not null references companies (cik) on delete cascade,
  family         text not null check (family in ('funding','leadership','ai_move')),
  headline       text not null,
  details        jsonb not null default '{}'::jsonb,
  evidence_url   text,
  evidence_quote text,
  event_date     date not null,
  source         text not null check (source in ('edgar','parallel')),
  strength       numeric not null default 0,
  status         text not null default 'active' check (status in ('active','stale')),
  fingerprint    text not null,
  collected_at   timestamptz not null default now(),
  unique (company_cik, fingerprint)
);
create index if not exists angles_company_idx on angles (company_cik);
create index if not exists angles_family_idx on angles (family);

create table if not exists contacts (
  id           bigint generated always as identity primary key,
  company_cik  bigint not null references companies (cik) on delete cascade,
  name         text not null,
  title        text not null,
  role_bucket  text default '',
  linkedin_url text,
  email        text,
  email_source text,
  confidence   text default 'medium',
  evidence     jsonb not null default '{}'::jsonb,
  created_at   timestamptz not null default now()
);
create index if not exists contacts_company_idx on contacts (company_cik);

-- Drafted outreach sequences (v2 sub-project 2). One row per sequence
-- (4 steps as jsonb); contact name/title are snapshots because contacts has
-- no unique key and /people re-runs can re-insert rows. angle_fingerprint is
-- the stable natural key into angles, same as scores.primary_angle.
-- status pre-provisions sub-project 3 (CRM push); this stage only writes 'draft'.
create table if not exists messages (
  id                bigint generated always as identity primary key,
  company_cik       bigint not null references companies (cik) on delete cascade,
  contact_id        bigint not null references contacts (id) on delete cascade,
  contact_name      text not null,
  contact_title     text not null,
  ticker            text not null,
  archetype         text not null check (archetype in
                    ('observation','creative_ideas','referral_ceiling','problem_solution',
                     'whole_offer','case_study','benchmark')),
  angle_fingerprint text not null,
  angle_family      text not null check (angle_family in ('funding','leadership','ai_move')),
  service           text not null,
  steps             jsonb not null default '[]'::jsonb,
  qa_warnings       jsonb not null default '[]'::jsonb,
  status            text not null default 'draft'
                    check (status in ('draft','approved','rejected','exported','sent')),
  run_id            text,
  model             text,
  created_at        timestamptz not null default now(),
  unique (contact_id, angle_fingerprint)
);
create index if not exists messages_company_idx on messages (company_cik);
create index if not exists messages_status_idx on messages (status);
-- v3 phase 4: widen messages.status additively so outcome events (recorded
-- via `pipeline outcome` / outcomes.record) can advance a draft all the way
-- to 'meeting' — existing rows (draft/approved/rejected/exported/sent) still
-- satisfy the new CHECK. Drop + re-add kept adjacent so one apply-schema run
-- does both.
alter table messages drop constraint if exists messages_status_check;
alter table messages add constraint messages_status_check
  check (status in ('draft','approved','rejected','exported','sent','bounced',
                     'replied','positive_reply','meeting','opted_out'));

-- v3 phase 4: append-only outcome events per drafted message — the audit
-- trail behind messages.status advancement (see outcomes.py). 'opt_out' is
-- the event name; it maps to the 'opted_out' status value above.
create table if not exists message_events (
  id           bigint generated always as identity primary key,
  message_id   bigint not null references messages (id) on delete cascade,
  event        text not null check (event in
               ('approved','rejected','exported','sent','bounced','replied',
                'positive_reply','meeting','opt_out')),
  occurred_at  timestamptz not null default now(),
  note         text not null default ''
);
create index if not exists message_events_message_idx on message_events (message_id);

create table if not exists runs (
  id          bigint generated always as identity primary key,
  stage       text not null,
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  stats       jsonb not null default '{}'::jsonb
);

-- keep companies.updated_at fresh
create or replace function set_updated_at() returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists companies_updated_at on companies;
create trigger companies_updated_at
  before update on companies
  for each row execute function set_updated_at();

-- Lock tables down: RLS on with no policies means only the service-role key
-- (which bypasses RLS) can touch them. The anon key sees nothing.
alter table companies enable row level security;
alter table signals   enable row level security;
alter table scores    enable row level security;
alter table contacts  enable row level security;
alter table runs      enable row level security;
alter table angles    enable row level security;
alter table messages  enable row level security;
alter table message_events enable row level security;
