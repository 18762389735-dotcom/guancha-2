-- Phase 9-4B: authenticated post-purchase data.
-- Selection remains in its existing tables; these are only user-owned
-- warehouse and Journal resources. No browser data is backfilled.

create table user_warehouse_teas (
  id uuid primary key,
  user_id uuid not null references app_users(id) on delete cascade,
  name varchar(120) not null,
  tea_category varchar(80),
  tea_subtype varchar(120),
  origin varchar(200),
  roast_or_style varchar(120),
  aroma varchar(120),
  status varchar(16) not null check (status in ('drinking', 'paused', 'finished')),
  source_type varchar(16) not null check (source_type in ('manual', 'selection')),
  selection_session_id uuid,
  candidate_id uuid,
  extraction_version_id uuid,
  decision_version_id uuid,
  facts jsonb not null default '[]'::jsonb check (jsonb_typeof(facts) = 'array'),
  risks jsonb not null default '[]'::jsonb check (jsonb_typeof(risks) = 'array'),
  risk_flags jsonb not null default '[]'::jsonb check (jsonb_typeof(risk_flags) = 'array'),
  joined_at timestamptz not null default now(),
  revision bigint not null default 1 check (revision >= 1),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, id)
);

create index user_warehouse_teas_owner_updated_idx
  on user_warehouse_teas(user_id, updated_at desc, id desc);

create table user_brew_journal_entries (
  id uuid primary key,
  user_id uuid not null references app_users(id) on delete cascade,
  tea_id uuid not null,
  brewed_on date not null,
  infusions jsonb not null default '[]'::jsonb check (jsonb_typeof(infusions) = 'array'),
  plan jsonb not null default '{}'::jsonb check (jsonb_typeof(plan) = 'object'),
  feedback jsonb not null default '{}'::jsonb check (jsonb_typeof(feedback) = 'object'),
  suggestion varchar(500),
  revision bigint not null default 1 check (revision >= 1),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (user_id, tea_id) references user_warehouse_teas(user_id, id) on delete cascade
);

create index user_brew_journal_owner_date_idx
  on user_brew_journal_entries(user_id, brewed_on desc, created_at desc, id desc);

create index user_brew_journal_owner_tea_date_idx
  on user_brew_journal_entries(user_id, tea_id, brewed_on desc);
