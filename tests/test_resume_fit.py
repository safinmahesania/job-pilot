"""Some jobs cannot have a resume written for them.

Three sessions were spent building guards against a model that kept inventing an
employer, a degree, a sales career. Every guard worked. Every guard was treating a
symptom, and the cause went unexamined the whole time: the model was being asked to
tailor a computer science student's resume to a healthcare sales posting, and there
is exactly one answer to that question. It is fiction. The model was not
misbehaving — it was obeying.

So the question is asked first: can this resume be written honestly at all? If the
job wants a sales background and there isn't one, nothing is generated, and the
refusal is itself the answer you wanted — you cannot honestly apply to this.

The tests that matter here are the two boundaries. The sales job must be refused.
The junior backend job must NOT be, because a tool that refuses the jobs you should
actually apply to is worse than one that never existed.
"""
import json

import pytest

from src import resume_fit
from src.resume_fit import JobDoesNotFitError, check_fit, overlap


def _json_resume() -> str:
    """The shape the model returns now. It fills fields; the code renders the page."""
    return json.dumps({
        "summary": "Software developer.",
        "skills": [{"label": "Programming & Markup Languages",
                    "skills": ["Dart", "Python"]}],
        "experience": [{"role": "Flutter Developer", "company": "Otrack",
                        "location": "", "dates": "2023 - 2024",
                        "bullets": ["Built a Flutter app."]},
                       {"role": "Application Support Officer",
                        "company": "Bank Alfalah", "location": "",
                        "dates": "2021 - 2022", "bullets": ["Troubleshot apps."]}],
        "education": [{"degree": "MSc", "institution": "Concordia",
                       "location": "", "dates": "2025 - 2026"}],
        "projects": [{"name": "Recipedia", "owner": "course",
                      "tech": ["Flutter"], "link": "",
                      "bullets": ["Cross-platform app."]}],
        "certificates": [], "volunteer": [],
    })


#: A software developer. Flutter, Java, Python, databases.
DEVELOPER = {
    "identity": {"titles": ["Software Developer", "Mobile Developer"]},
    "summary": "Software developer across frontend and backend, APIs and databases.",
    "skills": {
        "expert": ["Java", "Flutter", "Python", "RESTful APIs", "MySQL", "Git"],
        "proficient": ["Dart", "C#", "MVC Architecture", "Firebase", "SQLite"],
        "familiar": ["PyTorch", "Azure", "Apache Spark", ".NET 8", "Agile", "Scrum"],
    },
    "skill_categories": [
        {"label": "Programming & Markup Languages",
         "skills": ["Dart", "Python", "Java", "C#", "JavaScript", "SQL"]},
        {"label": "Databases", "skills": ["MySQL", "SQL Server", "SQLite"]},
    ],
    "experience": [
        {"role": "Flutter Developer", "company": "Otrack",
         "highlights": ["Built a Flutter mobile app with API-driven data and MVC."]},
        {"role": "Application Support Officer", "company": "Bank Alfalah",
         "highlights": ["Troubleshot banking applications via logs and SQL queries."]},
    ],
    "projects": [
        {"name": "Recipedia", "tech": ["Flutter", "Dart", "Firebase", "SQLite"],
         "highlights": ["Cross-platform mobile app with Firebase auth."]},
    ],
    "education": [{"degree": "MSc", "institution": "Concordia"}],
    "certificates": [], "volunteer": [],
}

SALES_JOB = [
    "Channel sales experience in healthcare or SaaS",
    "Inside sales and account management",
    "Sales operations knowledge",
    "Assisted Living Facility (ALF) sales background",
]

BACKEND_JOB = [
    "Python or Java",
    "REST APIs and relational databases",
    "Git and code review",
    "Agile team environment",
]

FLUTTER_JOB = [
    "Flutter and Dart",
    "Firebase and REST API integration",
    "MVC architecture and state management",
]


class TestTheJobThatStartedThis:
    def test_the_sales_job_is_refused(self):
        """The one that produced a fluent, professional resume for a salesperson
        who does not exist."""
        with pytest.raises(JobDoesNotFitError):
            check_fit({"title": "Canada Sales", "company": "PointClickCare"},
                      SALES_JOB, DEVELOPER)

    def test_it_shares_essentially_nothing_with_the_profile(self):
        score, matched = overlap(SALES_JOB, DEVELOPER)

        assert score < 0.05
        assert not matched

    def test_the_refusal_says_why(self):
        """"Failed" is not an answer. "You cannot honestly apply to this" is."""
        with pytest.raises(JobDoesNotFitError) as caught:
            check_fit({"title": "Canada Sales", "company": "PointClickCare"},
                      SALES_JOB, DEVELOPER)

        message = str(caught.value)

        assert "Canada Sales" in message
        assert "PointClickCare" in message
        assert "invent" in message
        assert "Nothing was generated" in message

    def test_nothing_is_generated(self, conn, monkeypatch, capture_llm):
        """The point of checking first. The downstream guards catch a fabricated
        resume; this stops one being attempted."""
        from src import apply

        monkeypatch.setattr(apply, "load_profile", lambda: dict(
            DEVELOPER, identity={"name": "Safin"}, summary="Dev.",
            education=[{"institution": "Concordia", "degree": "MSc"}]))
        monkeypatch.setattr(apply, "extract_requirements", lambda job: SALES_JOB)

        with pytest.raises(JobDoesNotFitError):
            apply.generate_resume({
                "title": "Canada Sales", "company": "PointClickCare",
                "description": "Sell healthcare SaaS to assisted living facilities.",
            })

        wrote = [u for _, u, _ in capture_llm.calls if "JSON SHAPE" in u]
        assert wrote == [], "a model was asked to write an impossible resume"


