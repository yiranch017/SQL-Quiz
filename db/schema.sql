-- SQL Quiz schema for Supabase (PostgreSQL 15+). Safe to re-run (idempotent).
--
-- Layout
--   public.departments / public.employees   quiz data that students query
--   public.quiz_questions                   the 5 questions (hint SQL is hidden from direct reads)
--   public.quiz_answers                     each signed-in user's saved answers
--   public.audit_log                        append-only record of who ran / saved / peeked at what
--   quiz_sandbox.exec_rows()                runs a student's query as the least-privileged role
--   public.run_query / save_answer / get_hint   the only entry points the web page calls (RPC)
--
-- Security model in one paragraph: browsers never talk to the quiz tables directly.
-- They call the three RPC functions with their Supabase Auth token. Each function
-- refuses signed-out callers (auth.uid() is null), then hands the student's SQL to
-- quiz_sandbox.exec_rows(), a SECURITY DEFINER function owned by the NOLOGIN role
-- quiz_reader, which holds SELECT on departments + employees and nothing else.

-- ---------------------------------------------------------------- roles
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'quiz_reader') then
    create role quiz_reader nologin;
  end if;
end
$$;
-- lets the migration owner create objects owned by quiz_reader
grant quiz_reader to current_user;

-- ---------------------------------------------------------------- quiz data
create table if not exists public.departments (
  id            int primary key,
  name          text not null unique,
  location      text not null,
  annual_budget numeric(12, 0) not null check (annual_budget > 0)
);

create table if not exists public.employees (
  id                 int primary key,
  first_name         text not null,
  last_name          text not null,
  department_id      int not null references public.departments (id),
  job_title          text not null,
  employment_type    text not null check (employment_type in ('Full-time', 'Part-time', 'Contract')),
  city               text not null,
  hire_date          date not null,
  salary             numeric(10, 0) not null check (salary > 0),
  performance_rating numeric(2, 1) not null check (performance_rating between 1 and 5)
);

-- ---------------------------------------------------------------- quiz bookkeeping
create table if not exists public.quiz_questions (
  id       int primary key,
  position int not null unique,
  topic    text not null,
  prompt   text not null,
  points   int not null check (points > 0),
  ordered  boolean not null default false,  -- true: row order matters when grading
  hint_sql text not null
);

create table if not exists public.quiz_answers (
  user_id     uuid not null references auth.users (id) on delete cascade,
  question_id int  not null references public.quiz_questions (id) on delete cascade,
  sql         text not null,
  passed      boolean not null,
  updated_at  timestamptz not null default now(),
  primary key (user_id, question_id)
);

