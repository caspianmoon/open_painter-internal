"""Nonblocking, authenticated local IPC. Called only from Blender's main thread."""

import hmac
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from .protocol import Decoder, MAX_BUFFER, VERSION, encode


class Connection:
    def __init__(self, binary, state_directory, *, credentials="keyring"):
        self.state_directory = Path(state_directory).resolve()
        binary = (shutil.which(binary) or binary) if binary else ""
        if binary and not Path(binary).is_file():
            raise ValueError("The custom Material Director runtime is unavailable; clear its Advanced override to use automatic setup")
        # Only the chosen path knows where its launcher's interpreter lives
        # (nvm keeps node beside the codex symlink, not beside the resolved
        # shim); the companion prepends these to its PATH.
        self.binary_dirs = list(dict.fromkeys(str(d) for d in (Path(binary).absolute().parent, Path(binary).resolve().parent))) if binary else []
        self.token = secrets.token_hex(32)
        self.socket = None
        self.authenticated = False
        self.decoder = Decoder()
        self.output = bytearray()
        self.started = time.monotonic()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(2)
        self.listener.setblocking(False)
        self.process = None
        try:
            kwargs = dict(stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.name != "nt":
                kwargs["start_new_session"] = True
            else:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            self.process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("companion.py"))], **kwargs)
            self.process.stdin.write(encode(dict(port=self.listener.getsockname()[1], token=self.token,
                                                binary=str(Path(binary).resolve()) if binary else "", binary_dirs=self.binary_dirs,
                                                state_directory=str(state_directory), credentials=credentials)))
            self.process.stdin.close()
        except Exception:
            self.close()
            raise

    def send(self, message):
        self.output.extend(encode(dict(message, token=self.token)))
        if len(self.output) > MAX_BUFFER:
            raise ValueError("Material Director output queue exceeded")

    def poll(self):
        events = []
        deadline = time.monotonic() + .002
        if not self.authenticated and time.monotonic() - self.started > 15:
            raise ValueError("Material Director companion did not authenticate")
        if self.socket is None:
            try:
                self.socket, _ = self.listener.accept()
                self.socket.setblocking(False)
            except BlockingIOError:
                if time.monotonic() - self.started > 15:
                    raise ValueError("Material Director companion did not connect")
        if self.socket is not None:
            while self.authenticated and self.output:
                try:
                    del self.output[:self.socket.send(self.output[:65536])]
                except BlockingIOError:
                    break
            try:
                data = self.socket.recv(65536)
                if not data:
                    raise ValueError("Material Director companion disconnected")
                self.decoder.feed(data)
            except BlockingIOError:
                pass
            while time.monotonic() < deadline:
                message = self.decoder.pop()
                if message is None:
                    break
                if not self.authenticated:
                    if (message.get("event") != "hello" or message.get("version") != VERSION or
                            not hmac.compare_digest(str(message.get("token", "")), self.token)):
                        # Reject strangers without losing the legitimate helper's listener.
                        self.socket.close()
                        self.socket = None
                        self.decoder = Decoder()
                        break
                    self.authenticated = True
                    self.listener.close()
                else:
                    events.append(message)
        if not events and self.process and self.process.poll() is not None:
            raise ValueError("Material Director companion stopped; reconnect to try again")
        return events

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None
        self.listener.close()
        if self.process and self.process.poll() is None:
            # Kill only the process group created for this companion, including
            # its Codex child. Never touch other editors' Codex processes.
            if os.name != "nt":
                import signal
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
            try:
                self.process.wait(timeout=.2)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    import signal
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    self.process.kill()
                self.process.wait(timeout=.2)