class TestTheJobsYouShouldApplyTo:
    """A tool that refuses the jobs you should actually apply to is worse than one
    that never existed. This is the boundary that matters most."""

    def test_a_junior_backend_job_goes_through(self):
        check_fit({"title": "Junior Backend Developer"}, BACKEND_JOB, DEVELOPER)

    def test_a_flutter_job_goes_through(self):
        check_fit({"title": "Mobile Developer"}, FLUTTER_JOB, DEVELOPER)

    def test_a_support_job_you_have_actually_done_goes_through(self):
        """Not a perfect match, but the person really did this work."""
        check_fit({"title": "Application Support"}, [
            "Troubleshooting production applications",
            "SQL and log analysis",
            "Incident documentation",
        ], DEVELOPER)

    def test_the_real_jobs_clear_the_bar_comfortably(self):
        """The threshold needs headroom, or it will start eating good jobs."""
        for requirements in (BACKEND_JOB, FLUTTER_JOB):
            score, _ = overlap(requirements, DEVELOPER)
            assert score > 0.4


class TestOtherImpossibleJobs:
    def test_a_nursing_job_is_refused(self):
        with pytest.raises(JobDoesNotFitError):
            check_fit({"title": "Registered Nurse"}, [
                "Nursing licence",
                "Patient care and triage",
                "Clinical documentation",
            ], DEVELOPER)

    def test_a_senior_role_in_a_stack_you_have_never_touched_is_refused(self):
        with pytest.raises(JobDoesNotFitError):
            check_fit({"title": "Senior Kubernetes SRE"}, [
                "5+ years Kubernetes and Terraform in production",
                "Go or Rust",
                "Prometheus, Grafana, on-call rotation",
            ], DEVELOPER)


class TestTheMatchIsNotFooledByBoilerplate:
    def test_filler_words_are_not_evidence_of_fit(self):
        """"Experience in sales" and "experience in Flutter" share "experience" and
        "in". Counting those is how a sales posting scores 8% instead of 0%."""
        score, matched = overlap([
            "Strong experience working in a collaborative team environment",
            "Excellent communication skills",
            "Ability to work independently",
        ], DEVELOPER)

        assert not matched

    def test_a_job_with_no_stated_requirements_is_not_refused(self):
        """Nothing was asked, so nothing can fail. Refusing here would block a job
        whose posting is simply vague."""
        check_fit({"title": "Software Engineer"}, [], DEVELOPER)


class TestThePromptPutsTheProfileClosestToThePen:
    """The job description used to sit immediately before "fill the template now" —
    five thousand characters of someone else's role, and the last thing the model
    read before it began to write. Recency does the rest.
    """

    def test_the_profile_comes_after_the_job_description(self, conn, monkeypatch,
                                                         capture_llm):
        from src import apply

        profile = dict(DEVELOPER, identity={"name": "Safin"}, summary="Dev.",
                       education=[{"institution": "Concordia", "degree": "MSc"}])
        monkeypatch.setattr(apply, "load_profile", lambda: profile)
        monkeypatch.setattr(apply, "extract_requirements", lambda job: BACKEND_JOB)
        monkeypatch.setattr(apply, "select_relevant_projects", lambda job, **kw: [0])
        capture_llm.reply = lambda system, user: (_json_resume(), "gemini")

        try:
            apply.generate_resume({"title": "Backend Developer", "company": "Shopify",
                                   "description": "Python, REST, Postgres."})
        except Exception:
            pass                      # the grounding guard may object; the prompt is what we want

        prompt = next(u for _, u, _ in capture_llm.calls if "JSON SHAPE" in u)

        job_at = prompt.index("Full description:")
        profile_at = prompt.index("MY PROFILE — the only facts")

        assert profile_at > job_at, (
            "the job description is closer to the moment of writing than the "
            "profile is — which is how a model steeped in a sales posting writes "
            "a salesperson"
        )

    def test_the_boundary_between_them_is_stated(self, conn, monkeypatch,
                                                 capture_llm):
        from src import apply

        profile = dict(DEVELOPER, identity={"name": "Safin"}, summary="Dev.",
                       education=[{"institution": "Concordia", "degree": "MSc"}])
        monkeypatch.setattr(apply, "load_profile", lambda: profile)
        monkeypatch.setattr(apply, "extract_requirements", lambda job: BACKEND_JOB)
        monkeypatch.setattr(apply, "select_relevant_projects", lambda job, **kw: [0])
        capture_llm.reply = lambda system, user: (_json_resume(), "gemini")

        try:
            apply.generate_resume({"title": "Backend Developer", "company": "Shopify",
                                   "description": "Python."})
        except Exception:
            pass

        prompt = next(u for _, u, _ in capture_llm.calls if "JSON SHAPE" in u)

        assert "Everything below is ME. Everything above is the job." in prompt


