"""Versioned artistic response curves; parameters are not physical years."""
VERSION=1
PROFILES={
    'NEUTRAL':dict(dirt=1.,fade=1.,oxidation=1.,dust=1.),
    'INDOOR':dict(dirt=.65,fade=.3,oxidation=.25,dust=1.15),
    'COASTAL':dict(dirt=.85,fade=1.1,oxidation=1.5,dust=.55),
    'ARID':dict(dirt=.6,fade=1.5,oxidation=.3,dust=1.5),
    'INDUSTRIAL':dict(dirt=1.4,fade=.7,oxidation=1.1,dust=.9),
}


def responses(age,usage,maintenance,profile='NEUTRAL'):
    if any(not 0<=value<=1 for value in (age,usage,maintenance)):
        raise ValueError('Age, usage and maintenance must be normalized to 0–1')
    p=PROFILES[profile]
    clamp=lambda value:max(0.,min(1.,value))
    return {
        'DIRT':clamp((age**.6*.65+usage*.35)*(1-maintenance*.85)*p['dirt']),
        'DUST':clamp((age**.4*.9+usage*.1)*(1-maintenance*.95)*p['dust']),
        'FADE':clamp(age**1.3*p['fade']),
        'SCRATCHES':clamp((usage**.65*.85+age*.15)*(1-maintenance*.15)),
        'OXIDATION':clamp(age**1.7*(.5+usage*.5)*(1-maintenance*.6)*p['oxidation']),
        'COATING_LOSS':clamp(usage**1.4*.6+age**2*.4),
    }
