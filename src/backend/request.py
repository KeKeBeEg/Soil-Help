"""The request JSON.

    {
      "polygon": {"1": [-82.300, 35.915], "2": [-82.296, 35.915], "3": [-82.296, 35.912]},
      "t_range": ["2024-09-20", "2024-10-05"],
      "us_cache": "cache/us",
      "global_cache": "cache/global",
      "working_save": "runs/my_field",
      "label": "optional name for figures"
    }

- polygon: vertices as (x, y) = (longitude, latitude) in WGS84 degrees, ordered by their
  numeric key ("10" comes after "9"). A list of vertices also works, and so do "(x, y)"
  strings. The ring is closed automatically.
- t_range: first and last day (inclusive) of the period to show. It only changes the
  views; the analysis always runs over the fixed climate period (see plan.CLIMATE_YEARS).
- Paths may be relative; they are resolved against the request file's folder.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

EARLIEST_DAY = np.datetime64("2001-01-01")


@dataclass
class Request:
    polygon: list[tuple[float, float]]  # (lon, lat), open ring
    t_start: np.datetime64  # first day shown
    t_end: np.datetime64  # last day shown (inclusive)
    us_cache: Path
    global_cache: Path
    working_save: Path
    label: str
    source: Path | None = None


def _vertex(v) -> tuple[float, float]:
    if isinstance(v, str):
        v = [p for p in re.split(r"[\s,()\[\]]+", v) if p]
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ValueError(f"a polygon vertex must be (x, y), got {v!r}")
    x, y = float(v[0]), float(v[1])
    if not (-180 <= x <= 180 and -90 <= y <= 90):
        raise ValueError(f"vertex {v!r} is not (longitude, latitude) in degrees")
    return x, y


def _polygon(raw) -> list[tuple[float, float]]:
    if isinstance(raw, dict):
        try:
            items = sorted(raw.items(), key=lambda kv: int(kv[0]))
        except ValueError:
            raise ValueError("polygon keys must be vertex numbers: \"1\", \"2\", ...") from None
        verts = [_vertex(v) for _, v in items]
    elif isinstance(raw, list):
        verts = [_vertex(v) for v in raw]
    else:
        raise ValueError("polygon must be an object of numbered vertices or a list of vertices")
    if len(verts) > 1 and verts[0] == verts[-1]:
        verts = verts[:-1]
    if len(verts) < 3:
        raise ValueError(f"polygon needs at least 3 vertices, got {len(verts)}")
    return verts


def _day(s) -> np.datetime64:
    try:
        return np.datetime64(str(s).strip()[:10], "D")
    except ValueError:
        raise ValueError(f"dates must be YYYY-MM-DD, got {s!r}") from None


def parse(data: dict, base: Path = Path(".")) -> Request:
    missing = [k for k in ("polygon", "t_range", "us_cache", "global_cache", "working_save") if k not in data]
    if missing:
        raise ValueError(f"request is missing: {', '.join(missing)}")
    t_range = data["t_range"]
    if not isinstance(t_range, (list, tuple)) or len(t_range) != 2:
        raise ValueError("t_range must be [first_day, last_day]")
    t_start, t_end = _day(t_range[0]), _day(t_range[1])
    if t_end < t_start:
        raise ValueError(f"t_range ends ({t_end}) before it starts ({t_start})")
    latest = np.datetime64("today", "D") - 2
    if t_start < EARLIEST_DAY or t_end > latest:
        raise ValueError(f"t_range must lie within {EARLIEST_DAY} .. {latest} (rain data), got {t_start} .. {t_end}")

    def path(key):
        p = Path(data[key]).expanduser()
        return p if p.is_absolute() else (base / p).resolve()

    ws = path("working_save")
    return Request(polygon=_polygon(data["polygon"]), t_start=t_start, t_end=t_end, us_cache=path("us_cache"),
                   global_cache=path("global_cache"), working_save=ws, label=str(data.get("label") or ws.name))


def load(path) -> Request:
    path = Path(path).resolve()
    req = parse(json.loads(path.read_text(encoding="utf-8")), path.parent)
    req.source = path
    return req
