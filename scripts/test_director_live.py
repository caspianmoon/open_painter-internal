"""Opt-in real-account Material Director acceptance run, in a disposable Blender profile."""

import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--use-codex-login", action="store_true", required=True,
                    help="Explicitly authorize real inference through the current Codex ChatGPT login")
parser.add_argument("--codex", default=shutil.which("codex"))
parser.add_argument("--blender", default="/Applications/Blender.app/Contents/MacOS/Blender")
parser.add_argument("--output", type=Path, default=ROOT / ".blender-test/director-live")
parser.add_argument("--cases", default="create,less_rust,bottom,explain,fix,plan")
parser.add_argument("--blend", type=Path)
parser.add_argument("--texture-set", help="Choose a texture set in the supplied test copy")
parser.add_argument("--reference", type=Path, help="Reference image sent through the real integration")
parser.add_argument("--frame-model", action="store_true", help="Frame the supplied model instead of the demo camera")
parser.add_argument("--scope", choices=("SET", "SCENE"), default="SET")
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
source = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"
auth = json.loads(source.read_text(encoding="utf-8"))
tokens = auth.get("tokens") or {}
if not tokens.get("access_token") or not tokens.get("id_token"):
    raise SystemExit("The current Codex cache does not contain a ChatGPT login")
# Do not copy or rotate the shared refresh token. The live access token is sufficient.
part = tokens["access_token"].split(".")[1]
claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
if claims.get("exp", 0) - time.time() < 1800:
    raise SystemExit("Refresh the normal Codex login before this live run; less than 30 minutes remain")
with tempfile.TemporaryDirectory(prefix="painter-live-auth-") as directory:
    isolated = dict(auth_mode="chatgpt", OPENAI_API_KEY=None,
                    tokens={k: tokens.get(k, "") for k in ("id_token", "access_token", "account_id")},
                    last_refresh=datetime.now(timezone.utc).isoformat())
    isolated["tokens"]["refresh_token"] = ""
    with os.fdopen(os.open(str(Path(directory) / "auth.json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        json.dump(isolated, handle)
    del auth, tokens, isolated, claims
    env = dict(os.environ, BLENDER_USER_RESOURCES=str(args.output / "blender-profile"),
               OP_DIRECTOR_LIVE_AUTH=directory, OP_DIRECTOR_LIVE_BINARY=args.codex,
               OP_DIRECTOR_LIVE_OUTPUT=str(args.output.resolve()), OP_DIRECTOR_LIVE_CASES=args.cases,
               OP_DIRECTOR_LIVE_SCOPE=args.scope)
    if args.blend:
        env["OP_DIRECTOR_LIVE_BLEND"] = str(args.blend.resolve())
    if args.texture_set:
        env["OP_DIRECTOR_LIVE_SET"] = args.texture_set
    if args.reference:
        env["OP_DIRECTOR_LIVE_REFERENCE"] = str(args.reference.resolve())
    if args.frame_model:
        env["OP_DIRECTOR_LIVE_FRAME_MODEL"] = "1"
    command = [args.blender, "--factory-startup", "--online-mode", "--python", str(ROOT / "tests/manual/material_director_live.py")]
    if os.sys.platform == "darwin":
        command += ["--", "-ApplePersistenceIgnoreState", "YES"]
    result = subprocess.run(command, env=env, cwd=ROOT, timeout=5400 if args.scope == "SCENE" else 2400)
    if result.returncode:
        raise SystemExit(result.returncode)
report = json.loads((args.output / "report.json").read_text(encoding="utf-8"))
print(json.dumps(dict(status=report["status"], cases=[dict(name=c["name"], outcome=c.get("outcome"),
                                                        seconds=c.get("seconds")) for c in report["cases"]]), indent=2))
raise SystemExit(0 if report["status"] == "completed" else 1)
