"""The app's HTTP surface, readable across Starlette versions.

Tests that assert "no endpoint here can change anything" have to enumerate
routes, and how a FastAPI app exposes them changed underneath us.

* Up to Starlette 1.0, ``include_router()`` flattened the sub-router into
  ``app.routes``: every entry was an ``APIRoute`` carrying the full prefixed
  ``.path``.
* From Starlette 1.6, ``include_router()`` appends a single ``_IncludedRouter``
  instead. It has **no** ``.path``, and the prefixed paths live behind
  ``effective_candidates()``.

Requests route identically either way, so the change is invisible to every
test that calls an endpoint — and fatal to every test that reads ``.path``.
Under the new version those tests saw an empty route list and a read-only
assertion over nothing passes vacuously. That is the failure mode this module
exists to prevent: the caller gets a count back and can refuse to pass on zero.

`fastapi>=0.115.0` is unpinned, so CI resolves a newer pair than a developer's
existing virtualenv holds. The versions differing is the reason this was green
locally and red in CI.
"""
from __future__ import annotations

from typing import Any, Iterator


def iter_route_surface(app: Any) -> Iterator[tuple[str, set[str]]]:
    """Yield ``(path, methods)`` for every routable endpoint of ``app``.

    Paths are full and prefixed, as a client would call them. Anything this
    cannot read contributes nothing rather than an empty string, so a third
    route shape shows up as a smaller count instead of a silent pass.
    """
    for route in getattr(app, "routes", ()):
        path = getattr(route, "path", None)
        if path:
            yield str(path), {str(m) for m in (getattr(route, "methods", None) or ())}
            continue

        # Starlette >= 1.6: one opaque router object standing in for many
        # routes. Its candidates carry the prefixed path.
        candidates = getattr(route, "effective_candidates", None)
        if callable(candidates):
            for candidate in candidates():
                candidate_path = getattr(candidate, "path", None)
                if candidate_path:
                    yield (
                        str(candidate_path),
                        {
                            str(m)
                            for m in (getattr(candidate, "methods", None) or ())
                        },
                    )


def routes_under(app: Any, *prefixes: str) -> list[tuple[str, set[str]]]:
    """Every route whose path starts with one of ``prefixes``."""
    return [
        (path, methods)
        for path, methods in iter_route_surface(app)
        if path.startswith(prefixes)
    ]


__all__ = ["iter_route_surface", "routes_under"]
