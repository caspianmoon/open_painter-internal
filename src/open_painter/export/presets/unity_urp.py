"""Unity URP Lit metallic workflow, smoothness source Metallic Alpha.
https://docs.unity.cn/Packages/com.unity.render-pipelines.universal@17.0/manual/lit-shader.html
"""
def outputs():
    return [('BaseMap','BASE_COLOR_OPACITY'),('Normal','NORMAL'),
            ('MetallicGlossMap',('METALLIC','ZERO','ZERO','ONE_MINUS_ROUGHNESS')),
            ('OcclusionMap','AO'),('EmissionMap','EMISSION')]
