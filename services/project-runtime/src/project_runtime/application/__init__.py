"""What the API does with the kernel, between the wire and ``archflow``.

Nothing in this package shapes a response and nothing in it answers a design
question on its own: it opens the one bound project, asks ``archflow``, and
hands typed results to ``transport``.
"""

from __future__ import annotations

__all__: list[str] = []
