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
from src.paths import FIT_MIN_OVERLAP
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

        wrote = [u for _, u, _ in capture_llm.calls if "RETURN THIS JSON" in u]
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

        prompt = next(u for _, u, _ in capture_llm.calls if "RETURN THIS JSON" in u)

        job_at = prompt.index("Full description:")
        profile_at = prompt.index("MY JOBS")

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

        prompt = next(u for _, u, _ in capture_llm.calls if "RETURN THIS JSON" in u)

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


class TestTheFallbackWhenTheModelIsDown:
    """extract_requirements is an LLM call, and an LLM call can fail. What happens
    then is not a corner case — it is what the whole check reduces to on a bad day.

    The first fallback returned a perfect score, so a failed extraction meant every
    job fit and the sales posting sailed through. That was fixed by falling back to
    the job's own text.

    Which was also wrong, in the other direction, and quieter about it. A job
    description is two thousand words of culture, benefits and encouragement with a
    dozen technologies buried in it. Matched as a bag of words, a TD Bank software
    engineering internship — Java, Python, SQL, REST, Git — came out at 11% and
    would have been REFUSED. Its matched terms were "critical", "day", "designed",
    "features". The noise stopped the sales job and it stopped the right job too,
    and nobody noticed because nobody looks at the path the code takes when the
    model is down.
    """

    PROFILE = {
        "summary": "Software developer across frontend and backend.",
        "skills": {
            "expert": ["Java", "Flutter", "Python", "SQL", "RESTful APIs", "Git"],
            "proficient": ["C#", ".NET 8", "Firebase", "MVC Architecture"],
            "familiar": ["Azure", "PyTorch", "Apache Spark"],
        },
        "skill_categories": [{"label": "Databases",
                              "skills": ["MySQL", "SQL Server"]}],
        "experience": [{"role": "Flutter Developer", "company": "Otrack",
                        "highlights": ["Built a Flutter app with REST APIs."]}],
        "projects": [{"name": "Recipedia", "tech": ["Flutter", "Dart"]}],
        "education": [{"degree": "MSc Computer Science",
                       "institution": "Concordia"}],
    }

    #: The real posting, prose and all. The technologies are in the last sentence.
    TD_BANK = {
        "title": "Software Engineer Intern/Co-op (Fall 2026)",
        "description": (
            "As a Software Engineer Intern at TD, you will work on a collaborative, "
            "agile team building the platforms that serve millions of customers "
            "every day. This is a critical role. You will be designing and "
            "implementing features in a fast-paced environment, mentored through "
            "code review and pair programming. We value curiosity, ownership and a "
            "growth mindset. TD is committed to an inclusive workplace where every "
            "colleague can thrive. Fluency in English is required. Requirements: "
            "enrolled in a Computer Science or Software Engineering program. "
            "Experience with Java or Python. Familiarity with SQL and relational "
            "databases. Understanding of REST APIs and version control with Git."
        ),
    }

    SALES = {
        "title": "Canada Sales - Talent Community",
        "description": (
            "Join our sales talent community. We seek Account Executives and Sales "
            "Development Representatives to drive revenue growth, manage pipeline "
            "in Salesforce, exceed quota and build relationships with healthcare "
            "providers across Canada. Every day you will be closing deals in a "
            "fast-paced, collaborative environment."
        ),
    }

    def test_a_real_developer_job_survives_a_model_outage(self):
        """It did not. 11%, against a floor of 15%."""
        score, _ = resume_fit.overlap([], self.PROFILE, self.TD_BANK)

        assert score >= FIT_MIN_OVERLAP, (
            "with no model, a Java/Python/SQL/REST internship is refused"
        )

    def test_it_matches_on_technologies_not_on_the_word_day(self):
        _, matched = resume_fit.overlap([], self.PROFILE, self.TD_BANK)

        assert {"java", "python", "sql"} <= matched
        for noise in ("day", "critical", "features", "english", "mindset"):
            assert noise not in matched

    def test_the_sales_job_is_still_refused_without_a_model(self):
        """The fallback got quieter, not softer."""
        score, _ = resume_fit.overlap([], self.PROFILE, self.SALES)

        assert score < FIT_MIN_OVERLAP

        with pytest.raises(resume_fit.JobDoesNotFitError):
            resume_fit.check_fit(self.SALES, [], self.PROFILE)

    def test_the_title_always_counts(self):
        """"Software Developer" and "Sales Representative" are the two most honest
        words in any posting."""
        bare = {"title": "Sales Representative", "description": ""}

        score, _ = resume_fit.overlap([], self.PROFILE, bare)

        assert score < FIT_MIN_OVERLAP

    def test_an_empty_extraction_is_not_a_perfect_score(self):
        """The original hole, still closed. A default that turns a failure into an
        approval is not a default."""
        score, _ = resume_fit.overlap([], self.PROFILE, self.SALES)

        assert score < 1.0


