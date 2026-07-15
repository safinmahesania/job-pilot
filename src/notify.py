"""Telegram notifications — silent no-op if not configured."""
import os
import httpx
from dotenv import load_dotenv
from src.logs import log
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
        log.warning("[notify] telegram failed: %s", e)
        return False


def run_summary(stats: dict, elapsed_s: float, model: str, new_jobs: list[dict]) -> str:
    """Build the run-summary message. new_jobs = list of dicts with score/title/company."""
    m = int(elapsed_s // 60)
    s = int(elapsed_s % 60)
    elapsed = f"{m}m {s}s" if m else f"{s}s"
    model_short = model.replace("qwen2.5:", "")

    new_60 = len(new_jobs)
    # Everything we actually looked at, i.e. everything that wasn't already known.
    # (The old one-liner here used `and`/`or` and returned the wrong number
    # whenever nothing new turned up.)
    processed = stats["kept"] + stats["trashed"] + stats["dropped"]

    lines = [
        "🧭 <b>JobPilot run complete</b>",
        "",
        f"⏱ {elapsed} · 🧠 {model_short}",
        f"📥 {stats['fetched']} fetched · 🆕 {processed} processed · ✅ {new_60} new 60+",
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

def board_alert(broken: list[dict]) -> str:
    """A board has stopped working. Say so, once.

    The silent ones lead, because they are the ones nothing else would tell you
    about — an erroring board at least shows red in the Health tab.
    """
    silent = [b for b in broken if b["verdict"] in ("silent", "never_worked")]
    erroring = [b for b in broken if b["verdict"] == "erroring"]

    lines = ["🔌 <b>A source stopped working</b>"]

    if silent:
        lines.append("")
        lines.append("<b>Returning nothing, but reporting success:</b>")
        for b in silent[:6]:
            lines.append(f"• {b['name']} — {b['zero_streak']} empty runs")
        lines.append("")
        lines.append("<i>These look healthy from the outside. You would not "
                     "have noticed.</i>")

    if erroring:
        lines.append("")
        lines.append("<b>Failing outright:</b>")
        for b in erroring[:6]:
            reason = (b.get("error") or "")[:60]
            lines.append(f"• {b['name']} — {reason}")

    total = len(silent) + len(erroring)
    if total > 12:
        lines.append("")
        lines.append(f"…and {total - 12} more. See the Health tab.")

    return "\n".join(lines)


def weekly_digest(stats: dict) -> str:
    """The week, in the order you'd want to hear it.

    What needs doing comes first — follow-ups you owe, jobs waiting to be
    triaged. The numbers come after, because they are interesting and the
    follow-ups are not optional.
    """
    lines = ["📊 <b>JobPilot — your week</b>"]

    if stats["followups_due"]:
        lines.append("")
        lines.append(f"📮 <b>{stats['followups_due']} follow-up"
                     f"{'s' if stats['followups_due'] != 1 else ''} due</b> — "
                     f"applications that have gone quiet.")

    if stats["unreviewed"]:
        lines.append(f"👀 {stats['unreviewed']} new job"
                     f"{'s' if stats['unreviewed'] != 1 else ''} waiting in the feed.")

    lines.append("")
    lines.append("🗓 <b>Last 7 days</b>")
    lines.append(f"• {stats['new_jobs']} new jobs surfaced")
    lines.append(f"• {stats['applied']} applied · {stats['saved']} saved · "
                 f"{stats['dismissed']} dismissed")
    lines.append(f"• {stats['runs']} runs")

    if stats["broken_boards"]:
        lines.append("")
        lines.append(f"🔌 {stats['broken_boards']} source"
                     f"{'s' if stats['broken_boards'] != 1 else ''} not working. "
                     f"See the Health tab.")

    if not stats["applied"] and stats["unreviewed"]:
        lines.append("")
        lines.append("<i>Nothing applied to this week. The feed is not the "
                     "point — the applications are.</i>")

    return "\n".join(lines)
