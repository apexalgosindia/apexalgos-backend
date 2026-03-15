-- ══════════════════════════════════════════════════════════
--  APEX ALGOS · Supabase Schema v2
--  Run this in: Supabase Dashboard → SQL Editor
-- ══════════════════════════════════════════════════════════

drop table if exists daily_summary cascade;
drop table if exists pnl_ticks cascade;
drop table if exists strategies cascade;
drop table if exists user_strategies cascade;
drop table if exists user_settings cascade;

create table user_strategies (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  name        text not null,
  shared_code text not null,
  sid         text default '',
  is_active   boolean default true,
  created_at  timestamptz default now()
);

create table user_settings (
  user_id             uuid primary key references auth.users(id) on delete cascade,
  telegram_bot_token  text default '',
  telegram_chat_id    text default '',
  alert_eod           boolean default true,
  updated_at          timestamptz default now()
);

create table pnl_ticks (
  id       bigserial primary key,
  sid      text not null,
  user_id  uuid not null,
  ts       text not null,
  value    numeric(12,2) not null
);
create index idx_ticks_sid_ts  on pnl_ticks(sid, ts);
create index idx_ticks_user_ts on pnl_ticks(user_id, ts);

create table daily_summary (
  id         bigserial primary key,
  sid        text not null,
  user_id    uuid not null,
  strat_name text not null,
  date       date not null,
  high       numeric(12,2) default 0,
  low        numeric(12,2) default 0,
  exit_pnl   numeric(12,2) default 0,
  peak_value numeric(12,2) default 0,
  peak_time  text,
  tick_count int default 0,
  unique(sid, user_id, date)
);
create index idx_summary_user_date on daily_summary(user_id, date);

alter table user_strategies enable row level security;
alter table user_settings   enable row level security;
alter table pnl_ticks       enable row level security;
alter table daily_summary   enable row level security;

create policy "own" on user_strategies for all using (auth.uid() = user_id);
create policy "own" on user_settings   for all using (auth.uid() = user_id);
create policy "own" on pnl_ticks       for all using (auth.uid() = user_id);
create policy "own" on daily_summary   for all using (auth.uid() = user_id);
