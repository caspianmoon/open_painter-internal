"""Benchmark process utilities. No Blender imports or inference at import time."""

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
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
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def redact(value):
    if isinstance(value, dict):
        return {k: "<REDACTED>" if k.lower() in SECRET_KEYS else redact(v) for k, v in value.items()}
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
    source = Path(source) if source else Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
    try:
        auth = json.loads(source.read_text())
        tokens = auth.get("tokens") or {}
        access = tokens["access_token"]
        part = access.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        if not tokens.get("id_token") or claims.get("exp", 0) - time.time() < minimum_seconds:
            raise ValueError("expiry")
    except (OSError, ValueError, KeyError, IndexError):
        raise RuntimeError("Current Codex ChatGPT login is unavailable or expires before this session's cap; refresh the normal login and retry an unattempted session") from None
    with tempfile.TemporaryDirectory(prefix="painter-benchmark-auth-") as directory:
        isolated = dict(auth_mode="chatgpt", OPENAI_API_KEY=None,
            tokens={key: tokens.get(key, "") for key in ("id_token", "access_token", "account_id")},
            last_refresh=datetime.now(timezone.utc).isoformat())
        isolated["tokens"]["refresh_token"] = ""
        with os.fdopen(os.open(str(Path(directory) / "auth.json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
            json.dump(isolated, handle)
        del auth, tokens, access, claims, isolated
        yield Path(directory)


def run_blender(command, *, env, timeout, log, heartbeat=None):
    """Bound a whole owned process group; never leave Blender running after timeout."""
    import signal
    started = time.monotonic()
    with Path(log).open("w") as handle:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                   start_new_session=os.name != "nt")
        try:
            while process.poll() is None:
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