class TestSoftSkillsAreNotEvidence:
    """A "Canada Sales - Talent Community" posting came out as a 15% match for a
    computer science student — exactly the floor — on the strength of these words:

        ai, dynamic, lake, management, office, ops

    None of them is a technology. They matched because the profile lists, quite
    correctly, "Soft Skills: Leadership | Critical Thinking | Communication | Time
    Management", "Languages: English - Fluent | French - Beginner", and "Software &
    Productivity Tools: MS Office | Slack".

    Those belong on the resume. A recruiter reads them and they mean something. They
    are not evidence that you can write software — because everyone has them,
    including everyone applying to the sales role.
    """

    CATEGORIES = [
        {"label": "Programming & Markup Languages",
         "skills": ["Dart", "Python", "Java", "C#", "SQL", "JavaScript"]},
        {"label": "Frameworks & Architecture",
         "skills": ["Flutter", ".NET 8", "RESTful APIs", "MVC Architecture"]},
        {"label": "Databases", "skills": ["MySQL", "SQL Server", "SQLite"]},
        {"label": "Developer Tools", "skills": ["Git", "GitHub", "Postman"]},
        {"label": "Languages",
         "skills": ["English - Fluent", "Urdu - Fluent", "French - Beginner"]},
        {"label": "Soft Skills",
         "skills": ["Leadership", "Critical Thinking", "Communication",
                    "Problem-Solving", "Time Management", "Collaboration"]},
        {"label": "Software & Productivity Tools",
         "skills": ["MS Office (Word, Excel, PowerPoint)", "Slack", "Jira"]},
    ]

    PROFILE = {
        "summary": "Software developer across frontend and backend.",
        "skills": {"expert": ["Java", "Python", "Flutter", "SQL", "RESTful APIs"],
                   "proficient": ["C#", ".NET 8", "Dart"],
                   "familiar": ["Azure", "PyTorch"]},
        "skill_categories": CATEGORIES,
        "experience": [{"role": "Flutter Developer",
                        "highlights": ["Built a Flutter app with REST APIs."]}],
        "projects": [{"name": "Recipedia", "tech": ["Flutter", "Dart"]}],
    }

    SALES = {
        "title": "Canada Sales - Talent Community",
        "description": (
            "Account Executives and Sales Development Representatives to drive "
            "revenue growth, manage pipeline in Microsoft Dynamics and Salesforce, "
            "exceed quota. Strong communication and time management skills. "
            "Proficiency in MS Office. Fluent English required."
        ),
    }

    def test_the_sales_job_matches_nothing(self):
        """It was 15%. The floor is 15%."""
        score, matched = resume_fit.overlap([], self.PROFILE, self.SALES)

        assert score < FIT_MIN_OVERLAP
        assert matched == set()

    def test_and_is_refused(self):
        with pytest.raises(resume_fit.JobDoesNotFitError):
            resume_fit.check_fit(self.SALES, [], self.PROFILE)

    def test_soft_skills_do_not_reach_the_comparison(self):
        terms = resume_fit.profile_terms(self.PROFILE)

        for word in ("communication", "leadership", "office", "excel",
                     "powerpoint", "adaptability"):
            assert word not in terms

    def test_human_languages_do_not_either(self):
        """"Fluent English" is on a nurse's resume too."""
        terms = resume_fit.profile_terms(self.PROFILE)

        assert "english" not in terms
        assert "urdu" not in terms

    def test_the_technical_categories_still_count(self):
        """"Programming & Markup Languages" contains the word "languages" and is
        very much evidence. The distinction is what the category is ABOUT."""
        terms = resume_fit.profile_terms(self.PROFILE)

        for word in ("java", "python", "flutter", "sql", "git", "mysql"):
            assert word in terms

    def test_a_real_developer_job_is_unharmed(self):
        job = {"title": "Software Engineer Intern",
               "description": ("Java or Python. SQL and relational databases. REST "
                               "APIs, Git. Fluency in English required. Strong "
                               "communication skills.")}

        score, matched = resume_fit.overlap([], self.PROFILE, job)

        assert score >= FIT_MIN_OVERLAP
        assert {"java", "python", "sql"} <= matched


