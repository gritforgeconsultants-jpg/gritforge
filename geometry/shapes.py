"""AISC W-shape profile builder.

Reads ``shapes_db.csv`` (a subset of the AISC v15 Shapes Database) and turns a
named wide-flange shape into a filleted cross-section polygon, then extrudes it
into a solid ``trimesh.Trimesh``.

Everything in this package works in **inches** (the native unit of the AISC
tables). The exporter is responsible for scaling to meters for glTF.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh
from shapely.geometry import Polygon

_DB_PATH = Path(__file__).with_name("shapes_db.csv")


@dataclass(frozen=True)
class WShape:
    """Properties of a single AISC wide-flange shape (inches)."""

    label: str
    weight: float  # plf
    area: float    # in^2
    d: float       # overall depth
    bf: float      # flange width
    tw: float      # web thickness
    tf: float      # flange thickness
    kdes: float    # design fillet distance (flange face to web toe)
    T: float       # clear web depth between fillets

    @property
    def fillet_radius(self) -> float:
        """Approximate flange-to-web fillet radius."""
        return max(self.kdes - self.tf, 0.0)


def _load_db() -> dict[str, WShape]:
    shapes: dict[str, WShape] = {}
    with _DB_PATH.open(newline="") as fh:
        for row in csv.DictReader(fh):
            shapes[row["AISC_Manual_Label"]] = WShape(
                label=row["AISC_Manual_Label"],
                weight=float(row["W"]),
                area=float(row["A"]),
                d=float(row["d"]),
                bf=float(row["bf"]),
                tw=float(row["tw"]),
                tf=float(row["tf"]),
                kdes=float(row["kdes"]),
                T=float(row["T"]),
            )
    return shapes


_SHAPES = _load_db()


def get(label: str) -> WShape:
    """Look up a shape by its AISC label, e.g. ``"W14X90"``."""
    try:
        return _SHAPES[label.upper()]
    except KeyError as exc:
        raise KeyError(
            f"{label!r} not in shapes_db.csv; have {sorted(_SHAPES)}"
        ) from exc


def section_polygon(shape: WShape, fillet: bool = True) -> Polygon:
    """Return the wide-flange cross-section as a shapely polygon.

    The polygon lives in a local 2-D frame:

    * ``x`` runs along the member **depth** ``d`` (-d/2 .. d/2)
    * ``y`` runs along the **flange width** ``bf`` (-bf/2 .. bf/2)

    Reentrant flange-to-web corners are rounded to the fillet radius via a
    morphological closing so the profile reads like real rolled steel.
    """
    d, bf, tw, tf = shape.d, shape.bf, shape.tw, shape.tf
    hd, hbf, htw = d / 2.0, bf / 2.0, tw / 2.0
    wu = hd - tf  # web reaches to +/- wu in the depth direction

    pts = [
        (hd, hbf), (hd, -hbf), (hd - tf, -hbf), (hd - tf, -htw),
        (-wu, -htw), (-wu, -hbf), (-hd, -hbf), (-hd, hbf),
        (-wu, hbf), (-wu, htw), (hd - tf, htw), (hd - tf, hbf),
    ]
    poly = Polygon(pts)

    if fillet and shape.fillet_radius > 1e-4:
        r = shape.fillet_radius
        # Closing (dilate then erode) rounds the concave web/flange corners
        # while leaving the convex outer corners crisp.
        poly = poly.buffer(r, join_style="round").buffer(-r, join_style="round")

    return poly


def extrude(shape: WShape, length: float, fillet: bool = True) -> trimesh.Trimesh:
    """Extrude a shape ``length`` inches along local +Z, centred on the origin.

    The resulting solid is centred at the origin in all three axes so callers
    can position it with a single transform.
    """
    poly = section_polygon(shape, fillet=fillet)
    mesh = trimesh.creation.extrude_polygon(poly, height=length)
    # extrude_polygon builds from z=0..length; recentre on z.
    mesh.apply_translation((0.0, 0.0, -length / 2.0))
    return mesh
