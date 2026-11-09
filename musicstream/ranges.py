"""HTTP Range header parsing (RFC 9110, single byte range)."""

from __future__ import annotations


class RangeNotSatisfiable(ValueError):
    """The Range header is valid but no part of it overlaps the resource."""


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Parse a ``Range`` header into an inclusive ``(start, end)`` byte range.

    Returns ``None`` when the full resource should be served: no header, a
    syntactically invalid header (RFC 9110 says to ignore it), a unit other
    than ``bytes`` or a multi-range request (not supported, so ignored).
    Raises :class:`RangeNotSatisfiable` for ranges entirely beyond the end.
    """
    if not header:
        return None
    unit, sep, spec = header.strip().partition("=")
    if not sep or unit.strip().lower() != "bytes":
        return None
    spec = spec.strip()
    if "," in spec:
        return None
    first, dash, last = spec.partition("-")
    first, last = first.strip(), last.strip()
    if not dash or (first and not first.isdigit()) or (last and not last.isdigit()):
        return None
    if not first and not last:
        return None
    if not first:  # suffix range: last N bytes
        length = int(last)
        if length == 0 or size == 0:
            raise RangeNotSatisfiable(header)
        return max(0, size - length), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if last and end < start:
        return None
    if start >= size:
        raise RangeNotSatisfiable(header)
    return start, min(end, size - 1)
