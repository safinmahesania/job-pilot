"""Telegram notifications — silent no-op if not configured."""
import os
import httpx
from dotenv import load_dotenv
load_dotenv()

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send(text: str) -> bool:
    if not enabled():
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"[notify] telegram failed: {e}")
        return False


def run_summary(stats: dict, elapsed_s: float, model: str, new_jobs: list[dict]) -> str:
    """Build the run-summary message. new_jobs = list of dicts with score/title/company."""
    m = int(elapsed_s // 60)
    s = int(elapsed_s % 60)
    elapsed = f"{m}m {s}s" if m else f"{s}s"
    model_short = model.replace("qwen2.5:", "")

    new_60 = len(new_jobs)
    lines = [
        "🧭 <b>JobPilot run complete</b>",
        "",
        f"⏱ {elapsed} · 🧠 {model_short}",
        f"📥 {stats['fetched']} fetched · 🆕 {stats['seen'] and stats['fetched'] - stats['seen'] or (stats['kept'] + stats['trashed'] + stats['dropped'])} processed · ✅ {new_60} new 60+",
        f"⚠️ {stats['errors']} errors",
    ]
    if new_jobs:
        lines.append("")
        lines.append("<b>Top new matches:</b>")
        for j in new_jobs[:5]:
            title = (j.get("title") or "")[:42]
            lines.append(f"• {round(j['score'])} — {title} · {j.get('company','')}")
    return "\n".join(lines)

def enabled() -> bool:
    if not (TOKEN and CHAT_ID):
        return False
    # DB setting check (default on)
    try:
        from src import store
        conn = store.connect()
        val = store.get_setting(conn, "notify_enabled", "1")
        conn.close()
        return val == "1"
    except Exception:
        return True   # setting na mile to on