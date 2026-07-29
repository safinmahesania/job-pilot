"""Two related cover-letter fixes:

1. The fabrication guard now anchors on the job description: it only flags a technology
   the letter claims if that technology actually appears in the posting. Random
   capitalised words — company names, cities, buzzwords — can't be "taken from the
   posting" if they aren't in it, so they stop producing false refusals.

2. Project selection is deterministic, so the resume and the cover letter feature the
   same projects for one application instead of two independent guesses.
"""
from unittest.mock import patch

from src import resume_guard


class TestGuardAnchorsOnJobDescription:
    PROFILE = {"identity": {"name": "Sam"}, "skills": ["Python", "Flutter"]}
    JD = "We're hiring a developer with Python, AWS, and Docker experience in Toronto."

    def test_random_capitalised_words_not_in_jd_are_not_flagged(self):
        # PolicyMe's, Agile, Toronto — none are technologies claimed FROM the posting.
        letter = ("I'd love to join PolicyMe's team. Your Agile culture and Toronto "
                  "office appeal to me. I build with Python.")
        problems = resume_guard.check_cover_letter_prose(
            letter, self.PROFILE, target_company="PolicyMe", job_description=self.JD)
        assert problems == []

    def test_a_technology_from_the_posting_not_in_profile_is_flagged(self):
        # AWS is in the JD and not in the profile -> a real fabrication.
        letter = "I have years of AWS experience."
        problems = resume_guard.check_cover_letter_prose(
            letter, self.PROFILE, target_company="PolicyMe", job_description=self.JD)
        assert any("AWS" in p for p in problems)

    def test_a_technology_not_in_the_posting_is_left_alone(self):
        # Kubernetes isn't in the JD, so even though it's not in the profile, the letter
        # didn't lift it from the posting — not this check's job to police.
        letter = "On my own time I explored Kubernetes."
        problems = resume_guard.check_cover_letter_prose(
            letter, self.PROFILE, target_company="PolicyMe", job_description=self.JD)
        assert problems == []

    def test_no_job_description_falls_back_to_profile_only(self):
        # Backward compatible: with no JD, an out-of-profile technology still flags.
        problems = resume_guard.check_cover_letter_prose(
            "I use React daily.", self.PROFILE, target_company="X")
        assert any("React" in p for p in problems)

    def test_profile_skill_never_flags_even_when_in_jd(self):
        letter = "I build with Python."
        problems = resume_guard.check_cover_letter_prose(
            letter, self.PROFILE, target_company="X", job_description=self.JD)
        assert problems == []


class TestDeterministicProjectSelection:
    PROFILE = {"projects": [
        {"name": "JobPilot", "description": "Python FastAPI job tool",
         "tech": ["Python", "FastAPI", "SQLite"]},
        {"name": "Mashric App", "description": "React Native cafe app",
         "tech": ["React Native", "Expo"]},
        {"name": "Erlang Bank", "description": "concurrent banking sim",
         "tech": ["Erlang"]},
        {"name": "Clojure DFS", "description": "graph search", "tech": ["Clojure"]},
        {"name": "Old Site", "description": "wordpress", "tech": ["PHP"]},
    ]}

    def test_same_job_picks_same_projects_every_call(self):
        from src import apply
        job = {"title": "Python Developer", "company": "X",
               "description": "Python, FastAPI, SQLite required."}
        with patch.object(apply, "load_profile", return_value=self.PROFILE):
            a = apply.select_relevant_projects(job, top_n=3)
            b = apply.select_relevant_projects(job, top_n=3)
        assert a == b                       # deterministic

    def test_most_relevant_project_ranks_first(self):
        from src import apply
        job = {"title": "Python Developer", "company": "X",
               "description": "We need Python, FastAPI and SQLite."}
        with patch.object(apply, "load_profile", return_value=self.PROFILE):
            picked = apply.select_relevant_projects(job, top_n=3)
        # JobPilot (Python/FastAPI/SQLite) overlaps the job most, so it leads.
        assert self.PROFILE["projects"][picked[0]]["name"] == "JobPilot"

    def test_resume_and_cover_letter_agree_on_the_same_three_projects(self):
        # Both call the same deterministic ranking with the same count, so they feature
        # the identical set — one story across the resume and its letter, not two.
        from src import apply
        job = {"title": "Python Developer", "company": "X",
               "description": "Python, FastAPI, SQLite, React Native."}
        with patch.object(apply, "load_profile", return_value=self.PROFILE):
            resume_picks = apply.select_relevant_projects(job)
            cover_picks = apply.select_relevant_projects(job)
        assert resume_picks == cover_picks
        assert len(resume_picks) == min(3, len(self.PROFILE["projects"]))


class TestFastModeSkipsRevise:
    """Behind a proxy that times out long requests (a Cloudflare Tunnel cuts at ~100s),
    the cover letter's two model calls could exceed the limit and surface as a 524.
    Fast mode skips the revise pass — one call instead of two.
    """
    PROFILE = {"identity": {"name": "Sam"}, "skills": {"expert": ["Python"]},
               "projects": [{"name": "P", "tech": ["Python"], "description": "x"}]}
    JOB = {"title": "Dev", "company": "X", "description": "Python role"}

    def _count_calls(self, fast):
        from unittest.mock import patch
        from src import apply
        calls = []

        def fake_gen(system, user, personal=False):
            calls.append(1)
            return ("Dear Hiring Manager,\n\nI am a Python developer. " * 8, "gemini")

        with patch.object(apply, "load_profile", return_value=self.PROFILE), \
             patch.object(apply, "llm") as mllm, \
             patch.object(apply, "redacting", return_value=False), \
             patch.object(apply, "fill_contact", side_effect=lambda t, p: t), \
             patch.object(apply, "extract_requirements", return_value=["Python"]), \
             patch.object(apply.resume_guard, "check_cover_letter_prose",
                          return_value=[]):
            mllm.generate = fake_gen
            apply.generate_cover_letter(self.JOB, fast=fast)
        return len(calls)

    def test_fast_uses_one_call(self):
        assert self._count_calls(fast=True) == 1

    def test_full_uses_two_calls(self):
        assert self._count_calls(fast=False) == 2


