"""Run explicitly with a patched, pinned checkout and a dedicated Python environment."""

import asyncio
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

UPSTREAM_SHA = "8bd8b4fac5e059578ac0c74b3c18d11139f88b7d"


@pytest.fixture(scope="session")
def native(tmp_path_factory):
    source = Path(os.environ["WHYNOTE_OPENWEBUI_SOURCE"]).resolve()
    assert subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip() == UPSTREAM_SHA
    patch = Path(__file__).parents[1] / "patches/native-v0.11.4-s0.patch"
    subprocess.run(["git", "-C", str(source), "apply", "--reverse", "--check", str(patch)], check=True)
    data = tmp_path_factory.mktemp("native-synthetic")
    os.environ.update(
        DATA_DIR=str(data),
        STATIC_DIR=str(data / "static"),
        DATABASE_URL=f"sqlite:///{(data / 'webui.db').as_posix()}",
        WEBUI_SECRET_KEY="synthetic-test-only-not-a-deployment-secret",
        OFFLINE_MODE="true",
        HF_HUB_OFFLINE="1",
        USE_SLIM_DOCKER="true",
        ENABLE_OLLAMA_API="false",
        ENABLE_OPENAI_API="false",
        ENABLE_VERSION_UPDATE_CHECK="false",
        ENABLE_ADMIN_EXPORT="false",
        ENABLE_ADMIN_CHAT_ACCESS="false",
        ENABLE_PERSISTENT_CONFIG="false",
        DATABASE_ENABLE_SQLITE_WAL="false",
        CORS_ALLOW_ORIGIN="http://127.0.0.1:8089",
    )
    sys.path.insert(0, str(source / "backend"))
    from open_webui.internal import db
    from open_webui.models import chats, feedbacks, groups, shared_chats, users
    from open_webui.routers import evaluations

    for module in (db, chats, feedbacks, users, evaluations, groups, shared_chats):
        assert Path(module.__file__).resolve().is_relative_to(source / "backend")
    db.Base.metadata.create_all(db.engine)
    print(f"upstream={UPSTREAM_SHA}; source={source}; patch_sha256={hashlib.sha256(patch.read_bytes()).hexdigest()}")
    yield SimpleNamespace(db=db, chats=chats, feedbacks=feedbacks, users=users, evaluations=evaluations, data=data)
    asyncio.run(db.async_engine.dispose())
    db.engine.dispose()
