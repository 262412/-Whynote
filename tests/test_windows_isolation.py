"""Actual Windows OS probes with synthetic text only; Linux CI skips these."""

import json
import sys
from pathlib import Path

import pytest

from whynote.isolation_probe import probe_network
from whynote.replay_laya import ReplayError
from whynote.windows_isolation import isolated_profile

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows AppContainer")


def roots():
    import whynote

    return [Path(sys.prefix), Path(sys.base_prefix), Path(whynote.__file__).parent.parent]


def test_actual_zero_capabilities_and_receiver_controls(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with isolated_profile(roots(), scratch) as sandbox:
        receipt = probe_network(sandbox, sys.executable)
        assert receipt["passed"] and all(receipt["positive_delivery"].values())
        assert not any(receipt["sandbox_delivery"].values())
        assert sandbox.cleanup_verified


def test_timeout_kills_job_and_next_process_works(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with isolated_profile(roots(), scratch) as sandbox:
        code = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)']);time.sleep(60)"
        with pytest.raises(ReplayError, match="timeout"):
            sandbox.run([sys.executable, "-I", "-B", "-c", code], b"", timeout=1)
        assert sandbox.cleanup_verified
        raw, _ = sandbox.run([sys.executable, "-I", "-B", "-c", "print(42)"], b"")
        assert raw.strip() == b"42" and sandbox.cleanup_verified


def test_worker_cannot_read_sibling_material_or_write_scratch(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    private = tmp_path / "private.txt"
    private.write_text("SYNTHETIC PRIVATE SENTINEL")
    code = """import sys,json
result={}
for name,path,mode in [('read',sys.argv[1],'r'),('write','new.txt','w')]:
 try:
  with open(path,mode) as stream: pass
  result[name]=True
 except OSError: result[name]=False
print(json.dumps(result))
"""
    with isolated_profile(roots(), scratch) as sandbox:
        raw, _ = sandbox.run([sys.executable, "-I", "-B", "-c", code, str(private)], b"")
        assert json.loads(raw) == {"read": False, "write": False}
    assert not list(scratch.iterdir())
