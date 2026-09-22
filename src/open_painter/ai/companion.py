"""Standalone local companion. Only this process owns network/SDK worker threads."""

import hmac
import json
import select
import socket
import sys

from codex_client import CodexClient
from protocol import Decoder, MAX_BUFFER, VERSION, encode


def main():
    # The capability token travels through an inherited pipe, never argv/logs.
    config = json.loads(sys.stdin.buffer.readline(16384))
    connection = socket.create_connection(("127.0.0.1", int(config["port"])), timeout=10)
    connection.sendall(encode(dict(event="hello", token=config["token"], version=VERSION)))
    connection.settimeout(2)
    decoder = Decoder()
    outgoing = bytearray()
    client = None

    def emit(message):
        # Queue instead of blocking: Blender drains this socket from a timer, so
        # a stalled main thread (bake, shader compile) must not end the session.
        outgoing.extend(encode(message))
        if len(outgoing) > MAX_BUFFER:
            raise ValueError("Material Director companion output queue exceeded")

    try:
        binary = config.get("binary", "")
        if not binary:
            import runtime
            # Setup runs before the client loop. Publish progress immediately so
            # Blender stays responsive and can cancel the owned process group.
            binary = runtime.ensure(config["state_directory"], lambda text: connection.sendall(
                encode(dict(event="runtime_status", message=text))))
        client = CodexClient(binary, config["state_directory"], emit,
                             credentials=config.get("credentials", "keyring"),
                             binary_dirs=config.get("binary_dirs", []))
        while True:
            readable, writable, _ = select.select([connection], [connection] if outgoing else [], [], .025)
            if writable:
                del outgoing[:connection.send(outgoing[:65536])]
            if readable:
                data = connection.recv(65536)
                if not data:
                    break
                decoder.feed(data)
            for _ in range(32):
                message = decoder.pop()
                if message is None:
                    break
                if not hmac.compare_digest(str(message.pop("token", "")), config["token"]):
                    raise ValueError("Invalid local session")
                action = message.get("action")
                if action == "close":
                    return
                if action == "login":
                    client.login()
                elif action == "cancel_login":
                    client.cancel_login()
                elif action == "logout":
                    client.logout()
                elif action == "run":
                    client.run(message)
                elif action == "interrupt":
                    client.interrupt()
                elif action == "tool_result":
                    client.tool_result(message)
                else:
                    raise ValueError("Unsupported companion action")
            client.poll()
    except Exception as exc:
        try:
            # Local errors are bounded; provider responses are sanitized by the adapter.
            outgoing.extend(encode(dict(event="error", message=str(exc)[:500], fatal=True)))
            connection.sendall(outgoing)
        except (OSError, ValueError):
            pass
    finally:
        if client:
            client.close()
        connection.close()


if __name__ == "__main__":
    main()
