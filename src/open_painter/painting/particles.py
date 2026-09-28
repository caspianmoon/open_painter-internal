"""Fixed-step, seeded particle contacts. Replay consumes contacts, never physics."""
import math

import numpy as np

STEP = 1. / 120.
MAX_CONTACTS = 4096


def contacts(point, normal, radius, settings, seed, collide):
    """``collide(start, displacement)`` returns (point, normal, owner, face, object) or None."""
    rng = np.random.default_rng(seed)
    point, normal = np.asarray(point, np.float64), np.asarray(normal, np.float64)
    normal /= max(np.linalg.norm(normal), 1e-20)
    helper = np.array((0., 0., 1.)) if abs(normal[2]) < .9 else np.array((1., 0., 0.))
    tangent = np.cross(normal, helper)
    tangent /= max(np.linalg.norm(tangent), 1e-20)
    bitangent = np.cross(normal, tangent)
    gravity = np.asarray(settings.particle_gravity, np.float64)
    emitted = []
    steps = max(1, int(math.ceil(settings.particle_lifetime / STEP)))
    for particle in range(settings.particle_count):
        angle, spread = rng.uniform(0, 2 * math.pi), math.sqrt(rng.random()) * settings.particle_spread
        lateral = (tangent * math.cos(angle) + bitangent * math.sin(angle)) * spread
        position = point + normal * max(radius * 2, 1e-4) + lateral * radius
        velocity = (-normal + lateral) * settings.particle_speed
        birth = particle / max(settings.particle_rate, 1e-5)
        for step in range(steps):
            velocity = (velocity + gravity * STEP) * math.exp(-settings.particle_drag * STEP)
            delta = velocity * STEP
            hit = collide(position, delta)
            if hit is None:
                position += delta
                continue
            at, axis, owner, face, obj = hit
            at, axis = np.asarray(at, np.float64), np.asarray(axis, np.float64)
            emitted.append((birth + step * STEP, particle, at.copy(), axis.copy(), owner, face, obj, velocity.copy()))
            if len(emitted) >= MAX_CONTACTS:
                break
            if settings.particle_bounce <= 0:
                break
            velocity -= (1 + settings.particle_bounce) * np.dot(velocity, axis) * axis
            if np.linalg.norm(velocity) < .01:
                break
            position = at + axis * max(radius * 1e-4, 1e-6)
        if len(emitted) >= MAX_CONTACTS:
            break
    emitted.sort(key=lambda row: (row[0], row[1]))
    return emitted
