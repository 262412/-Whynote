"""
title: 知因 S0 固定虚构回答
author: Whynote
version: 0.1.0
required_open_webui_version: 0.11.4
"""

import json
import os
from pathlib import Path


class Pipe:
    name = "知因 S0 固定虚构回答"

    def __init__(self):
        self.fixture = json.loads(Path(os.environ["WHYNOTE_S0_FIXTURE"]).read_text(encoding="utf-8"))
        if self.fixture.get("synthetic") is not True:
            raise ValueError("S0 fixture is required")

    async def pipe(self, body: dict) -> str:
        messages = body.get("messages") or []
        last = messages[-1] if messages else {}
        if last.get("role") != "user" or last.get("content") != self.fixture["prompt"]:
            return "S0 只接受固定虚构问题。"
        return self.fixture["response"]
