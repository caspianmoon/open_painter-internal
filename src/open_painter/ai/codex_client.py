"""Codex app-server adapter. This module runs only in the companion process."""

import json
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from ..constants import VERSION

try:
    from .protocol import encode
    from . import generated_images
except ImportError:  # Bundled companion launched as a standalone script.
    from protocol import encode
    import generated_images

# Runtime-specific protocol verified against the generated 0.154 schema. New
# minors must pass the local capability probe before extending this range.
RUNTIME_MINOR = 154
DISABLED = ("shell_tool", "unified_exec", "shell_snapshot", "multi_agent", "goals",
            "apps", "plugins", "remote_plugin", "hooks", "memories",
            "browser_use", "browser_use_external", "computer_use", "js_repl", "skill_search",
            "view_image", "sleep_tool", "tool_suggest", "skill_mcp_dependency_install")
CONFIG = {**{f"features.{name}": False for name in DISABLED},
          "features.code_mode.enabled": False, "features.skip_host_skill_discovery": True,
          "features.image_generation": True,
          # Summaries (never raw reasoning) let the artist follow a long turn.
          "model_reasoning_summary": "auto",
          "web_search": "disabled",
          "analytics.enabled": False,
          "cli_auth_credentials_store": "keyring", "approval_policy": "never",
          "sandbox_mode": "read-only", "mcp_servers": {}}


SAFE_ENVIRONMENT = {"PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT",
                    "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "LANG", "LC_ALL",
                    "SSL_CERT_FILE", "CODEX_CA_CERTIFICATE"}


def launch_environment(state_directory, binary_dirs=()):
    env = {k: v for k, v in os.environ.items() if k in SAFE_ENVIRONMENT}
    # npm's codex is a "#!/usr/bin/env node" shim. A Dock-launched Blender
    # inherits launchd's PATH, so node is only reachable beside the executable
    # the artist chose; the Windows codex.cmd shim needs node.exe the same way.
    entries = [*binary_dirs, *env.get("PATH", "").split(os.pathsep)]
    env["PATH"] = os.pathsep.join(dict.fromkeys(d for d in entries if d))
    env["CODEX_HOME"] = str(state_directory)
    return env


def launch_failure(stderr):
    # "env: node: No such file or directory" explains a wrong PATH far better
    # than asking for the executable again. Keep the hint short and path-free.
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    hint = " ".join(w for w in lines[-1].split() if "/" not in w and "\\" not in w)[:120] if lines else ""
    if hint:
        return f"Material Director could not start its runtime ({hint}). Reconnect to retry; clear any custom runtime override to use automatic setup"
    return "Material Director could not start its runtime; reconnect or clear the custom runtime override to use automatic setup"


def runtime_version(binary, *, env=None, cwd=None):
    result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10, env=env, cwd=cwd)
    match = re.search(r"codex-cli (\d+)\.(\d+)\.([^\s]+)", result.stdout)
    if result.returncode:
        raise ValueError(launch_failure(result.stderr))
    if not match:
        raise ValueError("The custom runtime is not compatible with Material Director; clear the Advanced override to use automatic setup")
    if (int(match[1]), int(match[2])) != (0, RUNTIME_MINOR):
        raise ValueError(f"This Material Director build supports Codex 0.{RUNTIME_MINOR}.x; found {match[0]}")
    return match[0]


def launch_arguments(binary, overrides=None):
    options = dict(CONFIG, **(overrides or {}))
    args = [binary, "app-server", "--stdio", "--strict-config"]
    for name, value in options.items():
        args += ["-c", name + "=" + json.dumps(value)]
    return args


