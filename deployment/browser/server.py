#!/usr/bin/env python3
"""Talk to your agent from a browser tab.

    python deployment/browser/server.py

The API key stays in this process; the page only gets 60-second tokens.
"""

import copy
import hashlib
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from lib import (ApiError, aai, load_env, publish_agent, read_agent,  # noqa: E402
                 required, stored_agent_id, _agents_api)


def _key_fingerprint(key: str) -> str:
    """Return first 12 hex chars of SHA-256 of the key, or 'MISSING'."""
    if not key:
        return "MISSING"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def resolve_agent() -> dict:
    """A published id means the agent is managed elsewhere, so use it as it is."""
    name = os.environ.get("AGENT", "minimal")
    agent_id_env = os.environ.get("AGENT_ID", "")
    agent_id_key_val = os.environ.get(f"AGENT_ID_{name.upper().replace('-', '_')}", "")
    known = stored_agent_id(name)
    api_base = _agents_api()
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "")
    key_fp = _key_fingerprint(api_key)

    print(f"[diag] AGENT={name}")
    print(f"[diag] AGENT_ID env={agent_id_env}")
    print(f"[diag] AGENT_ID_{name.upper().replace('-', '_')} env={agent_id_key_val}")
    print(f"[diag] stored_agent_id() -> {known}")
    print(f"[diag] API base={api_base}")
    print(f"[diag] API key present={'yes' if api_key else 'no'}, fp={key_fp}")

    if known:
        try:
            print(f"[diag] Loading agent via GET {api_base}/agents/{known}")
            agent = aai(f"/agents/{known}")
            print(f"[diag] Agent load OK: {agent.get('name')}")
        except ApiError as err:
            print(f"[diag] Agent load FAILED: status={err.status}, body={err.args[0] if err.args else 'unknown'}")
            sys.exit(f"Could not load agent {known}: {err}")
        return {"id": known, "name": agent.get("name") or "Your agent"}
    agent = read_agent(name)
    try:
        result = publish_agent(agent, name=name, reuse_by_name=True)
    except ApiError as err:
        sys.exit(f"Could not publish agents/{name}.jsonc: {err}")
    verb = "Created" if result["created"] else "Updated"
    print(f'{verb} "{agent["name"]}" from agents/{name}.jsonc')
    return {"id": result["id"], "name": agent["name"]}


def public_agent(agent: dict) -> dict:
    """Read-only view of the stored agent. The API keeps header values and llm
    keys write-only; these deletes hold even if that changes. The system prompt
    is in here, so a public deployment shows it to anyone who opens the page."""
    copied = copy.deepcopy(agent)
    for tool in copied.get("tools", []):
        for header in tool.get("http", {}).get("headers", []):
            header["value"] = "<hidden>"
    for llm in copied.get("llm", []):
        llm.pop("api_key", None)
    return copied


AGENT = None
PAGE = ""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/token":
            try:
                token = aai("/token?product=voice_agent&expires_in_seconds=60")
                self._send(200, json.dumps(token).encode(), "application/json")
            except ApiError as err:
                print(err)
                self._send(502, b'{"error":"token request failed"}', "application/json")
            return
        if path == "/agent":
            try:
                agent = aai(f"/agents/{AGENT['id']}")
                self._send(200, json.dumps(public_agent(agent)).encode(), "application/json")
            except ApiError as err:
                print(err)
                self._send(502, b'{"error":"could not load the agent"}', "application/json")
            return
        if path == "/app.js":
            self._send(200, (HERE / "app.js").read_bytes(), "text/javascript")
            return
        self._send(200, PAGE.encode(), "text/html")

    def log_message(self, *args) -> None:  # quiet; errors are printed above
        pass


def main() -> None:
    global AGENT, PAGE
    load_env()
    required("ASSEMBLYAI_API_KEY", "get one at https://www.assemblyai.com/dashboard/api-keys")

    AGENT = resolve_agent()
    print(f"Agent: {AGENT['id']}")
    PAGE = ((HERE / "index.html").read_text()
            .replace("{{AGENT_NAME}}", AGENT["name"])
            .replace("{{AGENT_JSON}}", json.dumps(AGENT).replace("<", "\\u003c")))

    # PORT when set, otherwise 3000 and up until one is free.
    fixed = os.environ.get("PORT")
    port = int(fixed) if fixed else 3000
    while True:
        try:
            server = ThreadingHTTPServer(("", port), Handler)
            break
        except OSError:
            if fixed or port >= 3010:
                raise
            port += 1

    print(f"Talk to it: http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()