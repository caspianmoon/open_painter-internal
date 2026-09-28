"""Transient GPU inputs. Document identity and pixels never depend on these handles."""
from dataclasses import dataclass

from .formats import texture_bytes


@dataclass(eq=False)
class TextureResource:
    identity: object
    texture: object
    format: str
    mapping: str = 'rgba'
    revision: int = 0

    @property
    def dimensions(self):
        return self.texture.width, self.texture.height

    @property
    def stamp(self):
        return self.identity, self.revision, self.dimensions, self.format, self.mapping

    @property
    def bytes(self):
        return texture_bytes(*self.dimensions, self.format)

    def touch(self):
        self.revision += 1


def binding(node):
    return getattr(node, 'gpu_resource', None)


def decode(expression, mapping):
    if mapping == 'rg':
        return f'vec4(({expression}).rrr, ({expression}).g)'
    if mapping == 'r':
        return f'vec4(({expression}).rrr, 1.0)'
    return expression
