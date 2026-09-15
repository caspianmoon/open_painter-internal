"""Small bounded JSONL protocol shared by Blender and its local companion."""

import json

VERSION = 1
MAX_MESSAGE = 4 * 1024 * 1024
MAX_BUFFER = MAX_MESSAGE * 2


def encode(message):
    data = json.dumps(message, allow_nan=False, separators=(",", ":")).encode() + b"\n"
    if len(data) > MAX_MESSAGE:
        raise ValueError("Material Director message exceeds 4 MiB")
    return data


class Decoder:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        if len(self.buffer) > MAX_BUFFER:
            raise ValueError("Material Director receive buffer exceeded")

    def pop(self):
        end = self.buffer.find(b"\n")
        if end < 0:
            if len(self.buffer) > MAX_MESSAGE:
                raise ValueError("Material Director frame exceeded")
            return None
        if end > MAX_MESSAGE:
            raise ValueError("Material Director frame exceeded")
        value = json.loads(bytes(self.buffer[:end]))
        del self.buffer[:end + 1]
        if not isinstance(value, dict):
            raise ValueError("Expected a Material Director object")
        return value
