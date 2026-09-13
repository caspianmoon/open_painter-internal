"""Unity HDRP Lit Mask Map: metallic/AO/detail-mask/smoothness.
https://docs.unity.cn/Packages/com.unity.render-pipelines.high-definition@12.1/manual/Mask-Map-and-Detail-Map.html
"""
def outputs():
    return [('BaseColorMap','BASE_COLOR_OPACITY'),('NormalMap','NORMAL'),
            ('MaskMap',('METALLIC','AO','ONE','ONE_MINUS_ROUGHNESS')),
            ('EmissiveColorMap','EMISSION')]
