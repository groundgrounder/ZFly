#!/usr/bin/env python3
"""Render the editable SVG into the Linux and macOS application icons."""

from pathlib import Path
import ctypes as c
from ctypes.util import find_library
import shutil
import struct
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 32, 48, 64, 128, 256, 512, 1024)
ICNS = ((b"icp4", 16), (b"icp5", 32), (b"icp6", 64), (b"ic07", 128),
        (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024),
        (b"ic11", 32), (b"ic12", 64), (b"ic13", 256), (b"ic14", 512))


def library_renderer():
    bundled = Path("/Applications/Inkscape.app/Contents/Resources/lib")
    names = (find_library("rsvg-2"), find_library("cairo"), find_library("gobject-2.0"))
    if not all(names) and (bundled / "librsvg-2.2.dylib").is_file():
        names = tuple(str(bundled / name) for name in
                      ("librsvg-2.2.dylib", "libcairo.2.dylib", "libgobject-2.0.0.dylib"))
    if not all(names):
        return None
    try:
        rsvg, cairo, gobject = (c.CDLL(name) for name in names)
    except OSError:
        return None

    class Viewport(c.Structure):
        _fields_ = [(name, c.c_double) for name in ("x", "y", "width", "height")]

    signatures = (
        (rsvg.rsvg_handle_new_from_data, [c.c_char_p, c.c_size_t, c.c_void_p], c.c_void_p),
        (rsvg.rsvg_handle_render_document, [c.c_void_p, c.c_void_p, c.POINTER(Viewport), c.c_void_p], c.c_int),
        (cairo.cairo_image_surface_create, [c.c_int, c.c_int, c.c_int], c.c_void_p),
        (cairo.cairo_create, [c.c_void_p], c.c_void_p),
        (cairo.cairo_surface_write_to_png, [c.c_void_p, c.c_char_p], c.c_int),
        (cairo.cairo_destroy, [c.c_void_p], None),
        (cairo.cairo_surface_destroy, [c.c_void_p], None),
        (gobject.g_object_unref, [c.c_void_p], None),
    )
    for function, arguments, result in signatures:
        function.argtypes, function.restype = arguments, result

    def render(source, destination, size):
        data = source.read_bytes()
        handle = rsvg.rsvg_handle_new_from_data(data, len(data), None)
        if not handle:
            raise RuntimeError(f"Cannot load SVG: {source}")
        surface = cairo.cairo_image_surface_create(0, size, size)
        context = cairo.cairo_create(surface)
        try:
            viewport = Viewport(0, 0, size, size)
            if not rsvg.rsvg_handle_render_document(handle, context, c.byref(viewport), None):
                raise RuntimeError("librsvg could not render the icon")
            if cairo.cairo_surface_write_to_png(surface, bytes(destination)) != 0:
                raise RuntimeError(f"Cannot write PNG: {destination}")
        finally:
            cairo.cairo_destroy(context)
            cairo.cairo_surface_destroy(surface)
            gobject.g_object_unref(handle)
    return render


def main():
    inkscape = shutil.which("inkscape")
    render = library_renderer()
    if not render and not inkscape:
        raise SystemExit("Install librsvg or Inkscape to render assets/icon.svg.")
    output = ROOT / "assets/icons"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="zfly-icons-") as temporary:
        temporary = Path(temporary)
        rendered = {}
        for size in SIZES:
            destination = temporary / f"zfly-{size}.png"
            if render:
                render(ROOT / "assets/icon.svg", destination, size)
            else:
                subprocess.run([
                    inkscape, str(ROOT / "assets/icon.svg"), "--export-type=png",
                    f"--export-filename={destination}", f"--export-width={size}",
                    f"--export-height={size}",
                ], check=True, stdout=subprocess.DEVNULL)
            rendered[size] = destination.read_bytes()
        chunks = []
        for kind, size in ICNS:
            data = rendered[size]
            chunks.append(kind + struct.pack(">I", len(data) + 8) + data)
        payload = b"".join(chunks)
        for size, data in rendered.items():
            (output / f"zfly-{size}.png").write_bytes(data)
        (output / "ZFly.icns").write_bytes(b"icns" + struct.pack(">I", len(payload) + 8) + payload)
    print(f"Rendered {len(SIZES)} PNG sizes and ZFly.icns into {output}")


if __name__ == "__main__":
    main()