class TestCoverLetterNeverRefuses:
    """A cover letter must never be refused outright — that leaves the person with
    nothing. If the draft names a technology from the posting that isn't in the profile,
    it's regenerated once with that name forbidden; if it still slips through, the letter
    is returned with a non-blocking warning to edit, not a wall of red.
    """
    PROFILE = {"identity": {"name": "Sam"}, "skills": {"expert": ["Python"]},
               "projects": [{"name": "P", "tech": ["Python"], "description": "x"}]}
    JOB = {"title": "Dev", "company": "X",
           "description": "Need React, AWS, PostgreSQL, Python."}

    def _run(self, draft_text):
        from unittest.mock import patch
        from src import apply

        def fake_gen(system, user, personal=False):
            return (draft_text, "gemini")

        with patch.object(apply, "load_profile", return_value=self.PROFILE), \
             patch.object(apply, "llm") as mllm, \
             patch.object(apply, "redacting", return_value=False), \
             patch.object(apply, "fill_contact", side_effect=lambda t, p: t), \
             patch.object(apply, "extract_requirements", return_value=["React", "AWS"]):
            mllm.generate = fake_gen
            return apply.generate_cover_letter(self.JOB, fast=True)

    def test_letter_with_fabrication_is_returned_not_refused(self):
        # Model stubbornly keeps naming React/AWS even on retry -> still returns a letter.
        result = self._run("Dear Hiring Manager,\n\nI love React and AWS. " * 6)
        assert result["text"]                       # a letter, not an exception
        assert result["warnings"]                   # with a heads-up

    def test_clean_letter_has_no_warnings(self):
        result = self._run("Dear Hiring Manager,\n\nI build with Python daily. " * 6)
        assert result["text"]
        assert result["warnings"] == []


class TestAlwaysThreeProjects:
    """An application features three projects — always three, chosen by relevance, and
    when nothing matches, simply the three most recent (the ranking falls back to profile
    order, which is newest-first)."""

    from unittest.mock import patch as _patch

    PROFILE = {"projects": [
        {"name": "Newest", "description": "flutter supabase app", "tech": ["Flutter"]},
        {"name": "Second", "description": "python fastapi tool", "tech": ["Python"]},
        {"name": "Third", "description": "react native app", "tech": ["React Native"]},
        {"name": "Fourth", "description": "erlang concurrency", "tech": ["Erlang"]},
        {"name": "Fifth", "description": "clojure graph", "tech": ["Clojure"]},
    ]}

    def test_three_are_selected_when_the_profile_has_enough(self):
        from unittest.mock import patch
        from src import apply
        job = {"title": "Dev", "company": "X", "description": "Python FastAPI role."}
        with patch.object(apply, "load_profile", return_value=self.PROFILE):
            picked = apply.select_relevant_projects(job)
        assert len(picked) == 3

    def test_a_job_that_matches_nothing_still_returns_three(self):
        """No keyword overlap at all — the count is held, and the three are the most
        recent, because a tie keeps profile order (newest first)."""
        from unittest.mock import patch
        from src import apply
        job = {"title": "Underwater Basket Weaver", "company": "X",
               "description": "Weaving reeds beneath the waves. No software here."}
        with patch.object(apply, "load_profile", return_value=self.PROFILE):
            picked = apply.select_relevant_projects(job)
        assert len(picked) == 3
        assert picked == [0, 1, 2]          # the three newest, in order

    def test_fewer_than_three_projects_does_not_crash(self):
        from unittest.mock import patch
        from src import apply
        thin = {"projects": [{"name": "Only one", "description": "python",
                              "tech": ["Python"]}]}
        job = {"title": "Dev", "company": "X", "description": "Python role."}
        with patch.object(apply, "load_profile", return_value=thin):
            picked = apply.select_relevant_projects(job)
        assert picked == [0]

    def test_no_projects_returns_empty_not_an_error(self):
        from unittest.mock import patch
        from src import apply
        job = {"title": "Dev", "company": "X", "description": "Python role."}
        with patch.object(apply, "load_profile", return_value={"projects": []}):
            assert apply.select_relevant_projects(job) == []


class TestResumeHonoursThePreferredProjects:
    """The resume is handed the same project indices the cover letter uses, and marks
    them in the list the model sees, so both documents feature the same work."""

    PROFILE = {"projects": [
        {"name": "Alpha", "tech": ["Python"], "highlights": ["Built X"]},
        {"name": "Beta", "tech": ["Flutter"], "highlights": ["Shipped Y"]},
        {"name": "Gamma", "tech": ["Erlang"], "highlights": ["Scaled Z"]},
    ]}

    def test_preferred_projects_are_starred(self):
        from src import resume_select
        out = resume_select.choices(self.PROFILE, preferred_projects=[0, 2])
        # The two chosen carry the marker; the un-chosen one does not.
        assert "★ Alpha" in out
        assert "★ Gamma" in out
        assert "★ Beta" not in out

    def test_without_a_preference_it_asks_the_model_to_choose(self):
        from src import resume_select
        out = resume_select.choices(self.PROFILE)
        assert "★" not in out
        assert "most relevant" in out