class TestAPostingCanOpenASentenceWithATechnology:
    """"Python, Kotlin, React, GraphQL, Postgres, AWS." is a sentence, and its first
    word is a technology.

    Reading a RESUME, a capital at the start of a sentence is grammar, not a claim —
    "Software developer with..." must not read as a tool called Software. Reading a
    POSTING, it is the first item of a list, and dropping it lost the first
    technology in every such list. A Faire Product Engineer role — Python, Kotlin,
    React, GraphQL, Postgres, AWS — came out at 12% for a Python developer.

    Same signal, two readers, opposite defaults.
    """

    PROFILE = TestSoftSkillsAreNotEvidence.PROFILE

    def test_the_first_technology_in_a_list_is_read(self):
        job = {"title": "Product Engineer",
               "description": ("Product Engineer. Backend systems and APIs. "
                               "Python, Kotlin, React, GraphQL, Postgres, AWS.")}

        score, matched = resume_fit.overlap([], self.PROFILE, job)

        assert "python" in matched
        assert score >= FIT_MIN_OVERLAP

    def test_a_resume_sentence_opening_with_a_capital_is_still_not_a_claim(self):
        """The other reader keeps its own default."""
        from src.resume_guard import named_technologies

        assert named_technologies("Software developer. Driven to build.") == []

    def test_the_posting_reader_and_the_resume_reader_disagree_on_purpose(self):
        from src.resume_guard import named_technologies

        text = "Python and SQL are required."

        assert named_technologies(text) == ["SQL"]
        assert "Python" in named_technologies(text, sentence_start=True)


