"""Blender Open Painter extension entry point."""


def register():
    from . import registration

    registration.register()


def unregister():
    from . import registration

    registration.unregister()
