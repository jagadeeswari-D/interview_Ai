import sqlite3
c = sqlite3.connect("interview_Ai/instance/interviewiq.db")
print("users count", c.execute("SELECT COUNT(*) FROM users").fetchone())
print("by id", c.execute("SELECT id,email,name FROM users WHERE id>=9").fetchall())
print("interviews by user", c.execute("SELECT user_id, COUNT(*), mode FROM interviews GROUP BY user_id,mode").fetchall())
print("weakness", c.execute("SELECT COUNT(*) FROM weaknesses").fetchone())
print("roadmaps", c.execute("SELECT user_id,count(*) FROM roadmaps GROUP BY user_id").fetchall())
print("sqlite_sequence", c.execute("SELECT name,seq FROM sqlite_sequence WHERE name IN ('users','interviews')").fetchall())