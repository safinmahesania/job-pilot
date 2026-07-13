"""Scoring, and the feedback loop that calibrates it.

Two invariants worth guarding:

  * A failed score is None, never a number. The whole app leans on the score being
    a real judgement — a fabricated one would look identical and be worthless.
  * The model's own `overall` is thrown away. It clusters around 65-75 whatever
    you feed it; the composite is what actually separates a match from a
    near-miss. If someone "simplifies" this by trusting the model's number, the
    feed silently turns to mush, and this test is the thing that stops them.
"""
import json

import pytest

from src import store
from src.scoring import feedback, rerank
from src.scoring.rerank import ScoreResult, score_job
from src.paths import (
    SCORE_WEIGHT_SKILLS, SCORE_WEIGHT_SENIORITY, SCORE_WEIGHT_DOMAIN,
    FEEDBACK_MIN_EXAMPLES,
)

JOB = {"title": "Backend Dev", "company": "Shopify",
       "location": "Toronto", "description": "Python, FastAPI"}


def _reply(skills=90, seniority=85, domain=80, overall=70):
    body = json.dumps({"skills_score": skills, "seniority_score": seniority,
                       "domain_score": domain, "overall": overall,
                       "rationale": "Python overlap."})
    return lambda system, user: (body, "cerebras")


class TestScoreJob:
    def test_uses_the_provider_chain(self, conn, monkeypatch, profile,
                                     capture_llm):
        capture_llm.reply = _reply()
        monkeypatch.setattr(rerank, "scoring_via_chain", lambda: True)

        result = score_job(JOB, profile)

        assert result is not None
        assert rerank.get_model_state()["provider"] == "cerebras"

    def test_the_models_own_overall_is_discarded(self, conn, monkeypatch,
                                                 profile, capture_llm):
        """It said 70. The composite of 90/85/80 is nowhere near 70."""
        capture_llm.reply = _reply(skills=90, seniority=85, domain=80, overall=70)
        monkeypatch.setattr(rerank, "scoring_via_chain", lambda: True)

        result = score_job(JOB, profile)

        expected = round(SCORE_WEIGHT_SKILLS * 90
                         + SCORE_WEIGHT_SENIORITY * 85
                         + SCORE_WEIGHT_DOMAIN * 80)
        assert result.overall == expected
        assert result.overall != 70

    def test_a_failure_is_none_not_a_number(self, conn, monkeypatch, profile,
                                            capture_llm):
        """Never invent a score. An unscored job is honest; a fake one isn't."""
        capture_llm.reply = lambda s, u: ("I think it's a good fit!", "gemini")
        monkeypatch.setattr(rerank, "scoring_via_chain", lambda: True)
        monkeypatch.setattr(rerank, "_call_ollama",
                            lambda m, p: (_ for _ in ()).throw(
                                RuntimeError("ollama down")))

        assert score_job(JOB, profile) is None

    def test_it_falls_back_to_ollama_when_the_chain_fails(self, conn, monkeypatch,
                                                          profile, capture_llm):
        """An offline machine must still work."""
        def chain_dies(system, user):
            raise RuntimeError("no providers configured")
        capture_llm.reply = chain_dies

        monkeypatch.setattr(rerank, "scoring_via_chain", lambda: True)
        monkeypatch.setattr(
            rerank, "_call_ollama",
            lambda model, prompt: ScoreResult(skills_score=80, seniority_score=80,
                                              domain_score=80, overall=60,
                                              rationale="local"),
        )

        result = score_job(JOB, profile)
        assert result is not None
        assert rerank.get_model_state()["provider"] == "ollama"

    def test_the_prompt_carries_the_calibration(self, conn, monkeypatch, profile,
                                                capture_llm):
        capture_llm.reply = _reply()
        monkeypatch.setattr(rerank, "scoring_via_chain", lambda: True)

        score_job(JOB, profile, "PAST DECISIONS: dismissed SAP roles")

        assert "PAST DECISIONS" in "\n".join(capture_llm.all_prompts)

    def test_scoring_is_marked_personal(self, conn, monkeypatch, profile,
                                        capture_llm):
        """So that local-only mode actually covers it."""
        capture_llm.reply = _reply()
        monkeypatch.setattr(rerank, "scoring_via_chain", lambda: True)

        score_job(JOB, profile)

        assert any(personal for _, _, personal in capture_llm.calls)


def _add(conn, title, company, status, score=None, location="Toronto"):
    conn.execute(
        "INSERT INTO jobs (dedupe_hash, source, title, company, location, "
        "score, status) VALUES (?,?,?,?,?,?,?)",
        (f"{title}{company}", "test", title, company, location, score, status),
    )
    conn.commit()


class TestFeedback:
    def test_silent_until_there_is_something_to_learn(self, conn):
        """Two dismissals is not a pattern; it's noise to overfit to."""
        for i in range(FEEDBACK_MIN_EXAMPLES - 1):
            _add(conn, f"Job{i}", "Co", "dismissed", 60)

        assert feedback.examples(conn) == ""

    def test_it_shows_what_you_kept_and_what_you_threw_away(self, conn):
        _add(conn, "Backend Developer", "Shopify", "applied", 88)
        _add(conn, "Python Developer", "Wealthsimple", "saved", 82)
        _add(conn, "SAP Consultant", "Deloitte", "dismissed", 55)

        block = feedback.examples(conn)

        assert "Backend Developer" in block
        assert "Python Developer" in block
        assert "SAP Consultant" in block

    def test_it_singles_out_the_scorings_own_mistakes(self, conn):
        """Scored high, dismissed anyway. That's a labelled error, and it's the
        most useful row in the database."""
        _add(conn, "Backend Developer", "Shopify", "applied", 88)
        _add(conn, "Senior Java Architect", "RBC", "dismissed", 84)
        _add(conn, "QA Analyst", "Geotab", "dismissed", 55)

        block = feedback.examples(conn)

        assert "WRONG" in block
        assert "Senior Java Architect" in block.split("WRONG")[1]
        # A low-scored dismissal isn't a mistake — the model already agreed.
        assert "QA Analyst" not in block.split("WRONG")[1]

    def test_stats_report_what_it_has_to_work_with(self, conn):
        _add(conn, "A", "Co", "applied", 88)
        _add(conn, "B", "Co", "saved", 80)
        _add(conn, "C", "Co", "dismissed", 84)

        stats = feedback.stats(conn)

        assert stats["applied"] == 1
        assert stats["saved"] == 1
        assert stats["dismissed"] == 1
        assert stats["high_scored_but_dismissed"] == 1
        assert stats["active"] is True
