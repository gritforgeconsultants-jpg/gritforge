"""Part builders for a bolted-web / welded-flange (WUF-W) steel moment connection.

Every builder returns one or more :class:`Part` records whose ``mesh`` is already
positioned in the shared world frame (inches):

* ``+X`` — beam longitudinal axis; the beam cantilevers in ``+X`` off the column.
* ``+Y`` — vertical; the column longitudinal axis.
* ``+Z`` — lateral (out of the connection face).

Each part also carries an ``explode`` vector: the translation applied to move the
part from its assembled home to its parked (deconstructed) position. The exporter
turns those vectors into the construct / deconstruct animation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import trimesh

from . import shapes

# --------------------------------------------------------------------------- #
# Connection definition
# --------------------------------------------------------------------------- #
COLUMN_SHAPE = "W14X90"
BEAM_SHAPE = "W18X50"

COLUMN_HEIGHT = 64.0     # in
BEAM_LENGTH = 46.0       # in (from column face outward)

TAB_THK = 0.5            # shear-tab thickness
TAB_HEIGHT = 11.5        # shear-tab height
TAB_WIDTH = 4.5          # shear-tab reach past the column face
DOUBLER_THK = 0.375
CONT_THK = 0.625         # continuity-plate thickness
BACKING_THK = 0.375
BACKING_W = 1.25
BOLT_DIA = 0.875         # 7/8" A325
HOLE_DIA = BOLT_DIA + 0.0625
N_BOLTS = 4
BOLT_GAGE_Y = 3.0        # vertical bolt spacing


@dataclass
class Part:
    """A single named solid in the connection."""

    name: str
    mesh: trimesh.Trimesh
    kind: str = "steel"            # steel | bolt | weld
    explode: np.ndarray = field(  # world-space parked offset
        default_factory=lambda: np.zeros(3)
    )
    seq: int = 0                   # assembly order (0 = first / anchor)

    def __post_init__(self) -> None:
        self.explode = np.asarray(self.explode, dtype=float)


# --------------------------------------------------------------------------- #
# Low-level primitives
# --------------------------------------------------------------------------- #
def _mat(R: np.ndarray, t) -> np.ndarray:
    M = np.eye(4)
    M[:3, :3] = R
    M[:3, 3] = t
    return M


# Rotations that carry a Z-extruded local profile into the world frame.
_R_BEAM = np.array([[0, 0, 1], [1, 0, 0], [0, 1, 0]], float)    # localZ->X, x->Y, y->Z
_R_COLUMN = np.array([[1, 0, 0], [0, 0, 1], [0, 1, 0]], float)  # localZ->Y, x->X, y->Z


def _box(lx: float, ly: float, lz: float, center) -> trimesh.Trimesh:
    m = trimesh.creation.box(extents=(lx, ly, lz))
    m.apply_translation(center)
    return m


def _hex_prism(across_flats: float, height: float) -> trimesh.Trimesh:
    """Regular hexagonal prism extruded along +Z, centred on the origin."""
    R = across_flats / np.sqrt(3.0)  # circumradius
    ang = np.deg2rad(np.arange(30, 360, 60))
    ring = np.column_stack([R * np.cos(ang), R * np.sin(ang)])
    from shapely.geometry import Polygon

    m = trimesh.creation.extrude_polygon(Polygon(ring), height=height)
    m.apply_translation((0, 0, -height / 2.0))
    return m


def _drill(solid: trimesh.Trimesh, centers, direction: str, dia: float,
           depth: float) -> trimesh.Trimesh:
    """Subtract cylindrical holes from ``solid``.

    ``centers`` are world points on the solid; ``direction`` is 'x', 'y' or 'z'.
    """
    axis = {"x": 0, "y": 1, "z": 2}[direction]
    cutters = []
    for c in centers:
        cyl = trimesh.creation.cylinder(radius=dia / 2.0, height=depth, sections=32)
        if axis == 0:
            cyl.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
        elif axis == 1:
            cyl.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
        cyl.apply_translation(c)
        cutters.append(cyl)
    return trimesh.boolean.difference([solid, *cutters], engine="manifold")


# --------------------------------------------------------------------------- #
# Members
# --------------------------------------------------------------------------- #
def column() -> Part:
    """Vertical W-shape column; strong-axis flanges face the beam."""
    sh = shapes.get(COLUMN_SHAPE)
    m = shapes.extrude(sh, COLUMN_HEIGHT)
    m.apply_transform(_mat(_R_COLUMN, (0, 0, 0)))
    return Part("column", m, seq=0)  # anchor: never moves


def beam() -> Part:
    """Horizontal W-shape beam framing into the column flange face."""
    sh = shapes.get(BEAM_SHAPE)
    m = shapes.extrude(sh, BEAM_LENGTH)
    x_face = shapes.get(COLUMN_SHAPE).d / 2.0
    m.apply_transform(_mat(_R_BEAM, (x_face + BEAM_LENGTH / 2.0, 0, 0)))
    # Web holes for the shear-tab bolts.
    m = _drill(m, _bolt_points(x_face), "z", HOLE_DIA, sh.bf * 2)
    return Part("beam", m, explode=(30, 0, 0), seq=4)


# --------------------------------------------------------------------------- #
# Column-side reinforcement
# --------------------------------------------------------------------------- #
def continuity_plate(level: str) -> Part:
    """Horizontal stiffener inside the column, aligned with a beam flange.

    ``level`` is ``"top"`` or ``"bottom"``.
    """
    col = shapes.get(COLUMN_SHAPE)
    bm = shapes.get(BEAM_SHAPE)
    inner_x = col.d / 2.0 - col.tf          # reach between flanges
    y = (bm.d / 2.0 - bm.tf / 2.0) * (1 if level == "top" else -1)
    lx = 2 * inner_x - 0.25
    lz = col.bf - 0.5
    plate = _box(lx, CONT_THK, lz, (0, y, 0))
    # Notch to clear the column web.
    plate = trimesh.boolean.difference(
        [plate, _box(lx + 1, CONT_THK + 1, col.tw + 0.06, (0, y, 0))],
        engine="manifold",
    )
    return Part(f"continuity_plate_{level}", plate,
                explode=(15, 13 if level == "top" else -13, 2), seq=2)


def doubler() -> Part:
    """Panel-zone doubler plate against the column web."""
    col = shapes.get(COLUMN_SHAPE)
    bm = shapes.get(BEAM_SHAPE)
    lx = col.d - 2 * col.tf - 0.5
    ly = bm.d + 1.0
    z = col.tw / 2.0 + DOUBLER_THK / 2.0
    plate = _box(lx, ly, DOUBLER_THK, (0, 0, z))
    return Part("doubler", plate, explode=(0, 0, 22), seq=1)


# --------------------------------------------------------------------------- #
# Beam-side connection
# --------------------------------------------------------------------------- #
def _bolt_points(x_face: float):
    """World centres of the shear-tab / web bolt line."""
    x = x_face + TAB_WIDTH * 0.55
    ys = (np.arange(N_BOLTS) - (N_BOLTS - 1) / 2.0) * BOLT_GAGE_Y
    return [(x, float(y), 0.0) for y in ys]


def shear_tab() -> Part:
    """Vertical plate welded to the column flange, bolted to the beam web."""
    col = shapes.get(COLUMN_SHAPE)
    bm = shapes.get(BEAM_SHAPE)
    x_face = col.d / 2.0
    z = bm.tw / 2.0 + TAB_THK / 2.0
    plate = _box(TAB_WIDTH, TAB_HEIGHT, TAB_THK, (x_face + TAB_WIDTH / 2.0, 0, z))
    plate = _drill(plate, _bolt_points(x_face), "z", HOLE_DIA, TAB_THK * 4)
    return Part("shear_tab", plate, explode=(20, 0, 13), seq=3)


def backing_bar(level: str) -> Part:
    """Steel backing bar under a beam-flange complete-joint-penetration weld."""
    col = shapes.get(COLUMN_SHAPE)
    bm = shapes.get(BEAM_SHAPE)
    x_face = col.d / 2.0
    y = (bm.d / 2.0 - bm.tf) * (1 if level == "top" else -1)
    bar = _box(BACKING_W, BACKING_THK, bm.bf + 0.5,
               (x_face + 0.1, y, 0))
    return Part(f"backing_bar_{level}", bar, kind="steel",
                explode=(11, 24 if level == "top" else -24, 6), seq=2)


def bolt(index: int) -> Part:
    """One A325 hex bolt with head and nut, through tab and web."""
    col = shapes.get(COLUMN_SHAPE)
    bm = shapes.get(BEAM_SHAPE)
    x_face = col.d / 2.0
    cx, cy, _ = _bolt_points(x_face)[index]

    grip = bm.tw + TAB_THK
    z0 = -bm.tw / 2.0
    z1 = TAB_THK + bm.tw / 2.0
    shank = trimesh.creation.cylinder(radius=BOLT_DIA / 2.0, height=grip + 1.4,
                                      sections=24)
    head = _hex_prism(1.4, 0.55)
    head.apply_translation((0, 0, -(grip + 1.4) / 2.0 + 0.275))
    nut = _hex_prism(1.4, 0.5)
    nut.apply_translation((0, 0, (grip + 1.4) / 2.0 - 0.25))
    b = trimesh.util.concatenate([shank, head, nut])
    # Orient along Z (already) and drop it on the bolt line, head on the -Z side.
    b.apply_translation((cx, cy, (z0 + z1) / 2.0))
    # Fan the bolts out diagonally so each one reads separately when exploded.
    return Part(f"bolt_{index}", b, kind="bolt",
                explode=(22 + index * 2.0, (index - 1.5) * 3.0, -16), seq=5)


def weld_bead(name: str, start, end, size: float = 0.5) -> Part:
    """A glowing fillet-weld bead running from ``start`` to ``end``.

    Modelled as a triangular-section prism so it reads as a real weld toe.
    """
    from shapely.geometry import Polygon

    start = np.asarray(start, float)
    end = np.asarray(end, float)
    length = float(np.linalg.norm(end - start))
    tri = Polygon([(0, 0), (size, 0), (0, size)])
    m = trimesh.creation.extrude_polygon(tri, height=length)
    # Extruded along +Z; aim it from start to end.
    d = (end - start) / length
    z = np.array([0, 0, 1.0])
    v = np.cross(z, d)
    s = np.linalg.norm(v)
    if s < 1e-9:
        R = np.eye(3) if d[2] > 0 else trimesh.transformations.rotation_matrix(
            np.pi, [1, 0, 0])[:3, :3]
    else:
        c = float(np.dot(z, d))
        vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
        R = np.eye(3) + vx + vx @ vx * ((1 - c) / (s * s))
    m.apply_transform(_mat(R, (0, 0, 0)))
    m.apply_translation(start)
    return Part(name, m, kind="weld", explode=(6, 0, 0), seq=6)


# --------------------------------------------------------------------------- #
# Full assembly
# --------------------------------------------------------------------------- #
def moment_connection() -> list[Part]:
    """Return every part of the connection, assembled in the world frame."""
    col = shapes.get(COLUMN_SHAPE)
    bm = shapes.get(BEAM_SHAPE)
    x_face = col.d / 2.0
    hbf = bm.bf / 2.0
    y_top = bm.d / 2.0
    y_bot = -bm.d / 2.0

    parts: list[Part] = [
        column(),
        doubler(),
        continuity_plate("top"),
        continuity_plate("bottom"),
        backing_bar("top"),
        backing_bar("bottom"),
        shear_tab(),
        beam(),
    ]
    parts += [bolt(i) for i in range(N_BOLTS)]

    # Flange CJP welds (top & bottom) + shear-tab-to-column vertical welds.
    parts += [
        weld_bead("weld_flange_top", (x_face, y_top, -hbf), (x_face, y_top, hbf), 0.55),
        weld_bead("weld_flange_bottom", (x_face, y_bot, -hbf), (x_face, y_bot, hbf), 0.55),
        weld_bead("weld_tab_near",
                  (x_face, -TAB_HEIGHT / 2, bm.tw / 2),
                  (x_face, TAB_HEIGHT / 2, bm.tw / 2), 0.4),
    ]
    return parts
