"""Shared records: a break is a record with no position, never an invented line."""
from dataclasses import dataclass, field, asdict


@dataclass
class Point:
    frame: str
    time: float | None = None
    xy: tuple | None = None
    raw_xy: tuple | None = None
    layer: str | None = None
    z: float | None = None
    state: str = 'unknown'
    confidence: float | None = None
    provenance: list = field(default_factory=list)
    layer_source: str = 'unknown'
    z_source: str = 'unknown'
    state_source: str = 'unknown'
    reason: str = ''


@dataclass
class Segment:
    points: list
    layer: str | None
    state: str
    reason: str


@dataclass
class Trajectory:
    points: list
    space: str
    sources: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def records(self):
        return [asdict(p) for p in self.points]