class TestTheTaskIsSelectionNotTransformation:
    def test_the_system_prompt_says_select_not_tailor(self):
        """"Tailor this resume to this job" invites the model to become whoever the
        job wants. It accepted the invitation."""
        from src.apply import _RESUME_SYSTEM

        assert "You SELECT from" in _RESUME_SYSTEM
        assert "You do not tailor" in _RESUME_SYSTEM

    def test_it_separates_order_and_emphasis_from_content(self):
        from src.apply import _RESUME_SYSTEM

        assert "It never decides CONTENT" in _RESUME_SYSTEM
        # Not claiming a skill you lack is honest. Deleting a job you had is not —
        # and the first phrasing of this rule taught the model to do exactly that.
        assert "Not claiming a skill you lack is honest" in _RESUME_SYSTEM


class TestAFailedExtractionIsNotAPerfectFit:
    """The hole the sales job walked through.

    `extract_requirements` is an LLM call, and it returns [] when it fails. The fit
    check treated an empty list as "nothing was asked, so nothing can fail" and
    returned a perfect score — which meant that whenever requirement extraction
    failed, every job fit perfectly, and the sales posting sailed straight through
    the check that existed to stop it.

    A default that turns a failure into an approval is not a default. It is a hole.
    """

    SALES_JD = (
        "Canada Sales - Talent Community. PointClickCare is a healthcare technology "
        "platform. We are building a talent community for Channel Sales, Inside "
        "Sales, Sales Operations, Account Management and Assisted Living Facility "
        "(ALF) sales. Candidates should have experience selling SaaS or enterprise "
        "software into healthcare. Travel to our Mississauga office required."
    )

    DEV_JD = (
        "Junior Backend Developer. Build and maintain REST APIs in Python, work "
        "with PostgreSQL, and ship through Git and code review in an Agile team."
    )

    def test_the_sales_job_is_still_refused_when_extraction_fails(self):
        job = {"title": "Canada Sales - Talent Community",
               "company": "PointClickCare",
               "description": self.SALES_JD}

        with pytest.raises(JobDoesNotFitError):
            check_fit(job, [], DEVELOPER)          # [] = extraction failed

    def test_a_real_dev_job_still_gets_through_when_extraction_fails(self):
        """The fallback is noisier. It must not become a wall."""
        job = {"title": "Junior Backend Developer", "company": "Shopify",
               "description": self.DEV_JD}

        check_fit(job, [], DEVELOPER)

    def test_the_fallback_reads_the_posting_itself(self):
        job = {"title": "Junior Backend Developer", "description": self.DEV_JD}

        score, matched = overlap([], DEVELOPER, job)

        assert "python" in matched
        assert score > 0.2

    def test_with_nothing_at_all_it_does_not_refuse(self):
        """No requirements, no title, no description. Refusing on the strength of
        no evidence would block a job for the crime of having a thin posting."""
        check_fit({"title": "", "description": ""}, [], DEVELOPER)


class TestNothingRealIsEverDropped:
    """The other half of the failure, and the half I caused.

    The system prompt said "an omission is honest; an invention is not". The model
    applied that to entire sections: told not to invent, it deleted three real jobs
    and wrote "(No work experience listed)" — reasoning, presumably, that a teaching
    assistantship is not relevant to a sales posting.

    It isn't. It is still a job you had. A job that does not value your experience
    does not erase your experience, and a resume that hides your career is as false
    as one that invents a career.

    Two rules, both binding: nothing false added, nothing real dropped. Satisfying
    one by breaking the other is not a compromise.
    """

    def test_the_prompt_says_omission_applies_to_claims_not_facts(self):
        from src.apply import _RESUME_SYSTEM

        assert "never to FACTS" in _RESUME_SYSTEM
        assert "Nothing real is ever dropped" in _RESUME_SYSTEM

    def test_the_prompt_names_the_exact_failure(self):
        """It happened. Say so, so it is not reasoned into again."""
        from src.apply import _RESUME_SYSTEM

        assert "(No work experience listed)" in _RESUME_SYSTEM

    def test_the_prompt_requires_every_populated_section(self):
        from src.apply import _RESUME_SYSTEM

        assert "MUST be filled" in _RESUME_SYSTEM
        assert "does not erase your experience" in _RESUME_SYSTEM
