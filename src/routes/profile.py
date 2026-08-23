"""Reading and writing your profile — one JSONB document per user.

Two shapes over the same user_profiles.profile column: a structured view the form
edits key by key, and a raw-YAML escape hatch for fields the form doesn't cover. The
raw path saves whatever you type, so it validates the YAML first and refuses to store
something it can't parse — a broken profile would break every generation that reads it.

Every route is scoped to the signed-in user: there is no shared profile any more.
"""
import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.auth import current_user_id
from src.deps import _db_dep, _user_profile

router = APIRouter()


def _save_profile(conn, user_id: str, profile: dict) -> None:
    """Persist the whole profile document for one user."""
    import json
    # A brand-new Supabase signup exists in auth.users; the on_auth_user_created trigger
    # is meant to mirror it into public.users. If that trigger isn't installed on the
    # database, the row is missing and the profile insert FK-fails ("not saved"). Create
    # the row defensively so a first-time user's very first save always works.
    conn.execute(
        "INSERT INTO users (id) VALUES (?) ON CONFLICT (id) DO NOTHING", (user_id,))
    conn.execute(
        "INSERT INTO user_profiles (user_id, profile, updated_at) "
        "VALUES (?, ?::jsonb, now()) "
        "ON CONFLICT (user_id) DO UPDATE "
        "SET profile = excluded.profile, updated_at = now()",
        (user_id, json.dumps(profile)))
    conn.commit()


@router.get("/api/profile")
def get_profile(user_id: str = Depends(current_user_id), conn=Depends(_db_dep)):
    return {"data": _user_profile(conn, user_id)}


class ProfileData(BaseModel):
    data: dict


@router.post("/api/profile")
def save_profile(body: ProfileData, user_id: str = Depends(current_user_id),
                 conn=Depends(_db_dep)):
    current = _user_profile(conn, user_id)
    current.update(body.data)          # only the keys the form manages
    _save_profile(conn, user_id, current)
    return {"saved": True}


# Raw YAML escape hatch — for the fields the form doesn't cover.

@router.get("/api/profile/raw")
def get_profile_raw(user_id: str = Depends(current_user_id), conn=Depends(_db_dep)):
    profile = _user_profile(conn, user_id)
    text = yaml.safe_dump(profile, sort_keys=False, allow_unicode=True) if profile else ""
    return {"text": text}


class ProfileText(BaseModel):
    text: str


@router.post("/api/profile/raw")
def save_profile_raw(body: ProfileText, user_id: str = Depends(current_user_id),
                     conn=Depends(_db_dep)):
    try:
        parsed = yaml.safe_load(body.text) or {}
        if not isinstance(parsed, dict):
            raise ValueError("profile must be a mapping, not a list or scalar")
    except Exception as e:
        raise HTTPException(400, f"invalid YAML: {e}")
    _save_profile(conn, user_id, parsed)
    return {"saved": True}


@router.delete("/api/account")
def delete_account(user_id: str = Depends(current_user_id), conn=Depends(_db_dep)):
    """Permanently delete the caller's account and ALL their personal data.

    Every per-user table (profile, user_jobs, application_answers, materials,
    notifications, settings) has ON DELETE CASCADE from public.users, and
    public.users itself cascades from auth.users — so removing the auth row erases
    everything, including the login. If deleting the auth row isn't permitted, we
    still delete the app-side data (public.users cascade) so no personal data is left.
    """
    try:
        conn.execute("DELETE FROM auth.users WHERE id = ?", (user_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    return {"deleted": True}
