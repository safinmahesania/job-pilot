# {{NAME}}

{{LOCATION}}
{{EMAIL}} | {{PHONE}} | {{LINKS}}

## Summary

{{SUMMARY}}

## Skills

{{SKILLS}}

## Education

{{EDUCATION}}

## Work Experience

{{EXPERIENCE}}

## Projects

{{PROJECTS}}

## Certificates and Achievements

{{CERTIFICATES}}

## Volunteer and Community Involvement

{{VOLUNTEER}}

<!--
HOW THIS TEMPLATE WORKS
-----------------------
This is your resume's shape. JobPilot fills it in per job: the headings and their
order never change, only the content does. Nothing is invented — every fact comes
from config/profile.yaml.

THE `@@` CONVENTION
-------------------
`@@` splits a line into "left @@ right". The right side is pushed to the right
margin when the resume is rendered:

    ### Software Developer Intern @@ May 2024 - Aug 2024

becomes

    Software Developer Intern                          May 2024 - Aug 2024

This is how the dates sit on the right without a table. Word resumes normally use
a two-column table for it. It looks identical — and Workday and Taleo are
notorious for scrambling tables, because a table cell is not a line of text to a
parser. A right-aligned tab is just text. You get the layout without the risk.

THE SHAPE OF EACH SECTION
-------------------------
Education      ### Degree @@ Sep 2024 - Apr 2026
               University Name, City, Province

Experience     ### Role @@ May 2024 - Aug 2024
               Company Name
               - Bullet: what you did, and what came of it.

Projects       ### Project Name - Personal (Python, FastAPI) @@ github.com/you/repo
               - Point one, up to two lines.
               - Point two, up to two lines.
               - Point three, one line.

Skills         One bullet per category, in the order your profile lists them:
               - **Programming Skills:** Dart | Java | C | Python
               - **Databases:** MySQL | SQL Server | SQLite

               The categories, their names and their order all come from
               `skill_categories` in profile.yaml. They are your headings.

Certificates   - AWS Certified Cloud Practitioner @@ credly.com/badges/...

Volunteer      ### Organization / Role
               Two or three lines on what you did.

A section with nothing in your profile is dropped entirely — an empty
"Certificates" heading is worse than no heading.

LENGTH
------
The resume is kept to one page. These limits are measured against the real
rendered page, not requested politely — see src/paths.py to change them:

  Summary               3 lines
  Experience bullet     2 lines each
  Projects              3, most relevant first
  Project points        3 per project — 2 lines, 2 lines, 1 line
  Volunteer entry       3 lines each

Every project has the same shape: three points, of which two may run to two lines
and the last is a single line — a short closing line, so the entry lands rather
than trailing off.

If the model overruns, JobPilot asks it once to tighten the specific parts that
ran long — and if it still overruns, it says so in the app rather than cutting a
bullet off mid-sentence. You can edit the text before you send it.

Replace this file if you want a different shape. Keep the {{PLACEHOLDER}} names
and the `@@` convention, and the renderer will follow you.
-->
