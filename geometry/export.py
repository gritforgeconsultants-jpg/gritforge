"""Tessellate the moment connection and export a single animated GLB.

The GLB contains one named node per part (``column``, ``beam``, ``shear_tab``,
``bolt_0`` ...) and two animation clips:

* **construct** — parts fly in from their parked positions and seat, staggered by
  assembly order, then the welds glow on.
* **deconstruct** — the reverse: welds fade, parts retreat to an exploded view.

Run with ``python -m geometry.export`` to (re)generate ``connection.glb``.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pygltflib as gl

from . import parts as P

IN_TO_M = 0.0254  # inches -> metres (glTF works in metres)

# Assembly timeline (seconds).
T_CONSTRUCT = 5.0
T_HOLD = 2.0
T_DECONSTRUCT = 5.0
SEAT = 1.8          # time a single part takes to travel
SEQ_MAX = 6

# PBR materials -------------------------------------------------------------- #
# Raw structural steel: dark, slightly blue mill-scale, high roughness so it
# reads as hot-rolled steel rather than polished chrome. Bolts are a touch
# lighter and smoother (machined). Welds glow molten orange.
_STEEL = dict(baseColorFactor=[0.135, 0.142, 0.155, 1.0], metallic=0.90, rough=0.66)
_BOLT = dict(baseColorFactor=[0.30, 0.305, 0.32, 1.0], metallic=1.0, rough=0.42)
_WELD = dict(baseColorFactor=[0.95, 0.32, 0.06, 1.0], metallic=0.5, rough=0.45,
             emissive=[1.0, 0.40, 0.09], emissive_strength=7.0)


class _GLB:
    """Accumulates meshes and animation channels into a single binary buffer."""

    def __init__(self) -> None:
        self.blob = bytearray()
        self.g = gl.GLTF2(
            asset=gl.Asset(version="2.0", generator="gritforge.geometry"),
            scenes=[gl.Scene(nodes=[])],
            scene=0,
        )
        self.g.extensionsUsed = ["KHR_materials_emissive_strength"]
        self._materials: dict[str, int] = {}

    # -- binary helpers -----------------------------------------------------
    def _pad(self) -> None:
        while len(self.blob) % 4:
            self.blob.append(0)

    def _view(self, data: bytes, target: int | None = None) -> int:
        self._pad()
        offset = len(self.blob)
        self.blob += data
        self.g.bufferViews.append(
            gl.BufferView(buffer=0, byteOffset=offset, byteLength=len(data),
                          target=target)
        )
        return len(self.g.bufferViews) - 1

    def _accessor(self, view: int, ctype: int, count: int, atype: str,
                  mn=None, mx=None) -> int:
        self.g.accessors.append(
            gl.Accessor(bufferView=view, componentType=ctype, count=count,
                        type=atype, min=mn, max=mx)
        )
        return len(self.g.accessors) - 1

    def _floats(self, arr: np.ndarray, atype: str, target=None,
                bounds=False) -> int:
        arr = np.asarray(arr, dtype=np.float32)
        view = self._view(arr.tobytes(), target)
        mn = arr.min(axis=0).tolist() if bounds else None
        mx = arr.max(axis=0).tolist() if bounds else None
        if arr.ndim == 1:  # scalar stream
            mn = [float(arr.min())] if bounds else None
            mx = [float(arr.max())] if bounds else None
        return self._accessor(view, gl.FLOAT, len(arr), atype, mn, mx)

    # -- materials ----------------------------------------------------------
    def material(self, key: str, spec: dict) -> int:
        if key in self._materials:
            return self._materials[key]
        mat = gl.Material(
            name=key,
            pbrMetallicRoughness=gl.PbrMetallicRoughness(
                baseColorFactor=spec["baseColorFactor"],
                metallicFactor=spec["metallic"],
                roughnessFactor=spec["rough"],
            ),
            doubleSided=False,
        )
        if "emissive" in spec:
            mat.emissiveFactor = spec["emissive"]
            mat.extensions = {
                "KHR_materials_emissive_strength": {
                    "emissiveStrength": spec["emissive_strength"]
                }
            }
        self.g.materials.append(mat)
        idx = len(self.g.materials) - 1
        self._materials[key] = idx
        return idx

    # -- geometry -----------------------------------------------------------
    def add_part(self, part: P.Part, anchor_m, verts_local_m) -> int:
        """Add a mesh + node. Returns the node index."""
        mesh = part.mesh
        idx = np.asarray(mesh.faces, dtype=np.uint32).ravel()
        pos = self._floats(verts_local_m, "VEC3", gl.ARRAY_BUFFER, bounds=True)
        nrm = self._floats(np.asarray(mesh.vertex_normals, dtype=np.float32),
                           "VEC3", gl.ARRAY_BUFFER)
        iview = self._view(idx.tobytes(), gl.ELEMENT_ARRAY_BUFFER)
        iacc = self._accessor(iview, gl.UNSIGNED_INT, len(idx), "SCALAR")

        spec = {"steel": _STEEL, "bolt": _BOLT, "weld": _WELD}[part.kind]
        mat = self.material(part.kind, spec)
        self.g.meshes.append(gl.Mesh(name=part.name, primitives=[
            gl.Primitive(attributes=gl.Attributes(POSITION=pos, NORMAL=nrm),
                         indices=iacc, material=mat)
        ]))
        mesh_idx = len(self.g.meshes) - 1

        node = gl.Node(name=part.name, mesh=mesh_idx,
                       translation=list(map(float, anchor_m)))
        self.g.nodes.append(node)
        node_idx = len(self.g.nodes) - 1
        self.g.scenes[0].nodes.append(node_idx)
        return node_idx

    # -- animation ----------------------------------------------------------
    def add_clip(self, name: str, channels: list[tuple[int, str, np.ndarray, np.ndarray]]) -> None:
        anim = gl.Animation(name=name, samplers=[], channels=[])
        for node_idx, path, times, values in channels:
            tin = self._floats(times, "SCALAR", bounds=True)
            atype = "VEC3" if path in ("translation", "scale") else "SCALAR"
            tout = self._floats(values, atype)
            anim.samplers.append(gl.AnimationSampler(input=tin, output=tout,
                                                     interpolation="LINEAR"))
            s = len(anim.samplers) - 1
            anim.channels.append(gl.AnimationChannel(
                sampler=s,
                target=gl.AnimationChannelTarget(node=node_idx, path=path),
            ))
        self.g.animations.append(anim)

    # -- output -------------------------------------------------------------
    def save(self, path: Path) -> None:
        self._pad()
        self.g.buffers = [gl.Buffer(byteLength=len(self.blob))]
        self.g.set_binary_blob(bytes(self.blob))
        self.g.save_binary(str(path))


def _keyframes(seq: int):
    """Return (construct_times, deconstruct_times) for a part's seq order.

    Each is a list of 6 time stamps used with a 6-value parked/seated ramp.
    """
    frac = (max(seq, 1) - 1) / SEQ_MAX
    cs = frac * (T_CONSTRUCT - SEAT)          # construct start
    ce = cs + SEAT                            # seated
    base = T_CONSTRUCT + T_HOLD
    ds = base + (SEQ_MAX - max(seq, 1)) / SEQ_MAX * (T_DECONSTRUCT - SEAT)
    de = ds + SEAT
    end = base + T_DECONSTRUCT
    return np.array([0.0, cs, ce, ds, de, end], dtype=np.float32)


def build(out: Path | None = None) -> Path:
    out = out or Path(__file__).with_name("connection.glb")
    glb = _GLB()
    parts = P.moment_connection()

    node_of: dict[str, int] = {}
    meta: dict[str, dict] = {}
    for part in parts:
        anchor_in = part.mesh.bounds.mean(axis=0)          # bbox centre (in)
        verts_local = (np.asarray(part.mesh.vertices) - anchor_in) * IN_TO_M
        anchor_m = anchor_in * IN_TO_M
        node_of[part.name] = glb.add_part(part, anchor_m, verts_local)
        meta[part.name] = dict(
            anchor=anchor_m,
            parked=(anchor_in + part.explode) * IN_TO_M,
            seq=part.seq,
            kind=part.kind,
        )

    # Build the two clips. translation ramps parked->seated->parked; welds also
    # scale 0->1->0 so they only glow once seated.
    construct: list = []
    deconstruct: list = []
    for name, m in meta.items():
        if np.allclose(m["parked"], m["anchor"]) and m["kind"] != "weld":
            continue  # static anchor (column)
        t = _keyframes(m["seq"])
        home = np.asarray(m["anchor"], np.float32)
        park = np.asarray(m["parked"], np.float32)
        node = node_of[name]

        # construct: parked -> home; deconstruct: home -> parked (reuse ramp,
        # values mirrored). translation values follow the 6 keyframes.
        tr_con = np.array([park, park, home, home, home, home], np.float32)
        tr_dec = np.array([home, home, home, home, park, park], np.float32)
        construct.append((node, "translation", t, tr_con))
        deconstruct.append((node, "translation", t, tr_dec))

        if m["kind"] == "weld":
            on = np.array([1, 1, 1.0], np.float32)
            off = np.zeros(3, np.float32)
            sc_con = np.array([off, off, on, on, on, on], np.float32)
            sc_dec = np.array([on, on, on, on, off, off], np.float32)
            construct.append((node, "scale", t, sc_con))
            deconstruct.append((node, "scale", t, sc_dec))

    glb.add_clip("construct", construct)
    glb.add_clip("deconstruct", deconstruct)
    glb.save(out)
    return out


if __name__ == "__main__":
    path = build()
    size = path.stat().st_size / 1024
    print(f"wrote {path} ({size:.0f} KB)")
