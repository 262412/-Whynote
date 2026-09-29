"""The approved patch stack must exclude an extra untracked frontend route."""

import os
import subprocess
import sys

import pytest
from test_q3133_preflight import host as host
from test_q3133_preflight import patched_source as patched_source


@pytest.mark.parametrize("mode", ["normal", "-O", "environment"])
def test_unapproved_route_rejected_before_build(patched_source, mode):
    source, pin = patched_source
    route = source / "src/routes/unapproved/+page.svelte"
    route.parent.mkdir(parents=True)
    route.write_text("<p>Synthetic unapproved route</p>\n", encoding="utf-8")
    code = (
        "import sys; from pathlib import Path; from qa import m53_live_host as h; "
        "import s1_browser_host as s; h.ROOT=Path(sys.argv[1]); s.UPSTREAM=sys.argv[2]; "
        "h.validate_source(Path(sys.argv[1])/'source')"
    )
    result = subprocess.run(
        [sys.executable, *(["-O"] if mode == "-O" else []), "-c", code, str(source.parent), pin],
        env={**os.environ, "PYTHONOPTIMIZE": "1" if mode == "environment" else "0"},
        capture_output=True,
        timeout=30,
    )
    assert result.returncode != 0, "Q-34: unapproved untracked route passed source validation"