class TestTheDecisionIsWhichNotHowMany:
    """The ratio was tuned three times and never worked.

    "Of everything this posting asks for, what fraction is in the profile" sounds
    like the right question. It is not, because the denominator is a job description
    — two thousand words of culture, benefits and section headings with a dozen
    technologies buried in them. Real developer jobs came out at 10-18% against a
    15% floor. The measure had no power left to discriminate; the floor was cutting
    through the middle of the noise. Its matched terms were "what", "every", "day",
    "best".

    Counting instead of dividing removed the poisoned denominator, and got closer —
    but a count alone still could not see the thing that matters. DoorDash's backend
    role names Kotlin, gRPC, Postgres, Kubernetes and AWS, of which this profile has
    none, and Java, which it has. One match. A floor of two refused it: a Java
    backend job, refused to a Java developer. Meanwhile an IT operations role also
    scored one — on the word "Agile" — and a network engineering role scored one on
    "Azure".

    The difference between them is not HOW MANY. It is WHICH.

    A job that asks for a programming language you write is a job you can write an
    honest resume for. You may be underqualified; that is a different question and
    not this one's business. There is simply nothing you would have to invent.
    """

    PROFILE = {
        "summary": "Software developer.",
        "skills": {"expert": ["Java", "Python", "Flutter", "SQL"],
                   "proficient": ["C#", ".NET 8"],
                   "familiar": ["Azure", "PyTorch"]},
        "skill_categories": [
            {"label": "Programming & Markup Languages",
             "skills": ["Dart", "Python", "Java", "C#", "JavaScript", "SQL"]},
            {"label": "Cloud & Data Platforms", "skills": ["Azure", "Firebase"]},
            {"label": "Developer Tools", "skills": ["Git", "GitHub", "Postman"]},
            {"label": "Methodologies & Practices", "skills": ["Agile", "Scrum"]},
            {"label": "Soft Skills", "skills": ["Communication", "Leadership"]},
        ],
        "experience": [{"role": "Flutter Developer",
                        "highlights": ["Built a Flutter app."]}],
        "projects": [{"name": "Recipedia", "tech": ["Flutter", "Dart"]}],
    }

    def _check(self, title, description):
        resume_fit.check_fit({"title": title, "description": description},
                             [], self.PROFILE)

    def test_one_language_you_write_is_enough(self):
        """DoorDash. Kotlin, gRPC, Postgres, Kubernetes, AWS — and Java."""
        self._check("Software Engineer, Backend",
                    "Backend. Kotlin, Java, gRPC, Postgres, Kubernetes, AWS.")

    def test_one_language_you_write_is_enough_even_in_a_stack_you_lack(self):
        """Faire. Python, and then nothing else of his."""
        self._check("Product Engineer",
                    "Backend systems and APIs. Python, Kotlin, React, GraphQL, "
                    "Postgres, AWS.")

    def test_agile_is_not_a_language(self):
        """An IT operations role scored one, on the word every posting contains."""
        with pytest.raises(resume_fit.JobDoesNotFitError):
            self._check("IT Operations Administrator",
                        "SaaS platforms, Okta, Jamf, device lifecycle. Agile. "
                        "Strong communication.")

    def test_azure_alone_is_not_a_language(self):
        with pytest.raises(resume_fit.JobDoesNotFitError):
            self._check("Network Engineering",
                        "Cisco, BGP, firewalls, cloud architecture, Azure.")

    def test_two_tools_without_a_language_still_pass(self):
        """A DevOps role naming Azure and Git is in his field. One naming only
        "Agile" is not."""
        self._check("Cloud Operations",
                    "Azure infrastructure, Git-based deployment, Firebase hosting.")

    def test_the_sales_job(self):
        with pytest.raises(resume_fit.JobDoesNotFitError):
            self._check("Canada Sales - Talent Community",
                        "Account Executives. Revenue growth, pipeline in Microsoft "
                        "Dynamics and Salesforce, quota. MS Office. Fluent English. "
                        "Strong communication and time management.")

    def test_the_recruiter_job(self):
        with pytest.raises(resume_fit.JobDoesNotFitError):
            self._check("Recruiter (6 Month Contract)",
                        "Full-cycle recruiting. Source candidates, manage pipeline, "
                        "partner with hiring managers. Best-in-class experience.")

    def test_a_qa_role_in_tools_he_does_not_have(self):
        """Wattpad. Selenium, Cypress, Playwright, and no language of his. He can
        apply if he likes — but a TAILORED resume for it would be reaching, and this
        check exists to say so."""
        with pytest.raises(resume_fit.JobDoesNotFitError):
            self._check("Product Quality Assurance Specialist",
                        "Test plans, regression testing. Selenium, Cypress, "
                        "Playwright. Attention to detail.")

    def test_every_real_developer_job_passes(self):
        for title, description in [
            ("Software Engineer Intern",
             "Java or Python. SQL and relational databases. REST APIs. Git."),
            ("Software Developer",
             "C#, .NET, SQL Server, Azure, REST APIs, microservices."),
            ("Fullstack Developer (Python)",
             "Python, Django, JavaScript, PostgreSQL, Git, AWS."),
            ("Data Scientist",
             "Machine learning. Python, Pandas, NumPy, scikit-learn, PyTorch."),
            ("Front-End Developer II",
             "React, TypeScript, C#, .NET, Azure, GitHub."),
        ]:
            self._check(title, description)      # no exception

    def test_the_word_c_does_not_match_critical(self):
        """"C" is a language and a letter. Without a token boundary it matches
        "critical", "cloud", "code" and every other word starting with c."""
        wanted = resume_fit.technologies_wanted(
            {"title": "Sales", "description": "Critical communication in a "
                                              "collaborative culture."},
            self.PROFILE)

        assert "c" not in wanted

    def test_human_languages_are_not_programming_languages(self):
        """"Fluent English and French" must not read as two languages he writes."""
        langs = resume_fit.languages_wanted(
            {"title": "Sales", "description": "Fluent English and French required."},
            self.PROFILE)

        assert langs == set()