create table if not exists public.audit_log (
  id          bigint generated always as identity primary key,
  user_id     uuid not null,
  user_email  text,
  action      text not null check (action in ('run', 'save', 'hint')),
  question_id int,
  sql         text,
  success     boolean not null,
  row_count   int,
  error       text,
  created_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------- audit integrity
-- Extra credit: the taker role must not be able to edit the log. Two independent layers:
--   1. privileges: signed-in users (role "authenticated") may only SELECT their own rows;
--      rows are inserted exclusively by the SECURITY DEFINER functions below.
--   2. triggers: UPDATE / DELETE / TRUNCATE raise an error for everybody, owner included.
create or replace function public.audit_log_block_changes()
returns trigger
language plpgsql
as $$
begin
  raise exception 'audit_log is append-only (% blocked)', tg_op using errcode = '42501';
end
$$;

drop trigger if exists audit_log_no_update_delete on public.audit_log;
create trigger audit_log_no_update_delete
  before update or delete on public.audit_log
  for each row execute function public.audit_log_block_changes();

drop trigger if exists audit_log_no_truncate on public.audit_log;
create trigger audit_log_no_truncate
  before truncate on public.audit_log
  for each statement execute function public.audit_log_block_changes();

-- ---------------------------------------------------------------- privileges + RLS
alter table public.departments    enable row level security;
alter table public.employees      enable row level security;
alter table public.quiz_questions enable row level security;
alter table public.quiz_answers   enable row level security;
alter table public.audit_log      enable row level security;

-- Supabase grants new tables to anon/authenticated by default; start from zero.
revoke all on public.departments, public.employees, public.quiz_questions,
              public.quiz_answers, public.audit_log
  from public, anon, authenticated, quiz_reader;

-- the sandbox role reads quiz data (no policy needed: RLS is on, so add permissive ones)
grant usage on schema public to quiz_reader;
grant select on public.departments, public.employees to quiz_reader;

drop policy if exists sandbox_read on public.departments;
create policy sandbox_read on public.departments for select to quiz_reader using (true);
drop policy if exists sandbox_read on public.employees;
create policy sandbox_read on public.employees   for select to quiz_reader using (true);

-- signed-in users: list questions (but not hint_sql), read their own answers and log rows
grant select (id, position, topic, prompt, points, ordered) on public.quiz_questions to authenticated;
drop policy if exists questions_read on public.quiz_questions;
create policy questions_read on public.quiz_questions for select to authenticated using (true);

grant select on public.quiz_answers to authenticated;
drop policy if exists answers_own on public.quiz_answers;
create policy answers_own on public.quiz_answers for select to authenticated
  using (user_id = (select auth.uid()));

grant select on public.audit_log to authenticated;
drop policy if exists audit_own on public.audit_log;
create policy audit_own on public.audit_log for select to authenticated
  using (user_id = (select auth.uid()));

-- ---------------------------------------------------------------- sandbox
create schema if not exists quiz_sandbox authorization quiz_reader;
revoke all on schema quiz_sandbox from public, anon, authenticated;

-- Runs one student query and returns {"rows": [...], "truncated": bool}.
-- Owned by quiz_reader + SECURITY DEFINER => the query executes with quiz_reader's
-- privileges only. The query is wrapped as a sub-select, so exactly one SELECT-shaped
-- statement can run; the newline keeps a trailing "-- comment" from eating the ")".
drop function if exists quiz_sandbox.exec_rows(text, int);
create function quiz_sandbox.exec_rows(p_sql text, p_limit int default 200)
returns json
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_rows json;
begin
  execute format(
    'select coalesce(json_agg(row_to_json(q)), ''[]''::json)
       from (select * from (%s
            ) as user_query limit %s) as q',
    p_sql, p_limit + 1)
  into v_rows;

  if json_array_length(v_rows) > p_limit then
    select json_agg(e order by n) into v_rows
      from json_array_elements(v_rows) with ordinality as t(e, n)
     where n <= p_limit;
    return json_build_object('rows', v_rows, 'truncated', true);
  end if;
  return json_build_object('rows', v_rows, 'truncated', false);
end
$$;

alter function quiz_sandbox.exec_rows(text, int) owner to quiz_reader;
revoke all on function quiz_sandbox.exec_rows(text, int) from public, anon, authenticated;
grant usage on schema quiz_sandbox to current_user;
grant execute on function quiz_sandbox.exec_rows(text, int) to current_user;

-- ---------------------------------------------------------------- helpers (not callable by clients)
-- Only a single SELECT / WITH statement is accepted. (Defence in depth: even if this
-- check were bypassed, quiz_reader has no write privileges.)
create or replace function public.quiz_clean_sql(p_sql text)
returns text
language plpgsql
as $$
declare
  v text := regexp_replace(coalesce(p_sql, ''), '[;\s]+$', '');
begin
  v := btrim(v);
  if v = '' then
    raise exception 'Enter a query first.';
  end if;
  if length(v) > 4000 then
    raise exception 'Query is too long (max 4000 characters).';
  end if;
  if position(';' in v) > 0 then
    raise exception 'Only one statement is allowed (remove the extra semicolon).';
  end if;
  if v !~* '^(select|with)\M' then
    raise exception 'Only SELECT queries are allowed in this quiz.';
  end if;
  return v;
end
$$;

-- Normalises a result set to compare a student's rows with the hint's rows:
-- column names are ignored, numbers are compared by value (4.50 = 4.5),
-- row order is ignored unless the question is "ordered".
create or replace function public.quiz_norm(p_rows json, p_ordered boolean)
returns text[]
language sql
immutable
as $$
  select coalesce(
    array_agg(rowtxt order by case when p_ordered then n end,
                              case when not p_ordered then rowtxt end),
    '{}'::text[])
  from (
    select n,
           (select string_agg(
                     case when v ~ '^-?[0-9]+(\.[0-9]+)?$' then trim_scale(v::numeric)::text
                          else coalesce(v, '<null>') end,
                     '|' order by k_n)
              from json_each_text(r) with ordinality as e(k, v, k_n)) as rowtxt
      from json_array_elements(p_rows) with ordinality as t(r, n)
  ) z
$$;

create or replace function public._audit(
  p_action text, p_question_id int, p_sql text, p_success boolean, p_rows int, p_error text)
returns void
language sql
security definer
set search_path = public, pg_temp
as $$
  insert into public.audit_log (user_id, user_email, action, question_id, sql, success, row_count, error)
  values (auth.uid(), auth.jwt() ->> 'email', p_action, p_question_id,
          left(p_sql, 4000), p_success, p_rows, left(p_error, 500));
$$;

-- ---------------------------------------------------------------- RPC entry points
-- Run: execute a query and show the rows. Parameters are bound by PL/pgSQL;
-- the only dynamic SQL in the system is the student's own query (sandboxed above).
create or replace function public.run_query(p_sql text, p_question_id int default null)
returns json
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_res json;
begin
  if auth.uid() is null then
    raise exception 'Sign in required' using errcode = '28000';
  end if;
  begin
    v_res := quiz_sandbox.exec_rows(public.quiz_clean_sql(p_sql));
    perform public._audit('run', p_question_id, p_sql, true, json_array_length(v_res -> 'rows'), null);
    return json_build_object('ok', true, 'rows', v_res -> 'rows', 'truncated', v_res -> 'truncated');
  exception
    when query_canceled then
      perform public._audit('run', p_question_id, p_sql, false, null, 'timeout');
      return json_build_object('ok', false, 'error', 'Query took too long and was cancelled.');
    when others then
      perform public._audit('run', p_question_id, p_sql, false, null, sqlerrm);
      return json_build_object('ok', false, 'error', sqlerrm);
  end;
end
$$;

-- Save: run the query, grade it against the hint SQL's live result, store the answer.
create or replace function public.save_answer(p_question_id int, p_sql text)
returns json
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_uid    uuid := auth.uid();
  v_q      public.quiz_questions;
  v_got    json;
  v_want   json;
  v_passed boolean;
begin
  if v_uid is null then
    raise exception 'Sign in required' using errcode = '28000';
  end if;
  select * into v_q from public.quiz_questions where id = p_question_id;
  if not found then
    raise exception 'Unknown question %', p_question_id;
  end if;
  begin
    v_got  := quiz_sandbox.exec_rows(public.quiz_clean_sql(p_sql));
    v_want := quiz_sandbox.exec_rows(public.quiz_clean_sql(v_q.hint_sql));
    v_passed := not (v_got ->> 'truncated')::boolean
                and public.quiz_norm(v_got -> 'rows', v_q.ordered)
                  = public.quiz_norm(v_want -> 'rows', v_q.ordered);

    insert into public.quiz_answers (user_id, question_id, sql, passed, updated_at)
    values (v_uid, p_question_id, p_sql, v_passed, now())
    on conflict (user_id, question_id)
    do update set sql = excluded.sql, passed = excluded.passed, updated_at = now();

    perform public._audit('save', p_question_id, p_sql, true, json_array_length(v_got -> 'rows'), null);
    return json_build_object('ok', true, 'passed', v_passed, 'points', v_q.points,
                             'rows', v_got -> 'rows', 'truncated', v_got -> 'truncated');
  exception
    when query_canceled then
      perform public._audit('save', p_question_id, p_sql, false, null, 'timeout');
      return json_build_object('ok', false, 'error', 'Query took too long and was cancelled.');
    when others then
      perform public._audit('save', p_question_id, p_sql, false, null, sqlerrm);
      return json_build_object('ok', false, 'error', sqlerrm);
  end;
end
$$;

-- Hint: the expected SQL plus the live expected result.
create or replace function public.get_hint(p_question_id int)
returns json
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_q   public.quiz_questions;
  v_res json;
begin
  if auth.uid() is null then
    raise exception 'Sign in required' using errcode = '28000';
  end if;
  select * into v_q from public.quiz_questions where id = p_question_id;
  if not found then
    raise exception 'Unknown question %', p_question_id;
  end if;
  v_res := quiz_sandbox.exec_rows(public.quiz_clean_sql(v_q.hint_sql));
  perform public._audit('hint', p_question_id, null, true, json_array_length(v_res -> 'rows'), null);
  return json_build_object('ok', true, 'sql', v_q.hint_sql, 'rows', v_res -> 'rows');
end
$$;

-- Only signed-in users may call the three RPCs; helpers stay private.
revoke all on function public.run_query(text, int)   from public, anon, authenticated;
revoke all on function public.save_answer(int, text) from public, anon, authenticated;
revoke all on function public.get_hint(int)          from public, anon, authenticated;
revoke all on function public.quiz_clean_sql(text)   from public, anon, authenticated;
revoke all on function public.quiz_norm(json, boolean) from public, anon, authenticated;
revoke all on function public._audit(text, int, text, boolean, int, text) from public, anon, authenticated;
revoke all on function public.audit_log_block_changes() from public, anon, authenticated;

grant execute on function public.run_query(text, int)   to authenticated;
grant execute on function public.save_answer(int, text) to authenticated;
grant execute on function public.get_hint(int)          to authenticated;
