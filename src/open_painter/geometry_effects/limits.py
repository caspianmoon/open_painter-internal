"""Project-wide instance limits, enforced before creating helper data."""
MAX_INSTANCES = 20000
MAX_TRIANGLES = 500000


def check(scene, count, triangles, replacing=None):
    used_instances = used_triangles = 0
    for obj in scene.objects:
        if obj != replacing and obj.get('open_painter_geometry_effect') == 'INSTANCE_HELPER':
            used_instances += obj.get('instance_count',0)
            used_triangles += obj.get('estimated_triangles',0)
    if count < 0 or used_instances + count > MAX_INSTANCES:
        raise ValueError(f'Geometry effect exceeds the {MAX_INSTANCES:,} project instance limit')
    if used_triangles + triangles > MAX_TRIANGLES:
        raise ValueError(f'Geometry effect exceeds the {MAX_TRIANGLES:,} estimated triangle limit')
    return dict(instances=count, triangles=triangles, estimated_realized_bytes=triangles*3*32)
