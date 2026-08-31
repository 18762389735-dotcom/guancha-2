-- Phase 9-4A: authenticated, domain-specific preference persistence.
-- This intentionally does not claim anonymous browser data or duplicate
-- Selection, warehouse, Journal, UI, or media state.

create table user_preferences (
  user_id uuid primary key references app_users(id) on delete cascade,
  schema_version integer not null default 1 check (schema_version >= 1),
  profile jsonb not null check (jsonb_typeof(profile) = 'object'),
  revision bigint not null default 1 check (revision >= 1),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table user_preference_evidence (
  id uuid primary key,
  user_id uuid not null references app_users(id) on delete cascade,
  target_type text not null check (target_type in (
    'tea-style', 'aroma', 'roast', 'bitterness', 'astringency',
    'sweetness', 'mouthfeel', 'aftertaste', 'salivation', 'finish'
  )),
  target_value varchar(64) not null check (target_value ~ '^[a-z0-9-]{1,64}$'),
  polarity text not null check (polarity in ('positive', 'negative')),
  confidence text not null check (confidence = 'low'),
  issue_source text not null check (issue_source in ('tea', 'brewing', 'uncertain')),
  source_brew_session_id varchar(120) not null,
  created_at timestamptz not null,
  unique (user_id, source_brew_session_id)
);

create index user_preference_evidence_recent_idx
  on user_preference_evidence(user_id, created_at desc, id desc);
