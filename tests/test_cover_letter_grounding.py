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

    def test_resume_and_cover_letter_would_agree_on_top_projects(self):
        # Both call the same deterministic function, so the cover letter's top_n is a
        # prefix of the resume's ranking — they feature the same projects.
        from src import apply
        job = {"title": "Python Developer", "company": "X",
               "description": "Python, FastAPI, SQLite, React Native."}
        with patch.object(apply, "load_profile", return_value=self.PROFILE):
            resume_picks = apply.select_relevant_projects(job, top_n=3)
            cover_picks = apply.select_relevant_projects(job, top_n=2)
        assert cover_picks == resume_picks[:2]


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
