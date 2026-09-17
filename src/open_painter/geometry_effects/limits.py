"""Project-wide instance limits, enforced before creating helper data."""
MAX_INSTANCES = 20000
MAX_TRIANGLES = 500000


def headroom(scene, replacing=None):
    """Instances and estimated triangles the project can still take on."""
    used_instances = used_triangles = 0
    for obj in scene.objects:
        if obj != replacing and obj.get('open_painter_geometry_effect') == 'INSTANCE_HELPER':
            used_instances += obj.get('instance_count',0)
            used_triangles += obj.get('estimated_triangles',0)
    return MAX_INSTANCES - used_instances, MAX_TRIANGLES - used_triangles


def check(scene, count, triangles, replacing=None):
    instances_left, triangles_left = headroom(scene, replacing)
    if count < 0 or count > instances_left:
        raise ValueError(f'Geometry effect exceeds the {MAX_INSTANCES:,} project instance limit')
    if triangles > triangles_left:
        raise ValueError(f'Geometry effect exceeds the {MAX_TRIANGLES:,} estimated triangle limit')
    return dict(instances=count, triangles=triangles, estimated_realized_bytes=triangles*3*32)
