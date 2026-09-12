from ...constants import CHANNEL_LABELS, CHANNELS


def outputs():
    return [(CHANNEL_LABELS[c], c) for c in CHANNELS]
