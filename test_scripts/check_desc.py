import sqlite3

conn = sqlite3.connect("../jobpilot.db")
row = conn.execute(
    "SELECT description FROM jobs WHERE company LIKE '%DoorDash%' LIMIT 1"
).fetchone()
conn.close()

if row:
    print(repr(row[0][:150]))
else:
    print("No DoorDash job found")