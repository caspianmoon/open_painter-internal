def outputs():
    return [
        ("BaseColor", "BASE_COLOR"),
        ("Normal", "NORMAL"),
        ("MetallicSmoothness", ("METALLIC", "ZERO", "ZERO", "ONE_MINUS_ROUGHNESS")),
        ("Occlusion", "AO"),
    ]
