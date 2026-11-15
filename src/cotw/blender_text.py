"""Runs inside Blender: renders thumbnail callouts as extruded, bevelled 3D text on a transparent background.

    blender -b --factory-startup --python blender_text.py -- job.json

`job.json` is {"font": "<ttf>", "jobs": [{"text": "1V2?!", "accent": "#00ACE7", "out": "<png>"}, ...]}.
Each PNG is cropped to the text by the caller; the face is the brand white, the extruded sides the
brand accent (cyan), lit from the upper left with a rim light in the accent colour.
"""

from __future__ import annotations

import json
import math
import sys

import bpy
from mathutils import Vector

PX_PER_UNIT = 220  # render resolution: text 1 unit tall comes out ~220 px
EXTRUDE, BEVEL = 0.16, 0.025
TILT_Y, TILT_X = math.radians(-16), math.radians(9)  # turned so the extruded sides show


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _rgb(hex_color: str) -> tuple[float, float, float, float]:
    h = hex_color.lstrip("#")
    return (*(_srgb_to_linear(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4)), 1.0)


def _material(name: str, rgba, metallic: float, roughness: float, emission: float = 0.0) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    if emission:
        bsdf.inputs["Emission Color"].default_value = rgba
        bsdf.inputs["Emission Strength"].default_value = emission
    return mat


def _reset() -> bpy.types.Scene:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = 64
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"  # keep the brand white white
    world = bpy.data.worlds.new("world")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.02, 0.025, 0.035, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.6
    scene.world = world
    return scene


def _light(scene, name: str, kind: str, energy: float, rgba, location, target=(0, 0, 0), size: float = 4.0) -> None:
    data = bpy.data.lights.new(name, kind)
    data.energy = energy
    data.color = rgba[:3]
    if kind == "AREA":
        data.size = size
    obj = bpy.data.objects.new(name, data)
    obj.location = location
    obj.rotation_euler = (Vector(target) - Vector(location)).to_track_quat("-Z", "Y").to_euler()
    scene.collection.objects.link(obj)


def render(text: str, accent: str, out: str, font_path: str) -> None:
    scene = _reset()
    curve = bpy.data.curves.new("callout", "FONT")
    curve.body = text
    curve.font = bpy.data.fonts.load(font_path, check_existing=True)
    curve.size = 1.0
    curve.extrude = EXTRUDE
    curve.bevel_depth = BEVEL
    curve.bevel_resolution = 3
    curve.align_x = "CENTER"
    curve.align_y = "CENTER"
    obj = bpy.data.objects.new("callout", curve)
    scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.convert(target="MESH")
    obj = bpy.context.view_layer.objects.active

    face = _material("face", _rgb("#F7FAFC"), 0.0, 0.32, emission=0.2)
    side = _material("side", _rgb(accent), 0.35, 0.3, emission=0.25)  # keeps the accent saturated in shadow
    obj.data.materials.append(face)
    obj.data.materials.append(side)
    for poly in obj.data.polygons:  # front faces white, extrusion and bevel in the accent
        poly.material_index = 0 if poly.normal.z > 0.95 else 1
    bpy.ops.object.shade_auto_smooth(angle=math.radians(35))

    obj.rotation_euler = (TILT_X, TILT_Y, 0)
    bpy.context.view_layer.update()

    # orthographic camera on +Z framing the turned text with a margin
    pts = [obj.matrix_world @ v.co for v in obj.data.vertices]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts)))
    pad = 0.25
    w, h = hi.x - lo.x + 2 * pad, hi.y - lo.y + 2 * pad
    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = max(w, h)
    cam = bpy.data.objects.new("cam", cam_data)
    cam.location = ((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, 10)
    scene.collection.objects.link(cam)
    scene.camera = cam
    scene.render.resolution_x = max(64, round(w * PX_PER_UNIT))
    scene.render.resolution_y = max(64, round(h * PX_PER_UNIT))
    scene.render.resolution_percentage = 100

    _light(scene, "key", "AREA", 900, (1, 1, 1, 1), (-6, 5, 8), size=6)
    _light(scene, "fill", "AREA", 250, (0.8, 0.9, 1.0, 1), (6, -2, 6), size=8)
    _light(scene, "rim", "AREA", 1400, _rgb(accent), (5, 4, -3), size=5)

    scene.render.filepath = out
    bpy.ops.render.render(write_still=True)


def main() -> None:
    job = json.loads(open(sys.argv[sys.argv.index("--") + 1], encoding="utf-8").read())
    for item in job["jobs"]:
        render(item["text"], item["accent"], item["out"], job["font"])


if __name__ == "__main__":
    main()
