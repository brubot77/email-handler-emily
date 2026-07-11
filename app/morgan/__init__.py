"""Morgan property document tracking agent."""


def handle_morgan_message(*args, **kwargs):
    from .handler import handle_morgan_message as _handler
    return _handler(*args, **kwargs)


__all__ = ["handle_morgan_message"]
