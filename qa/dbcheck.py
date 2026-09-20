import sqlite3
for p in ["instance/interviewiq.db", "interview_Ai/instance/interviewiq.db"]:
    c = sqlite3.connect(p)
    print("===", p)
    print("users", c.execute("SELECT id,email FROM users ORDER BY id").fetchall())
    print("interviews", c.execute(
        "SELECT COUNT(*), COALESCE(MIN(id),0), COALESCE(MAX(id),0) FROM interviews").fetchone())
    print("qa-like", c.execute("SELECT id,email FROM users WHERE email LIKE ?", ("%qa%",)).fetchall())