"""Start a new synthetic-only Open WebUI HTTP QA server on 127.0.0.1:8092.

Run with an Open WebUI 0.11.4 Python environment. All paths are explicit;
the data directory must not exist. No frontend or S0 Functions are installed.
"""

import argparse
import os
import secrets
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8092)
    args = parser.parse_args()
    source, data = args.source.resolve(), args.data_dir.resolve()
    assert (source / "backend/open_webui/main.py").is_file()
    data.mkdir(parents=True, exist_ok=False)
    (data / "static").mkdir()
    sys.path.insert(0, str(source / "backend"))
    os.environ.update(
        DATA_DIR=str(data),
        STATIC_DIR=str(data / "static"),
        DATABASE_URL="sqlite:///" + (data / "webui.db").as_posix(),
        WEBUI_SECRET_KEY=secrets.token_hex(32),
        FRONTEND_BUILD_DIR=str(data / "no-frontend"),
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
        CORS_ALLOW_ORIGIN=f"http://127.0.0.1:{args.port}",
        USER_PERMISSIONS_CHAT_RATE_RESPONSE="true",
        ENABLE_SIGNUP="true",
        DEFAULT_USER_ROLE="user",
    )
    import uvicorn

    uvicorn.run("open_webui.main:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
