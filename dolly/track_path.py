"""Track interpolation: a dolly rail through the camera positions plus a push.

Spline and Smooth interpolate X, Y and Z independently over *time*, so the
timing of the keys bends the path: a key that is close in space but far away
in time, next to a fast segment, swings the camera wide or into a loop.

Track separates the two, like a physical dolly:

* The rail (shape) is a centripetal Catmull-Rom curve through the distinct key
  positions, parameterized by distance. It passes through every key and cannot
  form cusps or self-intersections inside a segment, whatever the timing.
* The push (timing) maps each key time to its distance along the rail. That
  distance-over-time curve is a C2 spline filtered to stay monotone (Hyman), so
  the camera reaches every key at its authored time, eases through keys and
  never moves backwards. Consecutive keys at one position are a clean hold.

The composite motion is not a cubic in time, so it is sampled into contiguous
cubic Hermite pieces in shot seconds (values and exact time derivatives at
every sub-knot). Both the editor evaluator and the native playback blob use the
same table, so preview and playback are identical.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
import math

# Gauss-Legendre 5-point nodes/weights on [0, 1].
_GL = tuple(((1 + x) / 2, w / 2) for x, w in (
    (0.0, 0.5688888888888889),
    (-0.5384693101056831, 0.47862867049936647),
    (0.5384693101056831, 0.47862867049936647),
    (-0.906179845938664, 0.23692688505618908),
    (0.906179845938664, 0.23692688505618908),
))
ARC_TABLE_STEPS = 32          # arc-length table entries per rail segment
MIN_PIECES, MAX_PIECES = 2, 16
TOLERANCE = 0.05              # world units between the Hermite table and the exact track
MAX_SEGMENTS = 4095           # native path blob limit
_HOLD_EPSILON = 1e-6          # positions this close are the same rail knot


@dataclass(frozen=True)
class TrackTable:
    times: tuple[float, ...]
    values: tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]
    derivatives: tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]


Vec = tuple[float, float, float]


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _norm(a: Vec) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


class _Rail:
    """Centripetal Catmull-Rom rail with an arc-length parameterization."""

    def __init__(self, points: list[Vec]):
        self.points = points
        count = len(points)
        scale = max(1.0, max(abs(c) for p in points for c in p))
        # Phantom endpoints reflect the neighbors, giving natural end tangents.
        ext = [_sub(points[0], _sub(points[1], points[0]))] + points + [
            _sub(points[-1], _sub(points[-2], points[-1]))]
        knots = [0.0]
        for i in range(len(ext) - 1):
            knots.append(knots[-1] + max(math.sqrt(_norm(_sub(ext[i + 1], ext[i]))), 1e-12 * scale))
        # Barry-Goldman tangent at each real point, in the global knot parameter.
        tangents = []
        for i in range(1, count + 1):
            t0, t1, t2 = knots[i - 1], knots[i], knots[i + 1]
            p0, p1, p2 = ext[i - 1], ext[i], ext[i + 1]
            tangents.append(tuple(
                (p1[k] - p0[k]) / (t1 - t0) - (p2[k] - p0[k]) / (t2 - t0) + (p2[k] - p1[k]) / (t2 - t1)
                for k in range(3)))
        # Per segment Hermite in local u in [0, 1].
        self.segments = []
        for j in range(count - 1):
            span = knots[j + 2] - knots[j + 1]
            self.segments.append((points[j], points[j + 1],
                                  tuple(c * span for c in tangents[j]),
                                  tuple(c * span for c in tangents[j + 1])))
        self.tables = []      # (u list, cumulative length list) per segment
        self.starts = [0.0]
        for j in range(count - 1):
            us, lengths = [0.0], [0.0]
            for step in range(1, ARC_TABLE_STEPS + 1):
                a, b = (step - 1) / ARC_TABLE_STEPS, step / ARC_TABLE_STEPS
                us.append(b)
                lengths.append(lengths[-1] + self._length(j, a, b))
            self.tables.append((us, lengths))
            self.starts.append(self.starts[-1] + lengths[-1])

    def point(self, j: int, u: float) -> Vec:
        p0, p1, m0, m1 = self.segments[j]
        u2, u3 = u * u, u * u * u
        h00, h10, h01, h11 = 2 * u3 - 3 * u2 + 1, u3 - 2 * u2 + u, -2 * u3 + 3 * u2, u3 - u2
        return tuple(h00 * p0[k] + h10 * m0[k] + h01 * p1[k] + h11 * m1[k] for k in range(3))

    def velocity(self, j: int, u: float) -> Vec:
        p0, p1, m0, m1 = self.segments[j]
        u2 = u * u
        d00, d10, d01, d11 = 6 * u2 - 6 * u, 3 * u2 - 4 * u + 1, -6 * u2 + 6 * u, 3 * u2 - 2 * u
        return tuple(d00 * p0[k] + d10 * m0[k] + d01 * p1[k] + d11 * m1[k] for k in range(3))

    def _length(self, j: int, a: float, b: float) -> float:
        return (b - a) * sum(w * _norm(self.velocity(j, a + (b - a) * x)) for x, w in _GL)

    def locate(self, s: float) -> tuple[int, float]:
        """Segment and local u at rail distance ``s``."""
        if s <= 0 or len(self.segments) == 0:
            return 0, 0.0
        j = min(max(bisect_right(self.starts, s) - 1, 0), len(self.segments) - 1)
        us, lengths = self.tables[j]
        local = min(max(s - self.starts[j], 0.0), lengths[-1])
        k = min(max(bisect_right(lengths, local) - 1, 0), len(us) - 2)
        a, b = us[k], us[k + 1]
        target = local - lengths[k]
        low, high = a, b
        u = a + (b - a) * (target / (lengths[k + 1] - lengths[k])) if lengths[k + 1] > lengths[k] else a
        for _ in range(8):  # safeguarded Newton on the arc length
            error = self._length(j, a, u) - target
            speed = _norm(self.velocity(j, u))
            if error > 0:
                high = u
            else:
                low = u
            if abs(error) < 1e-9 * max(1.0, lengths[-1]):
                break
            step = u - error / speed if speed > 1e-12 else None
            u = step if step is not None and low < step < high else (low + high) / 2
        return j, u


def _push_tangents(times: list[float], distances: list[float]) -> list[float]:
    """Natural-spline slopes for distance(time), Hyman-filtered to stay monotone."""
    from .path import _spline_tangents  # local import: path imports this module

    count = len(times)
    secants = [(distances[i + 1] - distances[i]) / (times[i + 1] - times[i]) for i in range(count - 1)]
    slopes = _spline_tangents(times, distances) if count > 2 else [secants[0], secants[0]]
    result = []
    for i, slope in enumerate(slopes):
        around = [secants[k] for k in (i - 1, i) if 0 <= k < count - 1]
        limit = 3 * min(around)
        result.append(0.0 if limit <= 0 else min(max(slope, 0.0), limit))
    return result


def _hermite(v0: float, v1: float, d0: float, d1: float, span: float, u: float) -> tuple[float, float]:
    u2, u3 = u * u, u * u * u
    value = ((2 * u3 - 3 * u2 + 1) * v0 + (u3 - 2 * u2 + u) * span * d0
             + (-2 * u3 + 3 * u2) * v1 + (u3 - u2) * span * d1)
    rate = ((6 * u2 - 6 * u) * v0 + (3 * u2 - 4 * u + 1) * span * d0
            + (-6 * u2 + 6 * u) * v1 + (3 * u2 - 2 * u) * span * d1) / span
    return value, rate


@lru_cache(maxsize=32)
def build_track(times: tuple[float, ...], xs: tuple[float, ...], ys: tuple[float, ...],
                zs: tuple[float, ...]) -> TrackTable | None:
    """Sample the track into Hermite sub-knots, or None when not representable."""
    count = len(times)
    if count < 2:
        return None
    try:
        return _build(times, list(zip(xs, ys, zs)))
    except (ArithmeticError, ValueError):
        return None


def _build(times: tuple[float, ...], positions: list[Vec]) -> TrackTable | None:
    count = len(times)
    scale = max(1.0, max(abs(c) for p in positions for c in p))
    # Distinct rail knots; consecutive equal positions are holds on one knot.
    knots = [positions[0]]
    knot_of = [0]
    for p in positions[1:]:
        if _norm(_sub(p, knots[-1])) > _HOLD_EPSILON * scale:
            knots.append(p)
        knot_of.append(len(knots) - 1)
    if len(knots) == 1:
        zero = tuple(0.0 for _ in times)
        return TrackTable(tuple(times), tuple(tuple(p[k] for p in positions) for k in range(3)),
                          (zero, zero, zero))
    rail = _Rail(knots)
    distances = [rail.starts[knot_of[i]] for i in range(count)]
    push = _push_tangents(list(times), distances)

    def sample(t: float, interval: int) -> tuple[Vec, Vec]:
        span = times[interval + 1] - times[interval]
        u = (t - times[interval]) / span
        s, rate = _hermite(distances[interval], distances[interval + 1],
                           push[interval], push[interval + 1], span, u)
        s = min(max(s, distances[interval]), distances[interval + 1])
        j, v = rail.locate(s)
        point = rail.point(j, v)
        direction = rail.velocity(j, v)
        length = _norm(direction)
        if length <= 1e-12 or rate <= 0:
            return point, (0.0, 0.0, 0.0)
        return point, tuple(c / length * rate for c in direction)

    budget = max(1, MAX_SEGMENTS // max(1, count - 1))
    cap = min(MAX_PIECES, budget)
    out_t: list[float] = [times[0]]
    out_p: list[Vec] = [positions[0]]
    out_d: list[Vec] = [sample(times[0], 0)[1]]
    for i in range(count - 1):
        t0, t1 = times[i], times[i + 1]
        if distances[i] == distances[i + 1]:
            out_t.append(t1)
            out_p.append(positions[i + 1])
            out_d.append((0.0, 0.0, 0.0))
            continue
        pieces = min(MIN_PIECES, cap)
        while True:
            grid = [t0 + (t1 - t0) * k / pieces for k in range(pieces + 1)]
            grid[-1] = t1
            states = [sample(t, i) for t in grid]
            states[-1] = (positions[i + 1], states[-1][1])
            worst = 0.0
            for k in range(pieces):
                a, b = grid[k], grid[k + 1]
                mid = (a + b) / 2
                exact = sample(mid, i)[0]
                approx = tuple(_hermite(states[k][0][c], states[k + 1][0][c], states[k][1][c],
                                        states[k + 1][1][c], b - a, 0.5)[0] for c in range(3))
                worst = max(worst, _norm(_sub(exact, approx)))
            if worst <= TOLERANCE or pieces >= cap:
                break
            pieces = min(pieces * 2, cap)
        out_t.extend(grid[1:])
        out_p.extend(state[0] for state in states[1:])
        out_d.extend(state[1] for state in states[1:])
    # Key knots carry the authored positions exactly (first key above, each
    # interval end via ``states[-1]`` or the hold branch).
    if not all(math.isfinite(c) for p in out_p + out_d for c in p):
        return None
    return TrackTable(tuple(out_t),
                      tuple(tuple(p[k] for p in out_p) for k in range(3)),
                      tuple(tuple(d[k] for d in out_d) for k in range(3)))
