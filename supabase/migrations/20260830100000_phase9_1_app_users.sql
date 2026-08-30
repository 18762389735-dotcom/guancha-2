-- Phase 9-1 Authentication Kernel: external CloudBase subject to stable app user.
-- No password, access token, refresh token, or selection ownership column is stored.

create table app_users (
  id uuid primary key,
  cloudbase_user_id text not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
