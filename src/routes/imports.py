"""Bringing jobs in, and the documents/answers that come back out.

Three related surfaces the fetch pipeline doesn't cover:

  * IMPORT — jobs from a CSV/Excel file, a pasted posting, or an exported job-alert
    email. These are rate-limited and size-capped: an upload endpoint is the cheapest
    thing to abuse on a public tunnel, so anything oversized is refused before it is
    buffered, and the AI-backed ones are throttled.

  * MATERIALS — the generated resumes and cover letters, stored against a job and served
    back as PDF/DOCX/text. A document is always looked up by job_id, so the file returned
    belongs to the job it was requested for — one company's letter can never be served
    for another company's application.

  * AUTOFILL — the answers the browser extension uses to fill application forms, from
    local heuristics first and an AI pass only for the fields those couldn't place.
"""
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from src.deps import _db_dep, _get_setting, limiter
from src.paths import MAX_UPLOAD_BYTES, RATE_LIMIT_IMPORT

router = APIRouter()


# ── Autofill (browser extension) ──

@router.get("/api/autofill/data")
def autofill_data():
    """Canonical answers plus the user's own custom rules — no AI, instant."""
    from src import autofill
    return {"answers": autofill.answers(),
            "custom": autofill.custom_answers(),
            # Lists, for forms that ask for your history more than once. Flat answers
            # cannot fill a second "Job Title" box with a second job.
            "repeated": autofill.repeated()}


class ResolveField(BaseModel):
    id: str
    label: str = ""
    type: str = "text"
    options: list[str] = []


class ResolveRequest(BaseModel):
    fields: list[ResolveField]
    job_id: int | None = None


@router.post("/api/autofill/resolve")
def autofill_resolve(body: ResolveRequest, conn=Depends(_db_dep)):
    """AI-map the fields local heuristics couldn't place. Blank if unknown."""
    if _get_setting(conn, "generation_enabled", "1") != "1":
        raise HTTPException(403, "On-demand AI is off — enable it in Settings.")

    job = None
    if body.job_id:
        row = conn.execute(
            "SELECT title, company FROM jobs WHERE id=?", (body.job_id,)
        ).fetchone()
        job = dict(row) if row else None

    try:
        from src import autofill
        mapped = autofill.resolve([f.model_dump() for f in body.fields], job)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(502, f"{type(e).__name__}: {e}")

    # Keep what was answered, against the job it was answered for. These are the
    # screening questions — "why this company", "how long with React" — and they are
    # the part of an application you have no other copy of. Weeks later, when they
    # call, you should be able to read what you actually said. Blank answers are not
    # stored: a field the model declined to invent is not an answer.
    if body.job_id:
        by_id = {f.id: f.label for f in body.fields}
        for fid, answer in (mapped or {}).items():
            question = (by_id.get(fid) or "").strip()
            if not question or not str(answer).strip():
                continue
            conn.execute(
                "INSERT INTO application_answers (job_id, question, answer) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(job_id, question) DO UPDATE SET "
                "  answer = excluded.answer, created_at = datetime('now')",
                (body.job_id, question[:500], str(answer)[:5000]),
            )
        conn.commit()

    return {"answers": mapped}


# ── Materials (generated documents, bound to a job) ──

class MaterialSave(BaseModel):
    kind: str                 # "resume" | "cover"
    content: str
    provider: str = ""


@router.post("/api/jobs/{job_id}/materials")
def save_material(job_id: int, body: MaterialSave, conn=Depends(_db_dep)):
    """Store a generated document against this job."""
    from src import materials
    exists = conn.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not exists:
        raise HTTPException(404, "job not found")
    try:
        return materials.save(job_id, body.kind, body.content, body.provider)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/jobs/{job_id}/application")
