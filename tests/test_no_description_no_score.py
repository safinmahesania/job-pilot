"""A job with no description is left unscored rather than guessed at.

The model always answers. Given only a title and a company it scores its own impression
of the employer — and the number that comes back is indistinguishable from one read off
a real posting, so it quietly outranks jobs that earned theirs. "Unscored" is the true
statement about a job nobody has read.
"""
from unittest.mock import patch

from src.paths import MIN_DESCRIPTION_CHARS
from src.scoring.rerank import score_job

PROFILE = {"skills": {"expert": ["Python"]},
           "search": {"role_levels": ["junior"]}}
REAL_POSTING = ("We are hiring a junior Python developer to work on our billing "
                "platform. You will write services, review code and ship to "
                "production with a small team in Toronto.")


class TestNoDescriptionIsNotScored:
    def test_an_empty_description_returns_none(self):
        job = {"title": "Software Engineer", "company": "Shopify", "description": ""}
        assert score_job(job, PROFILE) is None

    def test_a_missing_description_key_returns_none(self):
        assert score_job({"title": "Dev", "company": "X"}, PROFILE) is None

    def test_whitespace_is_not_a_description(self):
        job = {"title": "Dev", "company": "X", "description": "   \n\t  "}
        assert score_job(job, PROFILE) is None

    def test_a_one_line_stub_is_not_a_description(self):
        """Alert emails arrive like this: a link and nothing else."""
        job = {"title": "Dev", "company": "X", "description": "Apply on our website."}
        assert score_job(job, PROFILE) is None

    def test_no_model_is_called_at_all(self):
        """Not scoring means not paying for it either — no request should go out."""
        with patch("src.scoring.rerank._call_chain") as chain, \
             patch("src.scoring.rerank._call_ollama") as ollama:
            score_job({"title": "Dev", "company": "X", "description": ""}, PROFILE)
        chain.assert_not_called()
        ollama.assert_not_called()


class TestARealPostingStillScores:
    def test_a_full_description_is_scored_as_before(self):
        job = {"title": "Junior Python Developer", "company": "Acme",
               "description": REAL_POSTING}
        assert len(REAL_POSTING) >= MIN_DESCRIPTION_CHARS

        fake = type("R", (), {"overall": 74, "skills_score": 8, "seniority_score": 7,
                              "domain_score": 6, "rationale": "fits"})()
        with patch("src.scoring.rerank.scoring_via_chain", return_value=True), \
             patch("src.scoring.rerank._call_chain", return_value=(fake, "gemini")):
            result = score_job(job, PROFILE)
        assert result is not None          # the guard did not swallow a real posting