class CodexClient:
    def __init__(self, binary, state_directory, emit, *, overrides=None, credentials="keyring", binary_dirs=()):
        state = Path(state_directory).resolve()
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        workspace = state / "workspace"
        workspace.mkdir(exist_ok=True, mode=0o700)
        self.workspace = str(workspace)
        self.state_directory = state
        self.emit = emit
        self.events = queue.Queue(maxsize=256)
        self.pending = {}
        self.serial = 0
        self.initialized = False
        self.thread_id = None
        self.turn_id = None
        self.request_id = None
        self.account = None
        self.models = []
        self.login_id = None
        self.closed = False
        self.deadline = 0
        self.allowed_tools = set()
        self.streamed_messages = set()
        self.image_items = set()
        self.streamed_reasoning = set()
        self.commentary = set()
        self.generate_textures = False
        safe_env = launch_environment(state, binary_dirs)
        self.version = runtime_version(binary, env=safe_env, cwd=workspace)
        options = dict(overrides or {})
        options["cli_auth_credentials_store"] = credentials
        self.config = dict(CONFIG, **options)
        self.process = subprocess.Popen(launch_arguments(binary, options), cwd=workspace, env=safe_env,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.request("initialize", dict(clientInfo=dict(name="open_painter", title="Open Painter", version=VERSION),
                     capabilities=dict(experimentalApi=True)), self._initialized)

    def _read(self):
        try:
            while not self.closed:
                line = self.process.stdout.readline(generated_images.MAX_SERVER_MESSAGE + 1)
                if not line:
                    break
                if len(line) > generated_images.MAX_SERVER_MESSAGE:
                    raise ValueError("Codex response exceeded the message limit")
                self.events.put(json.loads(line), timeout=2)
        except Exception:
            try:
                self.events.put(dict(local_error="Codex connection ended or returned invalid data"), timeout=2)
            except queue.Full:
                pass  # The consumer's bounded turn timeout will close the process.

    def send(self, value):
        self.process.stdin.write(encode(value))
        self.process.stdin.flush()

    def request(self, method, params, callback=None):
        self.serial += 1
        self.pending[self.serial] = (method, callback, time.monotonic(), self.request_id)
        self.send(dict(id=self.serial, method=method, params=params))

    def _initialized(self, result):
        self.initialized = True
        self.send(dict(method="initialized", params={}))
        self.emit(dict(event="connected", runtime=self.version))
        self.request("account/read", dict(refreshToken=False), self._account)
        self.request("model/list", dict(includeHidden=False), self._models)

    def _account(self, result):
        self.account = result.get("account")
        account = self.account or {}
        self.emit(dict(event="account", connected=bool(self.account),
                       auth_type=account.get("type", ""), plan=account.get("planType", "")))
        if account.get("type") == "chatgpt":
            self.request("account/rateLimits/read", {}, lambda r: self.emit(dict(event="limits", limits=r.get("rateLimits", {}))))

    def _models(self, result):
        self.models = [{"id": m.get("model", m.get("id", "")), "name": m.get("displayName", m.get("model", "")),
                        "default": m.get("isDefault", False), "input_modalities": m.get("inputModalities", [])}
                       for m in result.get("data", [])]
        self.emit(dict(event="models", models=self.models))

    def login(self):
        if not self.initialized:
            raise ValueError("Wait for Codex to connect")
        self.request("account/login/start", dict(type="chatgpt"), self._login)

    def _login(self, result):
        self.login_id = result.get("loginId")
        self.emit(dict(event="login", url=result.get("authUrl", "")))

    def cancel_login(self):
        if self.login_id:
            self.request("account/login/cancel", dict(loginId=self.login_id))
            self.login_id = None

    def logout(self):
        self.interrupt()
        self.request("account/logout", {}, lambda _: self._account(dict(account=None)))

    def run(self, message):
        if not self.account:
            raise ValueError("Sign in with ChatGPT before running Material Director")
        if self.request_id:
            raise ValueError("A Material Director instruction is already running")
        self.request_id = message["request_id"]
        self.streamed_messages = set()
        self.image_items = set()
        self.streamed_reasoning = set()
        self.commentary = set()
        self.generate_textures = message.get("generate_textures", True)
        request_id = self.request_id
        self.thread_id = self.turn_id = None
        self.deadline = time.monotonic() + (1800 if message.get("scene_scope") else 900 if self.generate_textures else 180)
        time_limit = round(self.deadline - time.monotonic())
        self.allowed_tools = {tool["name"] for tool in message["tools"]}
        params = dict(cwd=self.workspace, approvalPolicy="never", sandbox="read-only",
                      environments=[], ephemeral=True, dynamicTools=message["tools"],
                      baseInstructions=message["instructions"],
                      config=dict(self.config, **{"features.image_generation": message.get("generate_textures", True)}))
        if message.get("model"):
            params["model"] = message["model"]

        def started(result):
            if self.request_id != request_id:
                return
            # Never carry an unattended thread across file loads or reconnects.
            self.thread_id = result["thread"]["id"]
            # Public runtime metadata lets acceptance runs distinguish the
            # requested model from the model actually selected by the server.
            # Deliberately omit paths, instructions and account information.
            self.emit(dict(event="turn_metadata", request_id=request_id,
                           model=result.get("model"), provider=result.get("modelProvider"),
                           reasoning_effort=result.get("reasoningEffort"),
                           service_tier=result.get("serviceTier"), time_limit=time_limit))
            inputs = [dict(type="text", text=message["input"])]
            if message.get("image"):
                inputs.append(dict(type="image", url=message["image"]))
            for attachment in message.get("images", []):
                inputs.append(dict(type="text", text="VISUAL INPUT " + attachment["id"] + ": " + attachment["label"]))
                inputs.append(dict(type="image", url=attachment["url"]))
            thread_id = self.thread_id

            def turn_started(result):
                turn_id = result["turn"]["id"]
                if self.request_id != request_id:
                    self.request("turn/interrupt", dict(threadId=thread_id, turnId=turn_id))
                else:
                    self.turn_id = turn_id

            self.request("turn/start", dict(threadId=self.thread_id, input=inputs,
                         environments=[], sandboxPolicy=dict(type="readOnly", networkAccess=False)),
                         turn_started)

        self.request("thread/start", params, started)

    def interrupt(self):
        if self.thread_id and self.turn_id:
            self.request("turn/interrupt", dict(threadId=self.thread_id, turnId=self.turn_id))
        self.request_id = None

    def tool_result(self, message):
        content = [dict(type="inputText", text=json.dumps(message.get("result", {}), allow_nan=False))]
        for attachment in message.get("images", []):
            content.append(dict(type="inputText", text="VISUAL INPUT " + attachment["id"] + ": " + attachment["label"]))
            content.append(dict(type="inputImage", imageUrl=attachment["url"]))
        self.send(dict(id=message["call_id"], result=dict(success=message.get("success", True),
                       contentItems=content)))

    def poll(self):
        if self.process.poll() is not None:
            raise ValueError("Codex stopped. Check the runtime version and reconnect.")
        for _ in range(64):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if "local_error" in event:
                raise ValueError(event["local_error"])
            if "id" in event and "method" not in event:
                method, callback, _, owner = self.pending.pop(event["id"], ("", None, 0, None))
                if "error" in event:
                    # A failed start or interrupt of an earlier instruction must
                    # not cancel the one that replaced it.
                    if method in {"thread/start", "turn/start", "turn/interrupt"} and owner != self.request_id:
                        continue
                    # Server messages can contain private filesystem paths. Keep
                    # the product error useful without forwarding raw diagnostics.
                    error = dict(event="error",
                                 message=f"Codex {method} failed ({event['error'].get('code', 'error')}). Reconnect or check account access.")
                    if method in {"thread/start", "turn/start"}:
                        # Only a failed start ends the instruction; an account or
                        # model request failing on its own must not cancel a turn
                        # Blender would then believe has stopped.
                        error["request_id"] = self.request_id
                        self.request_id = None
                    self.emit(error)
                elif callback:
                    callback(event.get("result", {}))
                continue
            method, params = event.get("method", ""), event.get("params", {})
            if method == "item/tool/call":
                if not self.request_id or params.get("threadId") != self.thread_id or params.get("tool") not in self.allowed_tools:
                    self.send(dict(id=event["id"], result=dict(success=False, contentItems=[dict(type="inputText", text="Tool outside active Painter scope")])) )
                else:
                    self.emit(dict(event="tool", call_id=event["id"], request_id=self.request_id,
                                   tool=params["tool"], arguments=params["arguments"]))
            elif "id" in event:
                self.send(dict(id=event["id"], error=dict(code=-32601, message="Material Director does not grant this capability")))
            elif method == "account/login/completed":
                self.login_id = None
                self.request("account/read", dict(refreshToken=False), self._account)
                if not params.get("success"):
                    self.emit(dict(event="error", message="Sign-in was cancelled or failed. Try connecting again."))
            elif method == "account/updated":
                self.request("account/read", dict(refreshToken=False), self._account)
            elif method == "account/rateLimits/updated":
                self.emit(dict(event="limits", limits=params.get("rateLimits", {})))
            elif method == "model/rerouted" and self.request_id and params.get("threadId") == self.thread_id:
                self.emit(dict(event="model_rerouted", request_id=self.request_id,
                               from_model=params.get("fromModel"), to_model=params.get("toModel"),
                               reason=params.get("reason")))
            elif method == "thread/tokenUsage/updated" and self.request_id and params.get("threadId") == self.thread_id:
                usage = params.get("tokenUsage", {})
                counts = {group: {key: value for key, value in usage.get(group, {}).items()
                                  if isinstance(value, int) and not isinstance(value, bool) and value >= 0}
                          for group in ("last", "total") if isinstance(usage.get(group), dict)}
                self.emit(dict(event="usage", request_id=self.request_id, usage=counts))
            elif method == "item/agentMessage/delta" and self.request_id and params.get("threadId") == self.thread_id:
                self.streamed_messages.add(params.get("itemId"))
                # Mid-turn narration ("I'll inspect the regions first") is progress, not the reply.
                if params.get("itemId") in self.commentary:
                    self.emit(dict(event="commentary", request_id=self.request_id, item_id=str(params["itemId"]),
                                   text=params.get("delta", "")[:16000]))
                else:
                    self.emit(dict(event="text", request_id=self.request_id, text=params.get("delta", "")[:16000]))
            elif method == "item/reasoning/summaryTextDelta" and self.request_id and params.get("threadId") == self.thread_id:
                self.streamed_reasoning.add(params.get("itemId"))
                self.emit(dict(event="thinking", request_id=self.request_id, item_id=str(params.get("itemId", "")),
                               part=int(params.get("summaryIndex", 0)), text=str(params.get("delta", ""))[:4000]))
            elif method == "item/completed" and self.request_id and params.get("threadId") == self.thread_id:
                item = params.get("item", {})
                if item.get("type") == "reasoning" and item.get("id") not in self.streamed_reasoning:
                    # Some runtimes deliver the summary only with the finished item.
                    for index, text in enumerate(item.get("summary") or []):
                        if isinstance(text, str) and text.strip():
                            self.emit(dict(event="thinking", request_id=self.request_id, item_id=str(item.get("id", "")),
                                           part=index, text=text[:4000]))
                if item.get("type") == "imageGeneration" and item.get("id") in self.image_items:
                    if item.get("status") == "completed" and not item.get("failure"):
                        metadata = generated_images.store(self.state_directory, item)
                        self.emit(dict(event="generated_image", request_id=self.request_id, **metadata))
                    else:
                        self.emit(dict(event="image_status", request_id=self.request_id,
                                       message="Texture generation failed. Codex can retry or explain the failure."))
                # Per message: a streamed note must not swallow an answer that arrives whole.
                if item.get("type") == "agentMessage" and item.get("id") not in self.streamed_messages:
                    if item.get("phase") == "commentary":
                        self.emit(dict(event="commentary", request_id=self.request_id, item_id=str(item.get("id", "")),
                                       text=item.get("text", "")[:16000]))
                    else:
                        self.emit(dict(event="text", request_id=self.request_id, text=item.get("text", "")[:16000]))
            elif method == "turn/completed" and params.get("threadId") == self.thread_id:
                turn = params.get("turn", {})
                error = turn.get("error")
                # Keep the provider's terminal cause; previously it was lost
                # before the host could archive a failed material attempt.
                safe_error = None
                if isinstance(error, dict):
                    safe_error = {key: str(error[key])[:2000] for key in ("message", "codexErrorInfo", "additionalDetails")
                                  if error.get(key) is not None}
                self.emit(dict(event="completed", request_id=self.request_id, status=turn.get("status", "failed"),
                               **({"error": safe_error} if safe_error else {})))
                self.request_id = self.turn_id = None
            elif method == "item/started":
                item = params.get("item", {})
                # The phase arrives with the started item, before any of its text.
                if (item.get("type") == "agentMessage" and item.get("phase") == "commentary"
                        and self.request_id and params.get("threadId") == self.thread_id):
                    self.commentary.add(item.get("id"))
                if item.get("type") == "imageGeneration" and self.request_id and params.get("threadId") == self.thread_id:
                    if not self.generate_textures:
                        self.interrupt()
                        raise ValueError("Image generation was disabled for this instruction")
                    if len(self.image_items) >= generated_images.MAX_IMAGES:
                        # The budget is a prompt-level hint the model can overrun;
                        # dropping the turn here would discard the finished
                        # textures, and interrupt() would strand the draft by
                        # clearing request_id before turn/completed arrives.
                        self.emit(dict(event="image_status", request_id=self.request_id,
                                       message=f"Ignored an extra generated texture ({generated_images.MAX_IMAGES} per instruction)"))
                        continue
                    self.image_items.add(item["id"])
                    self.emit(dict(event="image_status", request_id=self.request_id,
                                   message=f"Generating texture {len(self.image_items)}… This can take a few minutes."))
                if item.get("type") in {"commandExecution", "fileChange", "webSearch", "mcpToolCall", "collabAgentToolCall"}:
                    self.interrupt()
                    raise ValueError("Codex requested a capability outside Material Director; connection stopped")
        now = time.monotonic()
        if self.request_id and now > self.deadline:
            request_id = self.request_id
            self.interrupt()
            self.emit(dict(event="error", request_id=request_id, message="The instruction timed out. Your material was not changed."))
        expired = [key for key, (_, _, started, _) in self.pending.items() if now - started > 30]
        if expired:
            raise ValueError("Codex connection timed out; reconnect to try again")

    def close(self):
        self.closed = True
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()
        self.reader.join(timeout=1)