def application_record(job_id: int, conn=Depends(_db_dep)):
    """Everything you sent for this job, in one place.

    Written for the moment the phone rings. The resume and cover letter are in one
    table, the screening answers in another, and before an interview you want all of
    it at once — not three clicks through two views. Bodies are included here, not
    just timestamps: the point is to read it.
    """
    from src import materials

    row = conn.execute(
        "SELECT id, title, company, apply_url, applied_on, status "
        "FROM jobs WHERE id=?", (job_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "no such job")

    docs = {}
    for kind in ("resume", "cover"):
        doc = materials.get(job_id, kind)
        if doc:
            docs[kind] = {"content": doc["content"],
                          "created_at": doc.get("created_at"),
                          "provider": doc.get("provider")}

    answers = [dict(r) for r in conn.execute(
        "SELECT question, answer, created_at FROM application_answers "
        "WHERE job_id=? ORDER BY id", (job_id,)
    ).fetchall()]

    return {"job": dict(row), "materials": docs, "answers": answers}


@router.get("/api/jobs/{job_id}/materials")
def list_materials(job_id: int):
    """What has been saved for this job (kinds + timestamps, not the bodies)."""
    from src import materials
    return {"job_id": job_id, "materials": materials.list_for(job_id)}


@router.delete("/api/jobs/{job_id}/materials/{kind}")
def delete_material(job_id: int, kind: str):
    from src import materials
    return {"deleted": materials.delete(job_id, kind)}


@router.get("/api/jobs/{job_id}/materials/{kind}/file")
def material_file(job_id: int, kind: str, format: str = "pdf", conn=Depends(_db_dep)):
    """Download a saved document. This is what the extension attaches.

    The document is looked up by job_id, so the file returned always belongs to
    the job it is requested for — there is no way to serve one company's letter
    for another company's application.
    """
    from src import materials

    job = conn.execute(
        "SELECT id, title, company FROM jobs WHERE id=?", (job_id,)
    ).fetchone()
    if not job:
        raise HTTPException(404, "job not found")

    doc = materials.get(job_id, kind)
    if not doc:
        raise HTTPException(
            404, f"no {kind} saved for this job — generate and save it first"
        )

    job = dict(job)
    if format == "docx":
        # Word, for the resume. Most ATS parse .docx at least as well as PDF, and
        # several parse it better.
        try:
            data = materials.to_docx(doc["content"], kind)
        except (RuntimeError, ValueError) as e:
            raise HTTPException(400, str(e))
        media = ("application/vnd.openxmlformats-officedocument"
                 ".wordprocessingml.document")
        ext = "docx"
    elif format == "pdf":
        try:
            data = materials.to_pdf(doc["content"], kind)
        except RuntimeError as e:
            raise HTTPException(500, str(e))
        media = "application/pdf"
        ext = "pdf"
    else:
        data = doc["content"].encode("utf-8")
        media = "text/plain; charset=utf-8"
        ext = "md" if kind == "resume" else "txt"

    name = materials.filename(job, kind, ext)
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# ── Importing jobs from outside the fetch pipeline ──

async def _read_capped(file) -> bytes:
    """Read an upload, refusing anything over the cap.

    UploadFile.read() with no argument pulls the entire body into memory, so the size
    limit has to be enforced here — a client that sends a 2 GB file should get a 413,
    not an out-of-memory kill. Read in chunks and stop the moment the cap is passed,
    so an oversized upload is rejected without ever being fully buffered.
    """
    chunks = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)     # 1 MB at a time
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                413, f"file too large — the limit is "
                     f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/api/import/file")
@limiter.limit(RATE_LIMIT_IMPORT)
async def import_file(request: Request, file: UploadFile = File(...)):
    """Import jobs from a CSV or Excel file."""
    from src import importers
    data = await _read_capped(file)
    try:
        rows = importers.parse_tabular(data, file.filename or "")
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"couldn't read that file: {e}")

    if not rows:
        raise HTTPException(
            400, "no usable rows — the file needs at least a title and a company column"
        )
    stats = importers.import_jobs(rows, source="import")
    return {"rows": len(rows), **stats}


class PastedJob(BaseModel):
    text: str


@router.post("/api/import/text")
@limiter.limit(RATE_LIMIT_IMPORT)
def import_text(request: Request, body: PastedJob, conn=Depends(_db_dep)):
    """Paste a whole job posting; the model pulls the fields out of it."""
    if _get_setting(conn, "generation_enabled", "1") != "1":
        raise HTTPException(403, "On-demand AI is off — enable it in Settings.")

    from src import importers
    try:
        job = importers.parse_text(body.text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(502, f"{type(e).__name__}: {e}")

    stats = importers.import_jobs([job], source="pasted", fetch_missing=False)
    return {"job": {"title": job["title"], "company": job["company"]}, **stats}


@router.post("/api/import/email-file")
@limiter.limit(RATE_LIMIT_IMPORT)
async def import_email_file(request: Request, file: UploadFile = File(...)):
    """Import jobs from a job-alert email you exported (.eml or .html).

    JobPilot has no mail credentials and no IMAP client. It reads the file you
    hand it and nothing else — there is no path from this app to your mailbox.
    """
    from src import importers
    data = await _read_capped(file)
    try:
        jobs = importers.parse_email_file(data, file.filename or "")
    except Exception as e:
        raise HTTPException(400, f"couldn't read that email: {e}")

    if not jobs:
        raise HTTPException(
            400, "no job links found in that email — is it a job-alert email?"
        )
    stats = importers.import_jobs(jobs)
    return {"found": len(jobs), **stats}


@router.post("/api/import/mail-drop")
def import_mail_drop():
    """Ingest every alert email sitting in data/mail_drop/.

    Drag your exported emails in there and press the button. Files are read and
    left alone.
    """
    from src import importers
    try:
        jobs, files = importers.read_mail_drop()
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(502, f"{type(e).__name__}: {e}")

    if not jobs:
        return {"files": len(files), "found": 0, "seen": 0, "imported": 0,
                "scored": 0, "unscored": 0, "duplicates": 0, "errors": 0}

    stats = importers.import_jobs(jobs)
    return {"files": len(files), "found": len(jobs), **stats}


@router.get("/api/import/template")
def import_template():
    """A starter CSV with the columns the importer understands."""
    header = "title,company,location,apply_url,description,posted_date,job_type,salary\n"
    example = ('Junior Backend Developer,Shopify,"Toronto, Canada",'
               'https://example.com/jobs/1,"We are looking for...",2026-07-01,Full-time,\n')
    return Response(
        content=header + example,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="jobpilot_import_template.csv"'},
    )
