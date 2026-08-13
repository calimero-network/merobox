"""CI's own workflow must not expand an expression into a shell script.

`ci.yml` triggers on `pull_request`, so the checkout is the PR's own tree and
the matrix is built by `ls workflow-examples/*.yml`. A `${{ }}` expanded inside
`run:` is spliced into the script before bash sees it, so a PR adding a file
whose name contains a command substitution would run it on the runner. Passing
the value through `env:` leaves it a string.
"""

import pathlib
import re

import yaml

_CI = pathlib.Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"


def test_no_expression_is_interpolated_into_a_run_block():
    jobs = yaml.safe_load(_CI.read_text())["jobs"]
    offenders = [
        (job_name, step.get("name"), expression)
        for job_name, job in jobs.items()
        for step in job.get("steps") or []
        if isinstance(step.get("run"), str)
        for expression in re.findall(r"\$\{\{[^}]*\}\}", step["run"])
    ]
    assert offenders == []
