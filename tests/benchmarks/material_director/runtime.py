"""Benchmark process utilities. No Blender imports or inference at import time."""

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
SECRET_KEYS = {"access_token", "refresh_token", "id_token", "token", "authorization", "api_key", "openai_api_key",
               "account_id", "email", "authurl"}
MAX_AUTH_BYTES = 1024 * 1024
MAX_TOKEN_BYTES = 128 * 1024


class AuthRefreshRequired(RuntimeError):
    """Safe control-plane outcome; it does not describe an attempted AI session."""


def _read_access_login(source=None, *, minimum_seconds=1800):
    """Bounded local read. Return only access-only fields, never a refresh token."""
    if not isinstance(minimum_seconds, (int, float)) or isinstance(minimum_seconds, bool) or not math.isfinite(minimum_seconds) or minimum_seconds <= 0:
        raise ValueError("minimum_seconds must be finite and positive")
    source = Path(source) if source else Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
    try:
        with source.open("rb") as handle:
            raw = handle.read(MAX_AUTH_BYTES + 1)
        if len(raw) > MAX_AUTH_BYTES:
            raise ValueError("oversized")
        auth = json.loads(raw)
        tokens = auth.get("tokens") or {}
        access, identity = tokens["access_token"], tokens["id_token"]
        if any(not isinstance(value, str) or not 0 < len(value) <= MAX_TOKEN_BYTES for value in (access, identity)):
            raise ValueError("invalid token")
        parts = access.split(".")
        if len(parts) != 3:
            raise ValueError("invalid access token")
        part = parts[1]
        claims = json.loads(base64.b64decode(part + "=" * (-len(part) % 4), altchars=b"-_", validate=True))
        expiry = claims.get("exp")
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry):
            raise ValueError("invalid expiry")
        remaining = expiry - time.time()
        if remaining < minimum_seconds:
            raise ValueError("expiry")
        account = tokens.get("account_id", "")
        if not isinstance(account, str) or len(account) > MAX_TOKEN_BYTES:
            raise ValueError("invalid account field")
        return {"id_token": identity, "access_token": access, "account_id": account}, remaining
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise AuthRefreshRequired("Current Codex ChatGPT login is unavailable or expires before this session's cap; refresh the normal managed login outside the benchmark, then resume unattempted sessions") from None


def preflight_login(source=None, *, minimum_seconds=1800):
    """Read-only check. No credentials, paths or account identifiers leave this API."""
    tokens, remaining = _read_access_login(source, minimum_seconds=minimum_seconds)
    del tokens
    return {"status": "READY", "minimum_validity_seconds": minimum_seconds,
            "remaining_seconds": int(remaining), "refresh_performed": False}


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def redact(value):
    if isinstance(value, dict):
        return {str(k): "<REDACTED>" if str(k).lower() in SECRET_KEYS else redact(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = value.replace(str(ROOT), "<REPO>").replace(str(Path.home()), "<HOME>")
        value = re.sub(r"(?i)Bearer\s+\S+", "Bearer <REDACTED>", value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "<REDACTED>", value)
        value = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "<EMAIL>", value)
        value = re.sub(r"/(?:private/)?(?:var/folders|tmp)/[^\s\"']+", "<TEMP>", value)
        if value.startswith("data:image/"):
            return "<image omitted; see input manifest>"
    return value


def append_trace(path, direction, value):
    with Path(path).open("a") as handle:
        handle.write(json.dumps(dict(time_utc=datetime.now(timezone.utc).isoformat(),
            monotonic=time.monotonic(), direction=direction, data=redact(value)), allow_nan=False) + "\n")


def source_fingerprint():
    """Hash executed bytes, including dirty edits; Git HEAD alone is insufficient."""
    roots = [ROOT / "src/open_painter", Path(__file__).parent]
    files = [p for base in roots for p in base.rglob("*") if p.is_file()
             and "__pycache__" not in p.parts and "results" not in p.parts
             and p.suffix in {".py", ".md", ".json", ".glsl"}]
    records = {str(p.relative_to(ROOT)): sha256(p) for p in sorted(files)}
    digest = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    return dict(commit=commit, source_sha256=digest, files=records)


@contextmanager
def isolated_login(source=None, *, minimum_seconds=1800):
    """Access-only opt-in account use. Never write or rotate the real refresh token."""
    tokens, _ = _read_access_login(source, minimum_seconds=minimum_seconds)
    with tempfile.TemporaryDirectory(prefix="painter-benchmark-auth-") as directory:
        isolated = dict(auth_mode="chatgpt", OPENAI_API_KEY=None,
            tokens={key: tokens.get(key, "") for key in ("id_token", "access_token", "account_id")},
            last_refresh=datetime.now(timezone.utc).isoformat())
        isolated["tokens"]["refresh_token"] = ""
        with os.fdopen(os.open(str(Path(directory) / "auth.json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
            json.dump(isolated, handle)
        del tokens, isolated
        yield Path(directory)


def run_blender(command, *, env, timeout, log, heartbeat=None, cancel_event=None):
    """Bound a whole owned process group; never leave Blender running after timeout."""
    import signal
    started = time.monotonic()
    with Path(log).open("w") as handle:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                   start_new_session=os.name != "nt")
        try:
            while process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("Owned Blender run cancelled")
                if time.monotonic() - started > timeout:
                    raise TimeoutError("Blender exceeded the predeclared process cap")
                if heartbeat:
                    heartbeat()
                time.sleep(.5)
            return process.returncode
        finally:
            if process.poll() is None:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait(timeout=3)


def save_attachment(output, attachment, *, prefix):
    """Use host-chosen filenames; provider labels never become paths."""
    data = base64.b64decode(attachment["url"].split(",", 1)[1], validate=True)
    digest = hashlib.sha256(data).hexdigest()
    relative = Path("inputs") / f"{prefix}-{digest[:16]}.png"
    destination = Path(output) / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return dict(id=attachment.get("id"), label=attachment.get("label"), path=relative.as_posix(), sha256=digest, bytes=len(data))
