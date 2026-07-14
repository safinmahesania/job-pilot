"""The length limits, and whether they are actually enforced.

Telling a model "three lines" does not work. It is counting words in its head
against a page it has never seen, and it overshoots — confidently, and in the one
document where running onto a second page costs you something.

So the limits are measured, not requested. These tests exist to make sure the
measurement is real: that it wraps text the way the renderer wraps it, that it
catches a bullet which is one word too long, and — the part that is easy to get
wrong — that it never silently truncates. A resume bullet cut off mid-sentence is
worse than a bullet that runs long, so an overrun that survives a tightening pass
is reported to you, not quietly amputated.
"""
import pytest

from src import resume_limits as limits
from src.paths import (
    RESUME_SUMMARY_LINES,
    RESUME_EXPERIENCE_BULLET_LINES,
    RESUME_PROJECT_BULLET_LINES,
    RESUME_VOLUNTEER_LINES,
    RESUME_PROJECTS_USED,
)


def words(n_chars: int) -> str:
    """Real words, adding up to about `n_chars` — not one long string, which would
    wrap differently and make the test a lie."""
    word = "delivered "
    return (word * (n_chars // len(word) + 1))[:n_chars].strip()


class TestMeasurement:
    def test_short_text_is_one_line(self):
        assert limits.measure_lines("Built a job pipeline.") == 1

    def test_it_wraps_on_words_not_characters(self):
        """len(text) / 110 under-counts every line that ends near a word boundary.
        The renderer wraps on words; so must the measurement."""
        per_line = limits.chars_per_line()

        # A single word that cannot fit on the tail of the first line pushes over.
        text = words(per_line - 5) + " " + "extraordinarily"

        assert limits.measure_lines(text) == 2

    def test_an_indented_bullet_has_less_room(self):
        assert limits.chars_per_line(indented=True) < limits.chars_per_line()

    def test_empty_text_takes_no_lines(self):
        assert limits.measure_lines("") == 0
        assert limits.measure_lines("   ") == 0


class TestSummary:
    def test_a_summary_within_its_limit_passes(self):
        md = f"## Summary\n\n{words(limits.chars_per_line() * RESUME_SUMMARY_LINES - 20)}\n"
        assert limits.check(md) == []

    def test_a_summary_that_runs_over_is_caught(self):
        long = words(limits.chars_per_line() * (RESUME_SUMMARY_LINES + 1))
        md = f"## Summary\n\n{long}\n"

        problems = limits.check(md)

        assert len(problems) == 1
        assert problems[0].where == "Summary"
        assert problems[0].allowed == RESUME_SUMMARY_LINES
        assert problems[0].actual > RESUME_SUMMARY_LINES


class TestExperience:
    def test_each_bullet_is_measured_separately(self):
        """The limit is per bullet. Three short bullets are fine; one long one
        is not, and a section-level check would miss that."""
        per_bullet = limits.chars_per_line(indented=True)
        ok = words(per_bullet - 10)
        too_long = words(per_bullet * (RESUME_EXPERIENCE_BULLET_LINES + 1))

        md = (f"## Work Experience\n\n### Role @@ 2024\nCompany\n"
              f"- {ok}\n- {too_long}\n- {ok}\n")

        problems = limits.check(md)

        assert len(problems) == 1
        assert problems[0].where == "Experience bullet 2"

    def test_bullets_within_the_limit_pass(self):
        per_bullet = limits.chars_per_line(indented=True)
        fits = words(per_bullet * RESUME_EXPERIENCE_BULLET_LINES - 20)

        md = f"## Work Experience\n\n### Role @@ 2024\nCompany\n- {fits}\n"

        assert limits.check(md) == []


class TestProjects:
    """Three projects. Three points each. Two points of two lines, one of one.

    The budget is per bullet, inside a project — it is the same shape for every
    project, not a shrinking allowance across them.
    """

    def _project(self, name, bullets):
        head = f"### {name} @@ github.com/x\n"
        return head + "".join(f"- {b}\n" for b in bullets)

    def test_a_fourth_project_is_caught(self):
        per_bullet = limits.chars_per_line(indented=True)
        short = words(per_bullet - 20)
        md = "## Projects\n\n" + "".join(
            self._project(f"P{i}", [short]) for i in range(4)
        )

        problems = limits.check(md)

        assert any(p.where == "Projects" for p in problems)

    def test_a_fourth_bullet_inside_a_project_is_caught(self):
        """Three points per project. A fourth is padding."""
        per_bullet = limits.chars_per_line(indented=True)
        short = words(per_bullet - 20)

        md = "## Projects\n\n" + self._project("P1", [short] * 4)

        problems = limits.check(md)

        assert any(p.where == "Project 1" and "bullet points" in p.text
                   for p in problems)

    def test_the_third_point_gets_one_line_the_first_two_get_two(self):
        per_bullet = limits.chars_per_line(indented=True)
        two_lines = words(per_bullet * 2 - 20)
        one_line = words(per_bullet - 20)

        # Correct shape: 2 lines, 2 lines, 1 line.
        good = "## Projects\n\n" + self._project(
            "P1", [two_lines, two_lines, one_line])
        assert limits.check(good) == []

        # The third point running to two lines is an overrun.
        bad = "## Projects\n\n" + self._project(
            "P1", [two_lines, two_lines, two_lines])

        problems = limits.check(bad)

        assert len(problems) == 1
        assert problems[0].where == "Project 1, bullet 3"
        assert problems[0].allowed == 1

    def test_the_same_shape_applies_to_every_project(self):
        """Not a shrinking allowance — project 3 gets the same three points as
        project 1."""
        per_bullet = limits.chars_per_line(indented=True)
        two_lines = words(per_bullet * 2 - 20)
        one_line = words(per_bullet - 20)
        shape = [two_lines, two_lines, one_line]

        md = ("## Projects\n\n"
              + self._project("P1", shape)
              + self._project("P2", shape)
              + self._project("P3", shape))

        assert limits.check(md) == []

    def test_each_bullet_is_measured_on_its_own(self):
        """A long first point is not excused by a short third one."""
        per_bullet = limits.chars_per_line(indented=True)
        three_lines = words(per_bullet * 3)
        one_line = words(per_bullet - 20)

        md = "## Projects\n\n" + self._project(
            "P1", [three_lines, one_line, one_line])

        problems = limits.check(md)

        assert len(problems) == 1
        assert problems[0].where == "Project 1, bullet 1"
        assert problems[0].allowed == 2
        assert problems[0].actual > 2


class TestVolunteer:
    def test_a_long_description_is_caught(self):
        long = words(limits.chars_per_line() * (RESUME_VOLUNTEER_LINES + 1))
        md = f"## Volunteer and Community Involvement\n\n### Club / Mentor\n{long}\n"

        problems = limits.check(md)

        assert len(problems) == 1
        assert problems[0].where == "Volunteer entry 1"

    def test_each_entry_is_measured_separately(self):
        short = words(limits.chars_per_line())
        long = words(limits.chars_per_line() * (RESUME_VOLUNTEER_LINES + 1))

        md = (f"## Volunteer and Community Involvement\n\n"
              f"### A / Mentor\n{short}\n\n"
              f"### B / Lead\n{long}\n")

        problems = limits.check(md)

        assert len(problems) == 1
        assert problems[0].where == "Volunteer entry 2"


class TestTheModelIsToldInUnitsItCanCount:
    def test_the_instructions_give_character_budgets(self):
        """A model cannot count rendered lines. It can count characters."""
        text = limits.instructions()
        b = limits.budgets()

        assert str(b["summary_chars"]) in text
        assert str(b["experience_bullet_chars"]) in text
        assert str(b["chars_per_line"]) in text
        for chars in b["project_bullet_chars"]:
            assert str(chars) in text

    def test_the_budgets_come_from_the_real_font(self):
        """Not a guess. If the page or the font size changes, these move."""
        per_line = limits.chars_per_line()
        assert 90 < per_line < 130, (
            f"{per_line} characters per line is implausible for 10.5pt on 7 inches"
        )

    def test_the_instructions_say_what_to_cut(self):
        """"Make it shorter" invites the model to drop the metric — which is the
        one part of the bullet that was doing any work."""
        text = limits.instructions()
        assert "Cut the adjectives" in text


class TestItNeverTruncates:
    def test_check_reports_and_does_not_modify(self):
        """A resume bullet cut off mid-sentence, sent to an employer, is
        unrecoverable. Reporting an overrun is the correct failure."""
        long = words(limits.chars_per_line() * 6)
        md = f"## Summary\n\n{long}\n"

        problems = limits.check(md)

        assert problems                      # it was caught
        assert long in md                    # and nothing was cut
        assert problems[0].text == long


class TestTheBudgetIsACeilingNotAQuota:
    """Telling a model "each project gets exactly 3 bullet points" when the profile
    supplies two is an instruction to invent the third.

    This was a real bug in this file's own prompt. The user's profile had two
    points per project; the prompt demanded three. The only way to satisfy both is
    to make something up — and we had spent the previous three sessions building
    guards against exactly that, while the prompt quietly asked for it.
    """

    def test_the_model_is_told_not_to_pad(self):
        text = limits.instructions()

        assert "CEILINGS, not quotas" in text
        assert "NEVER pad" in text

    def test_the_model_is_not_told_to_write_exactly_three(self):
        text = limits.instructions()

        assert "exactly 3 bullet points" not in text
        assert "AT MOST" in text

    def test_two_bullets_for_a_project_is_not_an_overrun(self):
        """A project with two true points is finished, not short."""
        per_bullet = limits.chars_per_line(indented=True)
        point = words(per_bullet - 20)

        md = ("## Projects\n\n### Plant Disease Detection @@ github.com/x\n"
              f"- {point}\n- {point}\n")

        assert limits.check(md) == []

    def test_a_fourth_bullet_still_is(self):
        """The ceiling is still a ceiling."""
        per_bullet = limits.chars_per_line(indented=True)
        point = words(per_bullet - 20)

        md = ("## Projects\n\n### P @@ x\n" + f"- {point}\n" * 4)

        assert any(p.where == "Project 1" for p in limits.check(md))


class TestTheSummaryHasAFloorAsWellAsACeiling:
    """The one place in this file where a budget is a quota, and it needs its
    reason stated because everywhere else the opposite is true.

    Elsewhere a budget is a ceiling and never a target: demanding three project
    bullets from a profile that supplies two is asking the model to invent the
    third. That reasoning does not carry here. The profile's summary is a paragraph
    the person wrote about themselves, and it typically runs to four or five lines.
    Asking for three lines OF IT is asking for more of what is already there.

    The floor holds only while that is true. Against a one-line profile summary it
    switches off, because at that point it would be the same padding wearing a
    different excuse.
    """

    #: A real profile: a paragraph, real work, real skills. Three lines out of this
    #: is a compression, not an invention.
    RICH = {
        "summary": ("Software developer who enjoys turning ideas into clean, "
                    "practical, and maintainable applications. Experienced across "
                    "frontend and backend development."),
        "experience": [{"role": "Flutter Developer", "company": "Otrack"}],
        "skills": {"expert": ["Flutter", "Java", "Python", "C#", "SQL"]},
    }

    #: A placeholder and nothing else. Three lines out of THIS would be padding.
    THIN = {"summary": "Software developer."}

    def test_a_rich_profile_summary_gets_a_floor(self):
        assert limits.summary_has_material(self.RICH)

    def test_a_thin_one_does_not(self):
        """Three lines out of one line of material is padding, and padding is where
        invention starts."""
        assert not limits.summary_has_material(self.THIN)

    def test_a_short_summary_is_flagged_when_there_was_material(self):
        message = limits.summary_is_short(
            {"summary": "Junior developer."}, self.RICH)

        assert message
        assert "should be about" in message
        assert "Do not invent" in message

    def test_a_short_summary_is_fine_when_there_was_not(self):
        assert limits.summary_is_short({"summary": "Software developer."},
                                       self.THIN) == ""

    def test_a_full_summary_passes(self):
        floor = limits.budgets()["summary_min_chars"]
        full = "word " * (floor // 5 + 4)

        assert limits.summary_is_short({"summary": full}, self.RICH) == ""

    def test_the_floor_is_below_the_ceiling(self):
        """A summary two words short of a full third line is finished, not
        deficient. Chasing the last few characters is how padding gets in."""
        b = limits.budgets()

        assert b["summary_min_chars"] < b["summary_chars"]
        assert b["summary_min_chars"] > b["summary_chars"] * 0.7

    def test_the_instructions_ask_for_a_full_three_lines(self):
        text = limits.instructions(self.RICH)

        assert "aim for a FULL 3 lines" in text
        assert "Do NOT invent anything to reach the length" in text

    def test_and_do_not_when_there_is_nothing_to_fill_it_with(self):
        text = limits.instructions(self.THIN)

        assert "aim for a FULL" not in text
        assert "Do not pad it" in text


class TestTheLimitsMeasureTheFontOnThePage:
    """It measured Times for a while after the page stopped being set in Times.

    The docstring on _char_width_mm said, in as many words, "get this wrong and
    every limit downstream is wrong" — and then the face changed to Calibri and this
    did not follow. Calibri is the narrower of the two, so every limit was quietly
    too strict, and a "three-line summary" was being measured against a page that no
    longer existed.
    """

    def test_it_asks_the_renderer_which_font_rather_than_assuming(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent
                  / "src" / "resume_limits.py").read_text(encoding="utf-8")

        assert 'pdf.set_font("Times"' not in source
        assert "resume_pdf.resolve_font" in source

    def test_the_measurement_is_plausible_for_an_a4_page(self):
        """A sanity range. If this ever reads 40 or 400, something is badly wrong
        and every limit in the file is meaningless."""
        assert 90 < limits.chars_per_line() < 140
