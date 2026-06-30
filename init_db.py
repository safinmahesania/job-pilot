import sqlite3

with open("schema.sql", "r", encoding="utf-8") as f:
    schema = f.read()

conn = sqlite3.connect("jobpilot.db")
conn.executescript(schema)
conn.commit()
conn.close()

print("Database ready: jobpilot.db")