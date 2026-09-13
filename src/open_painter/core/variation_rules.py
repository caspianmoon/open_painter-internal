"""Deterministic bounded artistic variations, one set of baked outputs per asset."""
import hashlib


def factor(seed,semantic,name,amount=.1):
    if not 0<=amount<=.5:
        raise ValueError('Variation must be between 0 and 0.5')
    digest=hashlib.sha256(f'1|{seed}|{semantic}|{name}'.encode()).digest()
    unit=int.from_bytes(digest[:8],'big')/(2**64-1)
    return 1+(unit*2-1)*amount
