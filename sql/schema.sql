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
