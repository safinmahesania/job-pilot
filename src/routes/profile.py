"""Reading and writing your profile.

Two shapes, one file (config/profile.yaml): a structured view the form edits key by
key, and a raw-text escape hatch for the fields the form doesn't cover. The raw path
saves whatever you type as-is, so it validates the YAML and refuses to write a file it
can't parse — a broken profile.yaml would break every generation that reads it.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src import configio

router = APIRouter()


@router.get("/api/profile")
def get_profile():
    return {"data": configio.read_yaml("profile.yaml") or {}}


class ProfileData(BaseModel):
    data: dict


@router.post("/api/profile")
def save_profile(body: ProfileData):
    current = configio.read_yaml("profile.yaml") or {}
    current.update(body.data)          # only the keys the form manages
    configio.write_yaml("profile.yaml", current)
    return {"saved": True}


# Raw YAML escape hatch — for the fields the form doesn't cover.

@router.get("/api/profile/raw")
def get_profile_raw():
    return {"text": configio.read_text("profile.yaml")}


class ProfileText(BaseModel):
    text: str


@router.post("/api/profile/raw")
def save_profile_raw(body: ProfileText):
    try:
        configio.write_text("profile.yaml", body.text)
    except Exception as e:
        raise HTTPException(400, f"invalid YAML: {e}")
    return {"saved": True}
