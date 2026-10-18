"""Local-only test of save_answer() grading (needs a scratch PostgreSQL, NOT the hosted DB).

It inserts a throw-away row into auth.users, so run it against the local stub database:
    psql -f tests/local_supabase_stub.sql && DATABASE_URL=... DB_SSLMODE=disable python scripts/seed.py
    DATABASE_URL=... DB_SSLMODE=disable python tests/local_grading_test.py
"""
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from db import connect  # noqa: E402

USER = str(uuid.uuid4())

CASES = [
    # (label, question id, student SQL, expect passed)
    ("Q1 exact hint", 1, "SELECT first_name, last_name, salary FROM employees ORDER BY salary DESC LIMIT 5", True),
    ("Q1 different column aliases still pass", 1,
     "select first_name as a, last_name as b, salary as c from employees order by 3 desc limit 5;", True),
    ("Q1 ordered: ascending order fails", 1,
     "SELECT first_name, last_name, salary FROM (SELECT * FROM employees ORDER BY salary DESC LIMIT 5) t ORDER BY salary ASC", False),
    ("Q1 extra column fails", 1, "SELECT first_name, last_name, salary, city FROM employees ORDER BY salary DESC LIMIT 5", False),
    ("Q2 unordered: any row order passes", 2,
     "SELECT e.first_name, e.last_name, e.job_title FROM employees e, departments d "
     "WHERE d.id = e.department_id AND d.name = 'Sales' AND e.employment_type = 'Full-time' "
     "AND e.performance_rating >= 4 ORDER BY e.last_name DESC", True),
    ("Q2 missing a filter fails", 2,
     "SELECT e.first_name, e.last_name, e.job_title FROM employees e JOIN departments d ON d.id = e.department_id WHERE d.name = 'Sales'", False),
    ("Q3 numeric scale is ignored (99351 vs 99351.00)", 3,
     "SELECT employment_type, COUNT(*), ROUND(AVG(salary), 0)::numeric(12,2) FROM employees GROUP BY 1", True),
    ("Q3 a different value (2 decimals) fails", 3,
     "SELECT employment_type, COUNT(*), ROUND(AVG(salary), 2) FROM employees GROUP BY 1", False),
    ("Q4 CTE solution passes", 4,
     "WITH a AS (SELECT department_id, AVG(salary) s FROM employees GROUP BY 1) "
     "SELECT d.name, ROUND(a.s, 2) FROM a JOIN departments d ON d.id = a.department_id WHERE a.s > 80000 ORDER BY a.s DESC", True),
    ("Q5 window-function solution passes", 5,
     "SELECT name, COUNT(*) FROM (SELECT d.name, e.salary, AVG(e.salary) OVER (PARTITION BY e.department_id) av "
     "FROM employees e JOIN departments d ON d.id = e.department_id) t WHERE salary > av GROUP BY name", True),
    ("Q5 trailing comment does not break wrapping", 5,
     "SELECT d.name, COUNT(*) FROM employees e JOIN departments d ON d.id = e.department_id "
     "WHERE e.salary > (SELECT AVG(x.salary) FROM employees x WHERE x.department_id = e.department_id) GROUP BY d.name -- done", True),
]


def main():
    conn = connect()
    cur = conn.cursor()
    bad = 0
    try:
        cur.execute("insert into auth.users (id, email) values (%s, 'grader@example.com')", (USER,))
        cur.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps({"sub": USER}),))
        cur.execute("set local role authenticated")
        for label, qid, sql, expect in CASES:
            cur.execute("select public.save_answer(%s, %s)", (qid, sql))
            res = cur.fetchone()[0]
            ok = res.get("ok") and res["passed"] == expect
            bad += not ok
            print(("PASS " if ok else "FAIL ") + label, "" if ok else res)

        cur.execute("select passed from public.quiz_answers where question_id = 1")
        stored = cur.fetchall()
        ok = len(stored) == 1 and stored[0][0] is False  # last save of Q1 was a failing one, and it upserted
        bad += not ok
        print(("PASS " if ok else "FAIL ") + "re-saving upserts one row per question", stored)

        cur.execute("select public.save_answer(%s, %s)", (1, "select nope from employees"))
        res = cur.fetchone()[0]
        ok = res["ok"] is False and "nope" in res["error"]
        bad += not ok
        print(("PASS " if ok else "FAIL ") + "invalid SQL returns a readable error, nothing saved", res.get("error"))

        cur.execute("select action, success, count(*) from public.audit_log group by 1, 2 order by 1, 2")
        print("audit summary:", cur.fetchall())
    finally:
        conn.rollback()
        conn.close()
    print("\nALL PASSED" if not bad else f"\n{bad} FAILED")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
