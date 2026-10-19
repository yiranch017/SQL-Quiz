"""Health + security checks for the hosted quiz database (run by GitHub Actions).

Connects with DATABASE_URL (a GitHub Secret in CI) over SSL, then verifies:
  0. ssl       the connection to the database is encrypted
  1. data      the quiz tables are loaded and the questions add up to 20 points
  2. hints     every question's hint SQL runs and returns rows
  3. gating    signed-out callers cannot run queries; signed-in ones can
  4. sandbox   a signed-in user's SQL cannot write, stack statements or read private tables
  5. integrity the taker role cannot insert into, edit or delete the audit log

Everything happens inside one transaction that is rolled back at the end, so the check
leaves no trace. All SQL values are bound parameters (%s). Exit code 1 on any failure.
"""
import json
import os
import sys
import uuid

import psycopg2
from psycopg2 import sql

from db import connect

failures = []
USER_A = str(uuid.uuid4())
USER_B = str(uuid.uuid4())


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def as_user(cur, role, user_id=None):
    """Impersonate a Supabase API caller: set the DB role and the JWT claims."""
    cur.execute("reset role")
    cur.execute("select set_config('request.jwt.claims', %s, true)",
                (json.dumps({"sub": user_id, "email": "ci@example.com"} if user_id else {}),))
    cur.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))  # identifiers are quoted by psycopg2, not string-formatted


def blocked(cur, sql, params=None):
    """Run statement expecting the database to refuse it. Returns the error text or None."""
    cur.execute("savepoint s")
    try:
        cur.execute(sql, params)
        cur.execute("release savepoint s")
        return None
    except psycopg2.Error as e:
        cur.execute("rollback to savepoint s")
        return str(e).strip().splitlines()[0]


def rpc(cur, sql_text, qid=None):
    cur.execute("select public.run_query(%s, %s)", (sql_text, qid))
    return cur.fetchone()[0]


def main():
    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute("select version()")
        print("connected:", cur.fetchone()[0].split(",")[0])
        # conn.info.ssl_in_use is libpq's view of OUR connection (client -> pooler).
        # (pg_stat_ssl would show the pooler -> database hop instead, which is not what we control.)
        encrypted = conn.info.ssl_in_use
        print("encrypted connection (sslmode=require):", encrypted)
        if os.environ.get("DB_SSLMODE", "require") != "disable":  # only the local test harness disables SSL
            check("connection to the database is SSL-encrypted", encrypted)

        # 1. data ------------------------------------------------------------------------
        cur.execute("select count(*) from public.departments")
        check("departments loaded", cur.fetchone()[0] >= 5)
        cur.execute("select count(*) from public.employees")
        n_emp = cur.fetchone()[0]
        check("employees loaded", n_emp >= 50, f"{n_emp} rows")
        cur.execute("select count(*), coalesce(sum(points), 0) from public.quiz_questions")
        n_q, pts = cur.fetchone()
        check("quiz has questions", n_q >= 1)
        check("quiz is worth 20 points", pts == 20, f"{pts} points")

        # 2. hints -----------------------------------------------------------------------
        cur.execute("select id, hint_sql, ordered from public.quiz_questions order by position")
        questions = cur.fetchall()
        as_user(cur, "authenticated", USER_A)
        for qid, hint_sql, ordered in questions:
            cur.execute("select public.get_hint(%s)", (qid,))
            hint = cur.fetchone()[0]
            res = rpc(cur, hint_sql, qid)
            check(f"question {qid}: hint SQL returns rows", res["ok"] and len(res["rows"]) > 0,
                  res.get("error", ""))
            cur.execute("reset role")  # quiz_norm is a private helper: call it as the owner
            cur.execute("select public.quiz_norm(%s::json, %s) = public.quiz_norm(%s::json, %s)",
                        (json.dumps(res["rows"]), ordered, json.dumps(hint["rows"]), ordered))
            check(f"question {qid}: hint result matches its own SQL", cur.fetchone()[0])
            as_user(cur, "authenticated", USER_A)
        cur.execute("select to_regprocedure('public.save_answer(integer,text)') is not null")
        check("save_answer() is installed", cur.fetchone()[0])

        # 3. gating ----------------------------------------------------------------------
        as_user(cur, "anon")
        err = blocked(cur, "select public.run_query(%s)", ("select 1",))
        check("signed-out (anon) cannot run a query", err is not None and "permission denied" in err, str(err))
        as_user(cur, "authenticated", None)
        err = blocked(cur, "select public.run_query(%s)", ("select 1",))
        check("token-less caller is refused", err is not None and "Sign in required" in err, str(err))
        as_user(cur, "authenticated", USER_A)
        res = rpc(cur, "select count(*) as n from employees")
        check("signed-in user can run a query", res["ok"] and int(res["rows"][0]["n"]) == n_emp, str(res))

        # 4. sandbox ---------------------------------------------------------------------
        attacks = {
            "DELETE is rejected": "delete from employees",
            "write hidden in a CTE is rejected": "with d as (delete from employees returning *) select * from d",
            "stacked statements are rejected": "select 1; select 2",
            "cannot read the hint table": "select * from quiz_questions",
            "cannot read the audit log": "select * from audit_log",
            "cannot read auth.users": "select * from auth.users",
        }
        for label, attack in attacks.items():
            res = rpc(cur, attack)
            check(f"sandbox: {label}", res["ok"] is False, str(res)[:120])
        as_user(cur, "authenticated", USER_A)
        cur.execute("reset role")
        cur.execute("select count(*) from public.employees")
        check("sandbox: employees table untouched", cur.fetchone()[0] == n_emp)

        # 5. audit integrity -------------------------------------------------------------
        as_user(cur, "authenticated", USER_A)
        cur.execute("select count(*) from public.audit_log")
        check("audit log records the user's own actions", cur.fetchone()[0] >= 1)
        as_user(cur, "authenticated", USER_B)
        cur.execute("select count(*) from public.audit_log")
        check("another user cannot see those rows (RLS)", cur.fetchone()[0] == 0)

        as_user(cur, "authenticated", USER_A)
        cases = {
            "taker cannot INSERT into the log": ("insert into public.audit_log (user_id, action, success) values (%s, 'run', true)", (USER_A,)),
            "taker cannot UPDATE the log": ("update public.audit_log set success = false", None),
            "taker cannot DELETE from the log": ("delete from public.audit_log", None),
            "taker cannot TRUNCATE the log": ("truncate public.audit_log", None),
        }
        for label, (sql, params) in cases.items():
            err = blocked(cur, sql, params)
            check(f"integrity: {label}", err is not None and "permission denied" in err, str(err))

        cur.execute("reset role")  # back to the owner: the trigger is the second line of defence
        for label, sql in {"UPDATE": "update public.audit_log set success = false",
                           "DELETE": "delete from public.audit_log",
                           "TRUNCATE": "truncate public.audit_log"}.items():
            err = blocked(cur, sql)
            check(f"integrity: trigger blocks owner {label}", err is not None and "append-only" in err, str(err))
    finally:
        conn.rollback()  # leave no trace
        conn.close()

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: " + "; ".join(failures))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
