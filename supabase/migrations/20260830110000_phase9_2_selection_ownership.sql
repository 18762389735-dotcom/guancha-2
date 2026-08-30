-- Phase 9-2: add authenticated ownership without backfilling anonymous history.

alter table selection_sessions
  alter column anonymous_client_id drop not null,
  add column user_id uuid references app_users(id),
  add constraint selection_sessions_owner_check
    check (user_id is not null or anonymous_client_id is not null);

create unique index selection_sessions_user_idempotency_idx
  on selection_sessions(user_id, idempotency_key)
  where user_id is not null;

create index selection_sessions_user_restore_idx
  on selection_sessions(user_id, created_at desc)
  where user_id is not null;

-- These columns remain historical anonymous provenance for old rows.  New
-- authenticated rows leave them null rather than inventing a guest identity.
alter table decision_versions
  alter column anonymous_client_id drop not null;

alter table merchant_replies
  alter column anonymous_client_id drop not null;

create unique index merchant_replies_authenticated_idempotency_idx
  on merchant_replies(selection_session_id, followup_question_id, idempotency_key)
  where anonymous_client_id is null;
