"""Create (or refresh) the quiz schema, data and questions in the hosted database.

Usage:  python scripts/seed.py
Re-running is safe: schema is idempotent, quiz data is reloaded, questions are upserted,
and students' saved answers / the audit log are never touched.
"""
import csv
import json
from pathlib import Path

from db import connect

DB_DIR = Path(__file__).resolve().parent.parent / "db"


def read_csv(name):
    with open(DB_DIR / name, newline="") as f:
        return list(csv.DictReader(f))


def main():
    departments = read_csv("departments.csv")
    employees = read_csv("employees.csv")
    questions = json.loads((DB_DIR / "questions.json").read_text())

    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute((DB_DIR / "schema.sql").read_text())
            print("schema applied")

            # Values are always passed as parameters (%s), never formatted into the SQL string.
            cur.execute("truncate public.employees, public.departments")
            cur.executemany(
                "insert into public.departments (id, name, location, annual_budget) values (%s, %s, %s, %s)",
                [(d["id"], d["name"], d["location"], d["annual_budget"]) for d in departments],
            )
            cur.executemany(
                """insert into public.employees
                   (id, first_name, last_name, department_id, job_title, employment_type,
                    city, hire_date, salary, performance_rating)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                [(e["id"], e["first_name"], e["last_name"], e["department_id"], e["job_title"],
                  e["employment_type"], e["city"], e["hire_date"], e["salary"],
                  e["performance_rating"]) for e in employees],
            )
            print(f"loaded {len(departments)} departments, {len(employees)} employees")

            cur.executemany(
                """insert into public.quiz_questions (id, position, topic, prompt, points, ordered, hint_sql)
                   values (%(id)s, %(position)s, %(topic)s, %(prompt)s, %(points)s, %(ordered)s, %(hint_sql)s)
                   on conflict (id) do update set
                     position = excluded.position, topic = excluded.topic, prompt = excluded.prompt,
                     points = excluded.points, ordered = excluded.ordered, hint_sql = excluded.hint_sql""",
                questions,
            )
            cur.execute("delete from public.quiz_questions where id <> all(%s)", ([q["id"] for q in questions],))
            total = sum(q["points"] for q in questions)
            print(f"loaded {len(questions)} questions worth {total} points")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
