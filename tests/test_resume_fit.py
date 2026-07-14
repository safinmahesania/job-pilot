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
import pytest

from src import resume_fit
from src.resume_fit import JobDoesNotFitError, check_fit, overlap


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
        {"role": "Flutter Developer",
         "highlights": ["Built a Flutter mobile app with API-driven data and MVC."]},
        {"role": "Application Support Officer",
         "highlights": ["Troubleshot banking applications via logs and SQL queries."]},
    ],
    "projects": [
        {"name": "Recipedia", "tech": ["Flutter", "Dart", "Firebase", "SQLite"],
         "highlights": ["Cross-platform mobile app with Firebase auth."]},
    ],
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

        wrote = [u for _, u, _ in capture_llm.calls if "TEMPLATE TO FILL" in u]
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
        capture_llm.reply = lambda system, user: ("# Safin\n", "gemini")

        try:
            apply.generate_resume({"title": "Backend Developer", "company": "Shopify",
                                   "description": "Python, REST, Postgres."})
        except Exception:
            pass                      # the grounding guard may object; the prompt is what we want

        prompt = next(u for _, u, _ in capture_llm.calls if "TEMPLATE TO FILL" in u)

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
        capture_llm.reply = lambda system, user: ("# Safin\n", "gemini")

        try:
            apply.generate_resume({"title": "Backend Developer", "company": "Shopify",
                                   "description": "Python."})
        except Exception:
            pass

        prompt = next(u for _, u, _ in capture_llm.calls if "TEMPLATE TO FILL" in u)

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
        assert "An omission is honest" in _RESUME_SYSTEM
