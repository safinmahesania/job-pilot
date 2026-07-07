import sqlite3;

c = sqlite3.connect('jobpilot.db');
[print(f'{s} | {src} | {t[:35]}') for s, t, src in c.execute(
    'SELECT score,title,source FROM jobs WHERE source IN (\"remoteok\",\"weworkremotely\",\"remotive\") ORDER BY score DESC LIMIT 10')]
