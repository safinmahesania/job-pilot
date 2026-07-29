"""Telling English postings from French ones, and both from bilingual.

Montreal's feed mixes all three. A French-only posting is kept and scored — the model
reads French — but flagged, because whether you need French to apply is worth seeing at a
glance, and because an English-tuned fit filter would otherwise mark a good French job
down for saying "ingénieur" instead of "engineer".
"""
from src import language

ENGLISH = ("We are looking for a software developer with experience in Python and SQL. "
           "You will build backend services and work with our team on the platform.")
FRENCH = ("Nous recherchons un développeur logiciel avec de l'expérience en Python. "
          "Vous allez travailler avec notre équipe sur la plateforme de facturation. "
          "Le poste est basé à Montréal et exige des compétences en développement.")
BILINGUAL = ("We are hiring a developer. You will build services with our team on the "
             "platform. Nous recherchons un développeur. Vous allez travailler avec "
             "notre équipe sur la plateforme de facturation à Montréal.")


class TestDetect:
    def test_english_is_en(self):
        assert language.detect(ENGLISH) == "en"

    def test_french_is_fr(self):
        assert language.detect(FRENCH) == "fr"

    def test_both_is_bilingual(self):
        assert language.detect(BILINGUAL) == "bilingual"

    def test_a_french_tech_posting_is_still_french(self):
        """Shared words like Python and API must not tip a French ad into English."""
        text = ("Développeur Python recherché. Compétences requises: expérience avec "
                "les API REST, connaissances en bases de données. Notre entreprise "
                "offre un salaire compétitif et un environnement de travail flexible.")
        assert language.detect(text) == "fr"

    def test_too_short_to_tell_is_unknown(self):
        assert language.detect("Python Developer") == "unknown"

    def test_empty_is_unknown(self):
        assert language.detect("") == "unknown"
        assert language.detect(None) == "unknown"


class TestIsFrenchOnly:
    def test_french_only_is_flagged(self):
        assert language.is_french_only(FRENCH) is True

    def test_bilingual_is_not_french_only(self):
        """A bilingual posting can be applied to in English, so it is not a French gate."""
        assert language.is_french_only(BILINGUAL) is False

    def test_english_is_not_french_only(self):
        assert language.is_french_only(ENGLISH) is False


class TestTheBadges:
    def test_french_and_bilingual_have_badges(self):
        assert language.BADGE["fr"]
        assert language.BADGE["bilingual"]

    def test_english_and_unknown_have_none(self):
        assert "en" not in language.BADGE
        assert "unknown" not in language.BADGE
