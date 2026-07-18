"""Employer job-alert emails (Deloitte, Celestica, Scotiabank, …).

These come from a different family of senders than the aggregators: each posting is a
/job/ link on the company's own careers domain, with the location often appended to the
title. The aggregator parser matched only LinkedIn/Indeed/Glassdoor/ZipRecruiter, so
these emails imported ZERO jobs. Fixtures here mirror the real structure without
carrying anyone's actual mail.
"""
from src.importers import (_company_from_host, _is_readable, _parse_career_site_html,
                           parse_email_file)


def _alert(rows, host="careers.example.com"):
    """Build an alert email body in the shape these senders use."""
    links = "".join(
        f'<tr><td><a href="http://{host}/job/{t.replace(" ", "-")}/{i}/?from=email">'
        f'{t}</a></td></tr>'
        for i, t in enumerate(rows))
    return f"<html><body><table>{links}</table>" \
           f'<a href="http://{host}/unsubscribe/">Click here to unsubscribe.</a>' \
           f"</body></html>"


class TestCareerSiteParsing:
    def test_finds_postings_on_a_company_careers_domain(self):
        html = _alert(["Junior Software Engineer", "Data Analyst"])
        jobs = _parse_career_site_html(html, "notify@example.com", "New jobs")
        assert len(jobs) == 2
        assert {j["title"] for j in jobs} == {"Junior Software Engineer", "Data Analyst"}

    def test_company_comes_from_the_domain(self):
        html = _alert(["Developer"], host="careers.celestica.com")
        jobs = _parse_career_site_html(html, "x@y.com", "")
        assert jobs[0]["company"] == "Celestica"

    def test_location_is_split_off_the_title(self):
        html = _alert(["Senior AI Engineer - Toronto, ON, CA"])
        jobs = _parse_career_site_html(html, "x@y.com", "")
        assert jobs[0]["title"] == "Senior AI Engineer"
        assert jobs[0]["location"] == "Toronto, ON, CA"

    def test_a_dash_in_the_title_is_not_mistaken_for_a_location(self):
        # "Software Engineer - Platform Engineering" has no location in it.
        html = _alert(["Software Engineer - Platform Engineering"])
        jobs = _parse_career_site_html(html, "x@y.com", "")
        assert jobs[0]["title"] == "Software Engineer - Platform Engineering"
        assert jobs[0]["location"] == "Not specified"

    def test_unsubscribe_and_other_non_postings_are_skipped(self):
        jobs = _parse_career_site_html(_alert(["Developer"]), "x@y.com", "")
        assert all("unsubscribe" not in j["apply_url"] for j in jobs)
        assert len(jobs) == 1

    def test_aggregator_links_are_left_to_the_other_parser(self):
        html = ('<a href="https://www.linkedin.com/jobs/view/123">Dev</a>'
                '<a href="https://ca.indeed.com/viewjob?jk=1">Dev</a>')
        assert _parse_career_site_html(html, "x@y.com", "") == []

    def test_parse_email_file_handles_a_career_alert_end_to_end(self):
        raw = ("From: notify@noreply.jobs2web.com\r\n"
               "Subject: New jobs posted\r\n"
               'Content-Type: text/html; charset="utf-8"\r\n\r\n'
               + _alert(["Junior Developer - Toronto, ON, CA"])).encode()
        jobs = parse_email_file(raw, "alert.eml")
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Junior Developer"
        assert jobs[0]["location"] == "Toronto, ON, CA"


class TestCompanyFromHost:
    def test_strips_careers_prefix_and_tld(self):
        assert _company_from_host("http://careers.deloitte.ca/job/x/1/") == "Deloitte"
        assert _company_from_host("http://jobs.scotiabank.com/job/x/1/") == "Scotiabank"

    def test_handles_a_bare_domain(self):
        assert _company_from_host("https://example.com/job/x/1/") == "Example"


class TestReadability:
    def test_company_careers_pages_are_readable(self):
        assert _is_readable("http://careers.deloitte.ca/job/Dev/1/")
        assert _is_readable("http://jobs.scotiabank.com/job/Dev/1/")

    def test_named_ats_platforms_stay_readable(self):
        assert _is_readable("https://boards.greenhouse.io/acme/jobs/1")

    def test_aggregators_are_never_read(self):
        assert not _is_readable("https://www.linkedin.com/jobs/view/1")
        assert not _is_readable("https://ca.indeed.com/viewjob?jk=1")

    def test_a_careers_page_that_is_not_a_posting_is_not_read(self):
        assert not _is_readable("https://careers.example.com/about-us")
