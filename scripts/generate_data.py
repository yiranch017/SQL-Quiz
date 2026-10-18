"""Generate the quiz dataset (db/departments.csv, db/employees.csv).

The data is synthetic and fully deterministic (fixed seed), so quiz answers
never change. Run once; the CSVs are committed. Usage: python scripts/generate_data.py
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

DB_DIR = Path(__file__).resolve().parent.parent / "db"
random.seed(706)

DEPARTMENTS = [
    # id, name, location, annual_budget
    (1, "Engineering", "Durham", 2400000),
    (2, "Data Science", "Durham", 1500000),
    (3, "Marketing", "Raleigh", 900000),
    (4, "Sales", "Charlotte", 1300000),
    (5, "Human Resources", "Raleigh", 600000),
    (6, "Finance", "Charlotte", 800000),
]

# department_id -> [(job_title, base_salary_low, base_salary_high)]
TITLES = {
    1: [("Software Engineer", 95000, 135000), ("Senior Software Engineer", 130000, 170000),
        ("DevOps Engineer", 100000, 140000)],
    2: [("Data Analyst", 80000, 110000), ("Data Scientist", 105000, 145000),
        ("ML Engineer", 120000, 165000)],
    3: [("Marketing Specialist", 55000, 80000), ("Content Strategist", 60000, 85000),
        ("Marketing Manager", 85000, 115000)],
    4: [("Sales Representative", 50000, 75000), ("Account Executive", 70000, 105000),
        ("Sales Manager", 95000, 130000)],
    5: [("HR Generalist", 55000, 75000), ("Recruiter", 58000, 82000),
        ("HR Manager", 85000, 110000)],
    6: [("Accountant", 62000, 88000), ("Financial Analyst", 70000, 98000),
        ("Finance Manager", 100000, 135000)],
}
HEADCOUNT = {1: 14, 2: 11, 3: 9, 4: 12, 5: 6, 6: 8}  # 60 employees
CITIES = ["Durham", "Raleigh", "Charlotte", "Asheville"]
TYPES = ["Full-time"] * 8 + ["Part-time"] + ["Contract"]
FIRST = ["Ava", "Liam", "Noah", "Emma", "Olivia", "Mason", "Sophia", "Lucas", "Mia", "Ethan",
         "Amelia", "Logan", "Harper", "James", "Ella", "Henry", "Grace", "Jack", "Chloe", "Owen",
         "Zoe", "Wyatt", "Nora", "Caleb", "Layla", "Isaac", "Aria", "Leo", "Ruby", "Eli",
         "Maya", "Jonah", "Iris", "Nolan", "Lena", "Felix", "Tessa", "Miles", "Hazel", "Dean",
         "Priya", "Arjun", "Mei", "Wei", "Yuki", "Kenji", "Sofia", "Mateo", "Amara", "Omar",
         "Fatima", "Diego", "Anika", "Rohan", "Lucia", "Hugo", "Nadia", "Idris", "Carmen", "Theo"]
LAST = ["Nguyen", "Patel", "Garcia", "Kim", "Johnson", "Martinez", "Lee", "Brown", "Davis", "Wilson",
        "Chen", "Lopez", "Walker", "Hall", "Young", "Allen", "King", "Wright", "Scott", "Green",
        "Baker", "Adams", "Nelson", "Hill", "Campbell", "Mitchell", "Roberts", "Carter", "Phillips",
        "Evans", "Turner", "Torres", "Parker", "Collins", "Edwards", "Stewart", "Morris", "Murphy",
        "Cook", "Rogers", "Reed", "Bailey", "Cooper", "Howard", "Ward", "Brooks", "Gray", "James",
        "Watson", "Price", "Bennett", "Wood", "Barnes", "Ross", "Henderson", "Coleman", "Jenkins",
        "Perry", "Powell", "Long"]


def main():
    random.shuffle(FIRST)
    random.shuffle(LAST)
    employees = []
    emp_id = 1
    for dept_id, count in HEADCOUNT.items():
        for _ in range(count):
            title, lo, hi = random.choice(TITLES[dept_id])
            etype = random.choice(TYPES)
            salary = random.randrange(lo, hi + 1, 500)
            if etype == "Part-time":
                salary = int(salary * 0.55 // 500 * 500)
            hire = date(2016, 1, 4) + timedelta(days=random.randrange(0, 365 * 10))
            rating = round(random.uniform(2.6, 4.9), 1)
            employees.append({
                "id": emp_id,
                "first_name": FIRST[emp_id - 1],
                "last_name": LAST[emp_id - 1],
                "department_id": dept_id,
                "job_title": title,
                "employment_type": etype,
                "city": random.choice(CITIES),
                "hire_date": hire.isoformat(),
                "salary": salary,
                "performance_rating": rating,
            })
            emp_id += 1

    with open(DB_DIR / "departments.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "location", "annual_budget"])
        w.writerows(DEPARTMENTS)
    with open(DB_DIR / "employees.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(employees[0]))
        w.writeheader()
        w.writerows(employees)
    print(f"wrote {len(DEPARTMENTS)} departments, {len(employees)} employees")


if __name__ == "__main__":
    main()
