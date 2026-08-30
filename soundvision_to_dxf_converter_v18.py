#!/usr/bin/env python3
"""
Soundvision to DXF converter v18

Reads encrypted Soundvision .xmlp/.xmls files, decrypts them, extracts native
Surface, Balcony and Revolution room geometry and exports it to DXF.
Optionally merges a Soundvision-exported loudspeaker DXF into the result.

Vectorworks-oriented DXF layer hierarchy:
    SV-Room Geometry-<Soundvision Group>
    SV-Room Geometry-<ungrouped object name>
    SV-Loudspeakers-<original Soundvision DXF layer>

Grouped Surface, Balcony and Revolution objects share the class of their
Soundvision group. Ungrouped objects use their own Soundvision object name as
a direct child class below ``SV-Room Geometry``.

When a loudspeaker DXF is merged, that DXF is used as the base document instead
of being imported into a newly-created DXF. This preserves Soundvision's original
3DFACE colors / edge flags / block structure as closely as possible.


Balcony and Revolution geometry (including circular Revolution mode and the
unchecked Revolution / Perpendicular length mode), both Depth/Height and
Angle/Distance profile coordinate systems, object translation and Init angle
handling are validated against Soundvision 2026.3.1 'Convert to Surfaces'
reference files.

Requirements:
    pip install ezdxf

Optional (recommended for portable decryption without an OpenSSL binary):
    pip install cryptography

macOS: if no input file is supplied, a Finder file chooser opens.
"""

from __future__ import annotations

import argparse
import math
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

try:
    import ezdxf
    import ezdxf.units as dxf_units
    from ezdxf import bbox as dxf_bbox
    from ezdxf import transform as dxf_transform
    from ezdxf.enums import InsertUnits
    from ezdxf.math import Matrix44
except ImportError:
    print("ERROR: ezdxf is missing. Install it in PyCharm with: pip install ezdxf")
    sys.exit(1)

try:
    from cryptography.hazmat.primitives import padding as crypto_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    HAVE_CRYPTOGRAPHY = True
except ImportError:
    HAVE_CRYPTOGRAPHY = False


# =============================================================================
# DXF EXPORT SETTINGS
# =============================================================================
# These three defaults can be changed directly:
EXPORT_FACES = False      # export actual 3D faces (3DFACE)
EXPORT_OUTLINES = True    # export closed 3D outlines
EXPORT_POINTS = False     # export individual vertices

# Vectorworks maps DXF layers most naturally to classes. A dash in a Vectorworks
# class name creates hierarchy levels when hierarchical class display is enabled.
VW_ROOT = ("SV",)
VW_ROOM_GEOMETRY_ROOT = ("SV", "Room Geometry")
VW_LOUDSPEAKER_ROOT = ("SV", "Loudspeakers")
# =============================================================================


# -----------------------------------------------------------------------------
# Soundvision passes the ASCII bytes directly to EVP AES-256-CBC, i.e. AES uses
# the first 32 ASCII bytes of the 64-char key constant and the first 16 ASCII
# bytes of the 32-char IV constant.
#
# PROJECT is confirmed against the supplied 2026.3.1 .xmlp test files.
# GENERIC_XML is used by Soundvision's general encrypted XML reader and is kept
# as a fallback for .xmls / other encrypted XML containers.
# THIRD is another encrypted-file pair present in the binary, kept as fallback.
# -----------------------------------------------------------------------------

CRYPTO_CANDIDATES = [
    (
        "project (.xmlp)",
        b"1A30A6E3DDD33444266BE81ABFC62722",
        b"2FCC9B1869C112AF",
    ),
    (
        "generic encrypted XML (.xmls candidate)",
        b"D463D421C83764CF384D49F4647E2E37",
        b"F5EEE8E4EE2F4D11",
    ),
    (
        "alternate encrypted file",
        b"679281E5A112AB199F8297DD3F742AE4",
        b"622F5005137B24CD",
    ),
]


def choose_file_macos() -> Path | None:
    """Open a native macOS file chooser without requiring tkinter."""
    script = (
        'POSIX path of (choose file with prompt "Select Soundvision .xmlp/.xmls file" '
        'of type {"public.data"})'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    except FileNotFoundError:
        pass
    return None


def choose_dxf_file_macos() -> Path | None:
    """Open a native macOS chooser for an optional Soundvision loudspeaker DXF."""
    script = (
        'POSIX path of (choose file with prompt '
        '"Select the loudspeaker DXF exported by Soundvision" '
        'of type {"public.data"})'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    except FileNotFoundError:
        pass
    return None


def choose_optional_loudspeaker_dxf_macos() -> Path | None:
    """Ask whether a Soundvision loudspeaker DXF should be merged."""
    script = (
        'display dialog "Merge a loudspeaker DXF exported by Soundvision?" '
        'with title "Soundvision to DXF converter" '
        'buttons {"No", "Choose DXF"} default button "No"\n'
        'return button returned of result'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return None

    if result.returncode != 0 or result.stdout.strip() != "Choose DXF":
        return None
    return choose_dxf_file_macos()


def choose_export_options_macos() -> tuple[bool, bool, bool] | None:
    """Open the native macOS selector for faces, outlines and vertices."""
    items = [
        "3D Faces (3DFACE)",
        "3D Outlines (Polylines)",
        "Vertices",
    ]
    defaults = []
    if EXPORT_FACES:
        defaults.append(items[0])
    if EXPORT_OUTLINES:
        defaults.append(items[1])
    if EXPORT_POINTS:
        defaults.append(items[2])

    def apple_list(values):
        escaped = [v.replace('\\', '\\\\').replace('"', '\\"') for v in values]
        return "{" + ", ".join(f'"{v}"' for v in escaped) + "}"

    script = f"""
set picked to choose from list {apple_list(items)} with title "Soundvision to DXF converter" with prompt "What should be exported to the DXF?" default items {apple_list(defaults)} with multiple selections allowed and empty selection allowed
if picked is false then return "__CANCEL__"
set AppleScript's text item delimiters to "|"
return picked as text
"""

    result = subprocess.run(
        ["osascript", "-e", script],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None

    raw = result.stdout.strip()
    if raw == "__CANCEL__":
        return None

    selected = set(raw.split("|")) if raw else set()
    return (
        items[0] in selected,
        items[1] in selected,
        items[2] in selected,
    )


def get_export_options() -> tuple[bool, bool, bool]:
    if sys.platform == "darwin":
        selected = choose_export_options_macos()
        if selected is not None:
            return selected
    return EXPORT_FACES, EXPORT_OUTLINES, EXPORT_POINTS


def parse_cli_args() -> argparse.Namespace:
    """Parse optional command-line arguments while preserving the macOS UI flow."""
    parser = argparse.ArgumentParser(
        description="Convert Soundvision room geometry to a Vectorworks-friendly DXF."
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Soundvision .xmlp/.xmls file. Omit on macOS to use the file chooser.",
    )
    parser.add_argument(
        "--merge-dxf",
        dest="merge_dxf",
        help="Optional loudspeaker DXF exported directly by Soundvision.",
    )
    parser.add_argument(
        "--no-merge-prompt",
        action="store_true",
        help="Do not ask for an optional loudspeaker DXF in the macOS UI.",
    )
    return parser.parse_args()


def get_input_path(cli_input: str | None = None) -> Path:
    if cli_input:
        return Path(cli_input).expanduser().resolve()

    if sys.platform == "darwin":
        selected = choose_file_macos()
        if selected:
            return selected.resolve()

    raw = input("Path to Soundvision file: ").strip().strip('"')
    return Path(raw).expanduser().resolve()


def get_loudspeaker_dxf_path(
    cli_path: str | None,
    no_merge_prompt: bool = False,
) -> Path | None:
    """Resolve the optional Soundvision loudspeaker DXF to merge."""
    if cli_path:
        return Path(cli_path).expanduser().resolve()

    if sys.platform == "darwin" and not no_merge_prompt:
        selected = choose_optional_loudspeaker_dxf_macos()
        if selected:
            return selected.expanduser().resolve()

    return None


def openssl_path() -> str | None:
    """Return an OpenSSL executable if one is available."""
    return shutil.which("openssl")


def _try_decrypt_cryptography(
    ciphertext: bytes, key_ascii: bytes, iv_ascii: bytes
) -> bytes | None:
    """Decrypt AES-256-CBC/PKCS#7 using the Python cryptography package."""
    if not HAVE_CRYPTOGRAPHY:
        return None

    try:
        decryptor = Cipher(
            algorithms.AES(key_ascii),
            modes.CBC(iv_ascii),
        ).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()

        unpadder = crypto_padding.PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()
    except Exception:
        return None


def _try_decrypt_openssl(
    ciphertext: bytes, key_ascii: bytes, iv_ascii: bytes
) -> bytes | None:
    """Decrypt AES-256-CBC/PKCS#7 with OpenSSL as a compatibility fallback."""
    exe = openssl_path()
    if not exe:
        return None

    cmd = [
        exe,
        "enc",
        "-d",
        "-aes-256-cbc",
        "-K",
        key_ascii.hex(),
        "-iv",
        iv_ascii.hex(),
    ]
    result = subprocess.run(cmd, input=ciphertext, capture_output=True, check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def try_decrypt(ciphertext: bytes, key_ascii: bytes, iv_ascii: bytes) -> bytes | None:
    """Decrypt a Soundvision encrypted XML container.

    Prefer in-process AES via ``cryptography``. This avoids relying on a
    particular OpenSSL installation and is also the path used by the packaged
    macOS application. OpenSSL remains as a fallback for existing Python setups.
    """
    clear = _try_decrypt_cryptography(ciphertext, key_ascii, iv_ascii)
    if clear is not None:
        return clear
    return _try_decrypt_openssl(ciphertext, key_ascii, iv_ascii)

def looks_like_xml(data: bytes) -> bool:
    stripped = data.lstrip(b"\xef\xbb\xbf\x00\t\r\n ")
    return stripped.startswith(b"<?xml") or stripped.startswith(b"<")


def decrypt_soundvision(path: Path) -> tuple[bytes, str]:
    raw = path.read_bytes()

    # Allow plaintext XML as well; useful while debugging or if Soundvision ever
    # exposes an unencrypted variant.
    if looks_like_xml(raw):
        try:
            ET.fromstring(raw)
            return raw, "plaintext XML"
        except ET.ParseError:
            pass

    for label, key, iv in CRYPTO_CANDIDATES:
        clear = try_decrypt(raw, key, iv)
        if clear is None or not looks_like_xml(clear):
            continue
        try:
            ET.fromstring(clear)
        except ET.ParseError:
            continue
        return clear, label

    raise RuntimeError(
        "The file could not be decrypted."
        "Sorry."
    )


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def child_text(parent: ET.Element, name: str) -> str | None:
    for child in list(parent):
        if local_name(child.tag) == name:
            return (child.text or "").strip()
    return None


def find_child(parent: ET.Element, name: str) -> ET.Element | None:
    for child in list(parent):
        if local_name(child.tag) == name:
            return child
    return None


def parse_xyz(text: str) -> tuple[float, float, float]:
    values = text.replace(",", ".").split()
    if len(values) < 3:
        raise ValueError(f"Invalid position: {text!r}")

    # Soundvision serializes scene coordinates in its internal 3D frame.
    # The Properties panel / CAD-facing frame used by the user is:
    #   UI X = -internal X
    #   UI Y =  internal Z
    #   UI Z =  internal Y
    # This mapping is confirmed by the supplied 10 m / 20 m probe files.
    ix, iy, iz = float(values[0]), float(values[1]), float(values[2])
    return -ix, iz, iy


def parse_numbers(text: str) -> list[float]:
    return [float(v) for v in text.replace(",", ".").split()]


def parse_profile_points(element: ET.Element) -> list[tuple[float, float]]:
    """Read a Balcony/Revolution profile as normalized Depth/Height points.

    Soundvision stores native Balcony and Revolution profiles in one of two
    coordinate systems:

    * ``coordinate_system == 1``: Depth / Height. The two values in each
      ``<position>`` are already the local profile depth and height.

    * ``coordinate_system == 2``: Angle / Distance. The first value is an
      elevation angle in degrees and the second value is the distance from the
      Observer position. Soundvision converts these to Depth/Height as::

          depth  = observer_depth  + distance * cos(angle)
          height = observer_height + distance * sin(angle)

    The Angle/Distance conversion is validated against Soundvision 2026.3.1
    ``Convert to Surfaces`` reference projects for both Balcony and Revolution.
    Returning a single normalized representation keeps all downstream geometry
    generation identical for both UI coordinate modes.
    """
    points_node = find_child(element, "points")
    if points_node is None:
        return []

    try:
        coordinate_system = int(child_text(element, "coordinate_system") or "1")
    except ValueError as exc:
        raise ValueError("Invalid Soundvision profile coordinate_system value") from exc

    if coordinate_system not in (1, 2):
        raise ValueError(
            f"Unsupported Soundvision profile coordinate system: {coordinate_system}. "
            "Supported values are 1 (Depth/Height) and 2 (Angle/Distance)."
        )

    observer_depth = 0.0
    observer_height = 0.0
    if coordinate_system == 2:
        observer_values = parse_numbers(child_text(element, "observer") or "0 0")
        if observer_values:
            observer_depth = observer_values[0]
        if len(observer_values) >= 2:
            observer_height = observer_values[1]

    result: list[tuple[float, float]] = []
    for point_node in list(points_node):
        if local_name(point_node.tag) != "point":
            continue
        position = child_text(point_node, "position")
        if not position:
            continue
        values = parse_numbers(position)
        if len(values) < 2:
            continue

        if coordinate_system == 2:
            angle_deg, distance = values[0], values[1]
            angle_rad = math.radians(angle_deg)
            depth = observer_depth + distance * math.cos(angle_rad)
            height = observer_height + distance * math.sin(angle_rad)
            result.append((depth, height))
        else:
            # Depth/Height (coordinate_system == 1).
            result.append((values[0], values[1]))

    return result


def parse_init_transform(element: ET.Element) -> tuple[float, tuple[float, float, float]]:
    """Return object rotation in degrees and internal XYZ translation."""
    angle_text = child_text(element, "init_angle") or "0"
    pos_text = child_text(element, "init_position") or "0 0 0"
    angle = float(angle_text.replace(",", "."))
    p = parse_numbers(pos_text)
    while len(p) < 3:
        p.append(0.0)
    return angle, (p[0], p[1], p[2])


def apply_internal_transform(
    point: tuple[float, float, float],
    angle_deg: float,
    translation: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Apply Soundvision object's local top-view rotation + translation.

    Verified against transformed Soundvision 2026.3.1 Balcony/Revolution
    reference projects, including non-zero Init X/Y/Z, 30 degree Init angle,
    multiple profile segments and different discretization values.
    """
    x, y, z = point
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    # Standard right-handed rotation around internal +Y (vertical).
    xr = x * ca + z * sa
    zr = -x * sa + z * ca
    tx, ty, tz = translation
    return xr + tx, y + ty, zr + tz


def internal_to_ui(point: tuple[float, float, float]) -> tuple[float, float, float]:
    """Convert Soundvision internal XYZ to the CAD/UI XYZ frame."""
    ix, iy, iz = point
    return -ix, iz, iy


def make_patch(
    name: str,
    internal_points: list[tuple[float, float, float]],
    angle_deg: float,
    translation: tuple[float, float, float],
    source_type: str,
    source_object_name: str | None = None,
) -> dict:
    transformed = [
        internal_to_ui(apply_internal_transform(p, angle_deg, translation))
        for p in internal_points
    ]
    return {
        "name": name,
        "points": transformed,
        "point_names": [f"Point {i}" for i in range(1, len(transformed) + 1)],
        "source_type": source_type,
        "source_object_name": source_object_name or name,
    }


def extract_explicit_surface_element(
    element: ET.Element,
    fallback_index: int = 1,
    group_path: tuple[str, ...] = (),
) -> dict | None:
    """Extract one explicit Soundvision <surface> element."""
    points_node = find_child(element, "points")
    if points_node is None:
        return None

    name = child_text(element, "name") or f"Surface {fallback_index}"
    points: list[tuple[float, float, float]] = []
    point_names: list[str] = []

    for point_node in list(points_node):
        if local_name(point_node.tag) != "point":
            continue
        position = child_text(point_node, "position")
        if not position:
            continue
        try:
            xyz = parse_xyz(position)
        except ValueError:
            continue
        points.append(xyz)
        point_names.append(child_text(point_node, "name") or f"Point {len(points)}")

    if len(points) < 3:
        return None

    return {
        "name": name,
        "points": points,
        "point_names": point_names,
        "source_type": "Surface",
        "source_object_name": name,
        "group_path": list(group_path),
    }


def extract_explicit_surfaces(root: ET.Element) -> list[dict]:
    """Compatibility extractor for all explicit Surface objects in a document."""
    surfaces: list[dict] = []
    for element in root.iter():
        if local_name(element.tag) != "surface":
            continue
        surface = extract_explicit_surface_element(
            element,
            fallback_index=len(surfaces) + 1,
        )
        if surface is not None:
            surfaces.append(surface)
    return surfaces


def generate_balcony(element: ET.Element) -> list[dict]:
    """Reconstruct a native Soundvision Balcony as the same quad patches
    produced by Soundvision's 'Convert to Surfaces' command.
    """
    name = child_text(element, "name") or "Balcony"
    profile = parse_profile_points(element)
    if len(profile) < 2:
        return []

    try:
        front_width = float((child_text(element, "front_width") or "0").replace(",", "."))
        rear_width = float((child_text(element, "rear_width") or "0").replace(",", "."))
        discretization = max(1, int(child_text(element, "discretization") or "1"))
    except ValueError:
        return []

    d0 = profile[0][0]
    d1 = profile[-1][0]
    depth_span = d1 - d0
    width_delta = rear_width - front_width
    eps = 1e-9

    # Soundvision has one important special case: a Balcony with
    # Discretization = 1 is converted to a single flat trapezoid, even when
    # front and rear widths differ. Only discretization values greater than 1
    # use the concentric-arc construction.
    curved = (
        discretization > 1
        and abs(width_delta) > eps
        and abs(depth_span) > eps
    )
    if curved:
        # Concentric-arc geometry. For two chord widths Wf/Wr separated by
        # radial distance D: sin(a/2)=(Wr-Wf)/(2D), R0=Wf/(2*sin(a/2)).
        sin_half = width_delta / (2.0 * depth_span)
        if abs(sin_half) > 1.0 + 1e-7:
            return []
        sin_half = max(-1.0, min(1.0, sin_half))
        total_angle = 2.0 * math.asin(sin_half)
        if abs(sin_half) < eps:
            curved = False
        else:
            base_radius = front_width / (2.0 * sin_half)

    angle_deg, translation = parse_init_transform(element)
    patches: list[dict] = []

    def local_point(depth: float, height: float, j: int) -> tuple[float, float, float]:
        t = j / discretization
        if curved:
            theta = -total_angle / 2.0 + total_angle * t
            radius = base_radius + (depth - d0)
            x = radius * math.sin(theta)
            z = radius * math.cos(theta) - base_radius
        else:
            # Parallel front/rear widths are the infinite-radius limit: a flat
            # strip subdivided across its width. Width is linearly interpolated
            # along profile depth so this also handles intermediate profile points.
            if abs(depth_span) > eps:
                u = (depth - d0) / depth_span
            else:
                u = 0.0
            width = front_width + (rear_width - front_width) * u
            x = -width / 2.0 + width * t
            z = depth - d0
        return x, height, z

    for profile_index in range(len(profile) - 1):
        da, ha = profile[profile_index]
        db, hb = profile[profile_index + 1]
        for segment in range(discretization):
            internal_points = [
                local_point(da, ha, segment),
                local_point(db, hb, segment),
                local_point(db, hb, segment + 1),
                local_point(da, ha, segment + 1),
            ]
            patches.append(
                make_patch(
                    f"{name} ({profile_index + 1}, {segment + 1})",
                    internal_points,
                    angle_deg,
                    translation,
                    "Balcony",
                    name,
                )
            )

    return patches


def generate_revolution(element: ET.Element) -> list[dict]:
    """Reconstruct a native Soundvision Revolution as surface patches.

    Both Soundvision construction modes are supported:

    * Revolution enabled (circular_cone == 1):
      Each profile depth is used directly as a circular radius.

    * Revolution disabled (circular_cone == 0):
      Soundvision uses ``Perpendicular length`` to scale the perpendicular
      semi-axis of the outermost profile point. Intermediate profile depths
      are scaled proportionally, producing the same elliptical patches as
      Soundvision's ``Convert to Surfaces`` command.

    Both modes, including object translation, Init angle, multiple profile
    segments and discretization, are validated against Soundvision 2026.3.1
    reference projects supplied by the user.
    """
    name = child_text(element, "name") or "Revolution"
    profile = parse_profile_points(element)
    if len(profile) < 2:
        return []

    try:
        circular = int(child_text(element, "circular_cone") or "0")
        described_angle = float((child_text(element, "angle") or "0").replace(",", "."))
        discretization = max(1, int(child_text(element, "discretization") or "1"))
    except ValueError:
        return []

    perpendicular_scale = 1.0

    if circular != 1:
        try:
            perpendicular_length = float(
                (child_text(element, "length") or "0").replace(",", ".")
            )
        except ValueError:
            return []

        # In Soundvision's non-circular mode, Perpendicular length is the
        # perpendicular semi-axis at the last profile depth. Other profile
        # depths scale proportionally from the origin.
        reference_depth = profile[-1][0]

        # Normal Soundvision profiles end at a non-zero depth. Keep a robust
        # fallback for unusual profiles where the final point is at depth 0.
        if abs(reference_depth) < 1e-12:
            reference_depth = max((abs(depth) for depth, _ in profile), default=0.0)

        if abs(reference_depth) < 1e-12:
            return []

        perpendicular_scale = perpendicular_length / reference_depth

    total_angle = math.radians(described_angle)
    start_angle = -total_angle / 2.0
    angle_deg, translation = parse_init_transform(element)
    patches: list[dict] = []

    def local_point(depth: float, height: float, j: int) -> tuple[float, float, float]:
        theta = start_angle + total_angle * (j / discretization)

        if circular == 1:
            x = depth * math.sin(theta)
        else:
            x = depth * perpendicular_scale * math.sin(theta)

        z = depth * math.cos(theta)
        return x, height, z

    for profile_index in range(len(profile) - 1):
        depth_a, height_a = profile[profile_index]
        depth_b, height_b = profile[profile_index + 1]

        for segment in range(discretization):
            internal_points = [
                local_point(depth_a, height_a, segment),
                local_point(depth_b, height_b, segment),
                local_point(depth_b, height_b, segment + 1),
                local_point(depth_a, height_a, segment + 1),
            ]
            patches.append(
                make_patch(
                    f"{name} ({profile_index + 1}, {segment + 1})",
                    internal_points,
                    angle_deg,
                    translation,
                    "Revolution",
                    name,
                )
            )

    return patches


def _set_group_path(items: list[dict], group_path: tuple[str, ...]) -> list[dict]:
    for item in items:
        item["group_path"] = list(group_path)
    return items


def extract_geometry(xml_data: bytes) -> tuple[list[dict], dict[str, int]]:
    """Extract room geometry and preserve Soundvision scene-group membership."""
    root = ET.fromstring(xml_data)
    geometry: list[dict] = []
    counts = {"Surface": 0, "Balcony": 0, "Revolution": 0}

    scene = next((e for e in root.iter() if local_name(e.tag) == "scene"), None)
    scene_children = find_child(scene, "children") if scene is not None else None
    group_tags = {"group", "scene_group", "scenegroup"}

    def process_child(element: ET.Element, group_path: tuple[str, ...]) -> None:
        tag = local_name(element.tag)

        if tag in group_tags:
            group_name = child_text(element, "name") or "Group"
            next_path = group_path + (group_name,)
            child_container = find_child(element, "children")
            if child_container is not None:
                for child in list(child_container):
                    process_child(child, next_path)
            else:
                for child in list(element):
                    if local_name(child.tag) in group_tags | {
                        "surface", "balcony", "revolution"
                    }:
                        process_child(child, next_path)
            return

        if tag == "surface":
            surface = extract_explicit_surface_element(
                element,
                fallback_index=counts["Surface"] + 1,
                group_path=group_path,
            )
            if surface is not None:
                geometry.append(surface)
                counts["Surface"] += 1
            return

        if tag == "balcony":
            generated = _set_group_path(generate_balcony(element), group_path)
            geometry.extend(generated)
            if generated:
                counts["Balcony"] += 1
            return

        if tag == "revolution":
            generated = _set_group_path(generate_revolution(element), group_path)
            geometry.extend(generated)
            if generated:
                counts["Revolution"] += 1
            return

        if tag == "children":
            for child in list(element):
                process_child(child, group_path)

    if scene_children is not None:
        for child in list(scene_children):
            process_child(child, ())

    if not geometry:
        geometry = extract_explicit_surfaces(root)
        counts["Surface"] = len(geometry)
        for element in root.iter():
            tag = local_name(element.tag)
            if tag == "balcony":
                generated = _set_group_path(generate_balcony(element), ())
                geometry.extend(generated)
                if generated:
                    counts["Balcony"] += 1
            elif tag == "revolution":
                generated = _set_group_path(generate_revolution(element), ())
                geometry.extend(generated)
                if generated:
                    counts["Revolution"] += 1

    return geometry, counts


def sanitize_layer_name(name: str, fallback: str) -> str:
    # DXF layer names may not contain these characters.
    cleaned = re.sub(r'[<>/\\":;?*|=,]', "_", name).strip()
    cleaned = cleaned[:200]
    return cleaned or fallback


def unique_layer_name(doc, desired: str) -> str:
    base = desired
    candidate = base
    i = 2
    while candidate in doc.layers:
        candidate = f"{base}_{i}"
        i += 1
    return candidate


def sanitize_class_component(name: str, fallback: str) -> str:
    """Sanitize one Vectorworks class hierarchy component."""
    cleaned = sanitize_layer_name(name, fallback).replace("-", "_").strip()
    return cleaned or fallback


def build_hierarchical_layer(
    root_parts: tuple[str, ...],
    child_parts: Iterable[str] = (),
) -> str:
    """Build a Vectorworks-friendly dash-separated class hierarchy."""
    root = [sanitize_class_component(p, "SV") for p in root_parts]
    children = [
        sanitize_class_component(str(p), f"Group_{i}")
        for i, p in enumerate(child_parts, start=1)
        if str(p).strip()
    ]
    parts = root + children
    if len(parts) > 4:
        parts = parts[:3] + ["_".join(parts[3:])]
    return "-".join(parts)


def geometry_layer_for(surface: dict) -> str:
    """Return the Vectorworks class/layer for one room-geometry patch.

    The hierarchy intentionally does *not* distinguish Surface, Balcony and
    Revolution objects. Soundvision group membership has priority: every object
    inside the same Soundvision group is exported to that group's Vectorworks
    class. If an object is not inside a Soundvision group, its own Soundvision
    object name becomes the class directly below ``SV-Room Geometry``.
    """
    source_type = str(surface.get("source_type", "Surface"))
    group_path = tuple(surface.get("group_path", ()))
    object_name = str(
        surface.get("source_object_name")
        or surface.get("name")
        or source_type
    )

    if group_path:
        return build_hierarchical_layer(VW_ROOM_GEOMETRY_ROOT, group_path)

    return build_hierarchical_layer(VW_ROOM_GEOMETRY_ROOT, (object_name,))


def ensure_layer(doc, name: str, *, color: int = 7, linetype: str = "Continuous") -> None:
    if name not in doc.layers:
        doc.layers.add(name, dxfattribs={"color": color, "linetype": linetype})


def ensure_hierarchy_layers(doc, name: str) -> None:
    """Create all dash-separated Vectorworks parent classes as DXF layers."""
    parts = name.split("-")
    for i in range(1, len(parts) + 1):
        ensure_layer(doc, "-".join(parts[:i]))


def _all_block_entities(doc):
    """Yield every graphical entity stored in model/paper space and blocks once."""
    for block in doc.blocks:
        for entity in block:
            yield entity


def _speaker_referenced_layers(source_doc) -> set[str]:
    layers: set[str] = set()
    for entity in _all_block_entities(source_doc):
        if entity.dxf.hasattr("layer"):
            layer = str(entity.dxf.layer)
            if layer:
                layers.add(layer)
    return layers


def _copy_layer_appearance(source_doc, old_name: str) -> tuple[int, str]:
    """Return basic layer appearance, falling back for Soundvision's implicit layers."""
    if old_name in source_doc.layers:
        old = source_doc.layers.get(old_name)
        color = int(old.dxf.get("color", 7) or 7)
        linetype = str(old.dxf.get("linetype", "Continuous") or "Continuous")
        return color, linetype
    return 7, "Continuous"


def _prepare_loudspeaker_source_layers(source_doc) -> dict[str, str]:
    """Put *all referenced* Soundvision loudspeaker layers below SV-Loudspeakers.

    Soundvision's R12 loudspeaker DXF can reference layers such as
    ``L_Acoustics_K3`` only from entities inside BLOCK definitions without
    actually listing those names in the DXF LAYER table. v12 only renamed the
    LAYER table, so these implicit layers escaped the Vectorworks hierarchy.
    v13 discovers entity layer references recursively and remaps them too.
    """
    ensure_hierarchy_layers(source_doc, build_hierarchical_layer(VW_LOUDSPEAKER_ROOT))

    mapping: dict[str, str] = {}
    referenced = sorted(_speaker_referenced_layers(source_doc), key=str.casefold)
    for old_name in referenced:
        if old_name.lower() in {"0", "defpoints"}:
            continue
        new_name = build_hierarchical_layer(VW_LOUDSPEAKER_ROOT, (old_name,))
        color, linetype = _copy_layer_appearance(source_doc, old_name)
        ensure_hierarchy_layers(source_doc, new_name)
        # DXF layer names are case-insensitive. Normalize spelling/case to the
        # actual layer-table entry so FullSystem/Fullsystem cannot split in VW.
        layer = source_doc.layers.get(new_name)
        new_name = layer.dxf.name
        mapping[old_name] = new_name
        # Preserve any real source-layer appearance when it exists.
        try:
            layer.dxf.color = color
            layer.dxf.linetype = linetype
        except Exception:
            pass

    for entity in _all_block_entities(source_doc):
        if not entity.dxf.hasattr("layer"):
            continue
        old_name = str(entity.dxf.layer)
        if old_name in mapping:
            entity.dxf.layer = mapping[old_name]

    return mapping


def _speaker_unit_scale_to_meters(source_doc) -> tuple[float, str]:
    """Return a uniform scale from source DXF insertion units to metres."""
    raw_units = int(source_doc.header.get("$INSUNITS", 0) or 0)
    if raw_units == int(InsertUnits.Unitless):
        return 1.0, "unitless (assumed metres)"
    try:
        units = InsertUnits(raw_units)
        scale = dxf_units.conversion_factor(units, InsertUnits.Meters)
        return float(scale), units.name
    except (ValueError, TypeError, IndexError):
        return 1.0, f"unknown unit code {raw_units} (assumed metres)"


def _speaker_entity_count(doc) -> int:
    return sum(1 for _ in _all_block_entities(doc))


def prepare_loudspeaker_base_dxf(speaker_dxf_path: Path):
    """Load Soundvision's loudspeaker DXF as the output base document.

    Keeping the source DXF as the base preserves its BLOCK hierarchy and the
    original per-face ACI colors/invisible-edge flags instead of copying those
    entities through a cross-document importer.
    """
    if not speaker_dxf_path.exists():
        raise FileNotFoundError(f"Loudspeaker DXF not found: {speaker_dxf_path}")

    doc = ezdxf.readfile(speaker_dxf_path)
    source_dxf_version = doc.dxfversion
    scale, source_units = _speaker_unit_scale_to_meters(doc)

    # Scaling modelspace entities scales INSERT locations and insert scales,
    # therefore nested Soundvision block geometry follows without modifying
    # the block definitions or their visual attributes.
    if not math.isclose(scale, 1.0, rel_tol=0.0, abs_tol=1e-15):
        entities = list(doc.modelspace())
        if entities:
            dxf_transform.inplace(entities, Matrix44.scale(scale))

    layer_mapping = _prepare_loudspeaker_source_layers(doc)

    # IMPORTANT: Preserve the exact DXF version exported by Soundvision.
    #
    # Soundvision currently exports its loudspeaker geometry as R12 (AC1009).
    # Vectorworks imports that R12 representation with the expected face
    # appearance. v13/v14 upgraded the document to R2018 before saving; although
    # the 3DFACE ACI colours and edge flags survived numerically, Vectorworks
    # rendered the speaker faces differently afterwards.
    #
    # All room entities created by this converter (3DFACE, POLYLINE, POINT) and
    # the class/layer hierarchy are R12-compatible, so there is no reason to
    # change the source version. Keep the Soundvision DXF as intact as possible.
    doc.header["$INSUNITS"] = int(InsertUnits.Meters)

    return doc, {
        "path": str(speaker_dxf_path),
        "entities": _speaker_entity_count(doc),
        "renamed_layers": len(layer_mapping),
        "source_units": source_units,
        "scale_to_meters": scale,
        "source_dxf_version": source_dxf_version,
        "output_dxf_version": doc.dxfversion,
    }

def _project_polygon_to_2d(points: list[tuple[float, float, float]]) -> list[tuple[float, float]]:
    """Project an approximately planar 3D polygon onto its dominant 2D plane."""
    nx = ny = nz = 0.0
    for i, (x1, y1, z1) in enumerate(points):
        x2, y2, z2 = points[(i + 1) % len(points)]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)

    ax, ay, az = abs(nx), abs(ny), abs(nz)
    if ax >= ay and ax >= az:
        return [(y, z) for x, y, z in points]
    if ay >= az:
        return [(x, z) for x, y, z in points]
    return [(x, y) for x, y, z in points]


def _signed_area_2d(points: list[tuple[float, float]]) -> float:
    return 0.5 * sum(
        x1 * y2 - x2 * y1
        for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])
    )


def _point_in_triangle(p, a, b, c, eps=1e-12) -> bool:
    def cross(o, u, v):
        return (u[0] - o[0]) * (v[1] - o[1]) - (u[1] - o[1]) * (v[0] - o[0])

    c1 = cross(a, b, p)
    c2 = cross(b, c, p)
    c3 = cross(c, a, p)
    has_neg = c1 < -eps or c2 < -eps or c3 < -eps
    has_pos = c1 > eps or c2 > eps or c3 > eps
    return not (has_neg and has_pos)


def triangulate_polygon(points: list[tuple[float, float, float]]) -> list[tuple[int, int, int]]:
    """Ear-clipping triangulation. Preserves the original polygon orientation."""
    n = len(points)
    if n < 3:
        return []
    if n == 3:
        return [(0, 1, 2)]

    p2 = _project_polygon_to_2d(points)
    orientation = 1.0 if _signed_area_2d(p2) >= 0 else -1.0
    remaining = list(range(n))
    triangles: list[tuple[int, int, int]] = []
    eps = 1e-12

    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    guard = 0
    while len(remaining) > 3 and guard < n * n:
        guard += 1
        ear_found = False
        m = len(remaining)
        for j in range(m):
            i_prev = remaining[(j - 1) % m]
            i_curr = remaining[j]
            i_next = remaining[(j + 1) % m]
            a, b, c = p2[i_prev], p2[i_curr], p2[i_next]

            if orientation * cross(a, b, c) <= eps:
                continue

            if any(
                _point_in_triangle(p2[k], a, b, c)
                for k in remaining
                if k not in (i_prev, i_curr, i_next)
            ):
                continue

            triangles.append((i_prev, i_curr, i_next))
            del remaining[j]
            ear_found = True
            break

        if not ear_found:
            # Degenerate/self-intersecting polygon: fall back to a fan so the
            # export still completes instead of dropping the surface entirely.
            return [(0, i, i + 1) for i in range(1, n - 1)]

    if len(remaining) == 3:
        triangles.append(tuple(remaining))
    return triangles


def _points_are_close(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
    tolerance: float = 1e-9,
) -> bool:
    return math.dist(a, b) <= tolerance


def normalize_polygon_points(
    points: Iterable[tuple[float, float, float]],
    tolerance: float = 1e-9,
) -> list[tuple[float, float, float]]:
    """Remove redundant consecutive/closing vertices without changing shape.

    Soundvision Revolutions whose profile reaches the rotation axis naturally
    produce patches such as ``centre -> outer A -> outer B -> centre``. That is
    geometrically a triangle, but exporting all four vertices creates a
    degenerate DXF 3DFACE. Converting it to three unique vertices is cleaner for
    Vectorworks while preserving exactly the same geometry.
    """
    clean: list[tuple[float, float, float]] = []
    for point in points:
        if not clean or not _points_are_close(clean[-1], point, tolerance):
            clean.append(point)

    if len(clean) > 1 and _points_are_close(clean[0], clean[-1], tolerance):
        clean.pop()

    return clean


def validate_geometry(surfaces: Iterable[dict]) -> None:
    """Fail early if generated geometry contains non-finite or collapsed data.

    Soundvision's converted Surface coordinates are serialized from 32-bit
    floating-point values (the reference files use nine digits after the
    decimal point in scientific notation).  The converter intentionally keeps
    Python double-precision calculations instead of globally quantizing to
    float32: across the supplied Soundvision 2026.3.1 reference projects this
    gives the most consistent geometric match, while avoiding unnecessary
    precision loss in DXF.
    """
    for index, surface in enumerate(surfaces, start=1):
        name = surface.get("name", f"Surface {index}")
        points = list(surface.get("points", []))
        if not points:
            raise ValueError(f"{name}: no vertices were generated")
        for vertex_index, point in enumerate(points, start=1):
            if len(point) != 3 or not all(math.isfinite(v) for v in point):
                raise ValueError(
                    f"{name}: invalid vertex {vertex_index}: {point!r}"
                )
        if len(normalize_polygon_points(points)) < 3:
            raise ValueError(
                f"{name}: fewer than three unique vertices were generated"
            )




def _add_vectorworks_origin_extent_anchor(doc) -> dict | None:
    """Make the actual XY drawing extents symmetric around Soundvision WCS 0/0.

    Vectorworks can centre a first DXF import around its internal origin. For
    an asymmetric room this places the Vectorworks internal origin at the
    geometry bounding-box centre instead of Soundvision 0/0.

    No project geometry is translated. Two tiny 0.1 mm construction lines are
    placed on the existing R12 ``Defpoints`` layer at opposite symmetric XY
    extents. They make the imported file's real geometric centre exactly 0/0.
    """
    try:
        ext = dxf_bbox.extents(doc.modelspace(), fast=False)
    except Exception:
        return None

    if not ext.has_data:
        return None

    xmin, ymin, zmin = map(float, ext.extmin)
    xmax, ymax, zmax = map(float, ext.extmax)
    values = (xmin, ymin, zmin, xmax, ymax, zmax)
    if not all(math.isfinite(v) for v in values):
        return None

    center_x = (xmin + xmax) * 0.5
    center_y = (ymin + ymax) * 0.5
    bound_x = max(abs(xmin), abs(xmax))
    bound_y = max(abs(ymin), abs(ymax))

    if abs(center_x) <= 1e-9 and abs(center_y) <= 1e-9:
        return {
            "added": False,
            "before_center": (center_x, center_y),
            "symmetric_x": bound_x,
            "symmetric_y": bound_y,
        }

    ensure_layer(doc, "Defpoints")
    msp = doc.modelspace()
    epsilon = 0.0001  # 0.1 mm

    # Opposite tiny LINE entities are used instead of POINT entities because
    # CAD importers reliably include LINE entities when calculating extents.
    msp.add_line(
        (-bound_x, -bound_y, 0.0),
        (-bound_x + epsilon, -bound_y, 0.0),
        dxfattribs={"layer": "Defpoints"},
    )
    msp.add_line(
        (bound_x, bound_y, 0.0),
        (bound_x - epsilon, bound_y, 0.0),
        dxfattribs={"layer": "Defpoints"},
    )

    return {
        "added": True,
        "before_center": (center_x, center_y),
        "symmetric_x": bound_x,
        "symmetric_y": bound_y,
        "epsilon": epsilon,
    }

def _restore_vectorworks_r12_origin_header(output_path: Path, origin_anchor: dict | None = None) -> None:
    """Restore a minimal Soundvision-style R12 HEADER after ezdxf saves the file.

    Soundvision's loudspeaker DXF is an R12/AC1009 file with a deliberately
    minimal HEADER that still carries ``$INSUNITS = 6`` (metres).  ezdxf can
    read this non-standard R12 header, but when it re-saves AC1009 it drops
    ``$INSUNITS`` and writes placeholder EXTMIN/EXTMAX values of +/-1e20.

    Vectorworks then treats the merged drawing differently during import even
    though all entity XYZ coordinates are unchanged.  In particular, the
    drawing's 0/0/0 reference no longer matches the non-merged room export.

    Keep the R12 file (required to preserve Soundvision loudspeaker appearance)
    but replace the generated HEADER with a small deterministic header:
      - AC1009 / R12
      - insertion base explicitly 0,0,0
      - insertion units explicitly metres
      - no invalid placeholder extents

    This changes metadata only; no entity or block coordinates are transformed.
    """
    raw = output_path.read_bytes()
    # ezdxf writes text DXF here; latin-1 is byte-preserving for the ASCII DXF
    # control data and safely round-trips any extended single-byte characters.
    content = raw.decode("latin-1")
    newline = "\r\n" if "\r\n" in content else "\n"
    lines = content.splitlines()

    start = None
    end = None
    for i in range(len(lines) - 3):
        if (
            lines[i].strip() == "0"
            and lines[i + 1].strip() == "SECTION"
            and lines[i + 2].strip() == "2"
            and lines[i + 3].strip() == "HEADER"
        ):
            start = i
            break

    if start is None:
        raise ValueError("Saved DXF does not contain a HEADER section")

    for i in range(start + 4, len(lines) - 1):
        if lines[i].strip() == "0" and lines[i + 1].strip() == "ENDSEC":
            end = i + 2
            break

    if end is None:
        raise ValueError("Saved DXF HEADER section is not terminated")

    header = [
        "0",
        "SECTION",
        "2",
        "HEADER",
        "999",
        "Soundvision to DXF converter v18 - Soundvision R12 loudspeaker base preserved",
        "9",
        "$ACADVER",
        "1",
        "AC1009",
        "9",
        "$DWGCODEPAGE",
        "3",
        "ANSI_1252",
        "9",
        "$INSBASE",
        "10",
        "0.0",
        "20",
        "0.0",
        "30",
        "0.0",
        "9",
        "$INSUNITS",
        "70",
        str(int(InsertUnits.Meters)),
    ]

    # Keep header extents consistent with the real symmetric helper extents.
    if origin_anchor is not None:
        bound_x = float(origin_anchor.get("symmetric_x", 0.0))
        bound_y = float(origin_anchor.get("symmetric_y", 0.0))
        if math.isfinite(bound_x) and math.isfinite(bound_y):
            header.extend([
                "9",
                "$EXTMIN",
                "10",
                repr(-bound_x),
                "20",
                repr(-bound_y),
                "30",
                "0.0",
                "9",
                "$EXTMAX",
                "10",
                repr(bound_x),
                "20",
                repr(bound_y),
                "30",
                "0.0",
            ])

    header.extend([
        "0",
        "ENDSEC",
    ])

    rebuilt = lines[:start] + header + lines[end:]
    output_path.write_bytes((newline.join(rebuilt) + newline).encode("latin-1"))

def write_dxf(
    surfaces: Iterable[dict],
    output_path: Path,
    export_faces: bool,
    export_outlines: bool,
    export_points: bool,
    loudspeaker_dxf_path: Path | None = None,
) -> tuple[int, dict | None]:
    surfaces = list(surfaces)

    merge_info = None
    if loudspeaker_dxf_path is not None:
        doc, merge_info = prepare_loudspeaker_base_dxf(loudspeaker_dxf_path)
    else:
        doc = ezdxf.new("R2018")
        doc.header["$INSUNITS"] = int(InsertUnits.Meters)

    msp = doc.modelspace()
    ensure_hierarchy_layers(doc, build_hierarchical_layer(VW_ROOT))
    ensure_hierarchy_layers(doc, build_hierarchical_layer(VW_ROOM_GEOMETRY_ROOT))

    exported_count = 0

    for surface in surfaces:
        layer = geometry_layer_for(surface)
        ensure_hierarchy_layers(doc, layer)

        points = normalize_polygon_points(surface["points"])
        if len(points) < 3:
            continue

        if export_faces:
            if len(points) in (3, 4):
                msp.add_3dface(points, dxfattribs={"layer": layer})
            else:
                for a, b, c in triangulate_polygon(points):
                    msp.add_3dface(
                        [points[a], points[b], points[c]],
                        dxfattribs={"layer": layer},
                    )

        if export_outlines:
            msp.add_polyline3d(points, close=True, dxfattribs={"layer": layer})

        if export_points:
            # Keep vertices in the same Vectorworks class as their source
            # geometry; do not create an extra hierarchy branch just for points.
            for p in points:
                msp.add_point(p, dxfattribs={"layer": layer})

        exported_count += 1

    doc.header["$INSUNITS"] = int(InsertUnits.Meters)

    origin_anchor = None
    if merge_info is not None and doc.dxfversion == "AC1009":
        origin_anchor = _add_vectorworks_origin_extent_anchor(doc)
        merge_info["vectorworks_origin_anchor"] = origin_anchor

    doc.saveas(output_path)

    # AC1009 keeps the Soundvision loudspeaker face appearance, but ezdxf drops
    # the non-standard R12 $INSUNITS field and adds invalid placeholder extents
    # while saving.  Restore deterministic origin/unit metadata for Vectorworks.
    if merge_info is not None and doc.dxfversion == "AC1009":
        _restore_vectorworks_r12_origin_header(output_path, origin_anchor)

    return exported_count, merge_info


def main() -> int:
    args = parse_cli_args()
    input_path = get_input_path(args.input)
    if not input_path.exists():
        print(f"ERROR: File not found: {input_path}")
        return 1

    export_faces, export_outlines, export_points = get_export_options()
    if not any((export_faces, export_outlines, export_points)):
        print("Cancelled: No export option selected.")
        return 0

    loudspeaker_dxf_path = get_loudspeaker_dxf_path(
        args.merge_dxf,
        no_merge_prompt=args.no_merge_prompt,
    )
    if loudspeaker_dxf_path is not None and not loudspeaker_dxf_path.exists():
        print(f"ERROR: Loudspeaker DXF not found: {loudspeaker_dxf_path}")
        return 1

    output_path = input_path.with_name(input_path.stem + "_converted.dxf")

    print("\n" + "=" * 72)
    print("SOUNDVISION TO DXF CONVERTER v18")
    print("=" * 72)
    print(f"Input:  {input_path}")

    try:
        clear_xml, crypto_label = decrypt_soundvision(input_path)
    except Exception as exc:
        print(f"\nERROR while decrypting: {exc}")
        return 1

    print(f"Decrypt: OK ({crypto_label})")

    try:
        surfaces, source_counts = extract_geometry(clear_xml)
    except ET.ParseError as exc:
        print(f"ERROR: Decrypted XML could not be parsed: {exc}")
        return 1
    except ValueError as exc:
        print(f"ERROR: Unsupported/invalid Soundvision geometry data: {exc}")
        return 1

    if not surfaces:
        print("ERROR: No supported room geometry found.")
        return 1

    try:
        validate_geometry(surfaces)
    except ValueError as exc:
        print(f"ERROR: Invalid generated geometry: {exc}")
        return 1

    print(f"DXF faces/patches: {len(surfaces)}")
    print(f"  native Surfaces:    {source_counts['Surface']}")
    print(f"  Balcony objects:    {source_counts['Balcony']}")
    print(f"  Revolution objects: {source_counts['Revolution']}")

    grouped_classes = {}
    ungrouped_classes = {}
    for item in surfaces:
        group = tuple(item.get("group_path", ()))
        layer = geometry_layer_for(item)
        if group:
            grouped_classes[group] = layer
        else:
            name = str(
                item.get("source_object_name")
                or item.get("name")
                or item.get("source_type")
                or "Geometry"
            )
            ungrouped_classes[name] = layer

    if grouped_classes:
        print("Soundvision groups -> Vectorworks classes:")
        for group, layer in sorted(grouped_classes.items()):
            print("  " + " / ".join(group) + " -> " + layer)

    if ungrouped_classes:
        print("Ungrouped Soundvision room objects -> Vectorworks classes:")
        for name, layer in sorted(ungrouped_classes.items()):
            print(f"  {name} -> {layer}")

    print("Export options:")
    print(f"  3D Faces:    {'YES' if export_faces else 'NO'}")
    print(f"  3D Outlines: {'YES' if export_outlines else 'NO'}")
    print(f"  Vertices:    {'YES' if export_points else 'NO'}")
    print(
        "  Loudspeaker DXF: "
        + (str(loudspeaker_dxf_path) if loudspeaker_dxf_path else "NO")
    )

    try:
        count, merge_info = write_dxf(
            surfaces,
            output_path,
            export_faces,
            export_outlines,
            export_points,
            loudspeaker_dxf_path=loudspeaker_dxf_path,
        )
    except Exception as exc:
        print(f"ERROR while writing/merging DXF: {exc}")
        return 1

    print("\n" + "=" * 72)
    print("DONE")
    print("=" * 72)
    print(f"{count} room faces/patches exported")
    if merge_info:
        print(
            f"{merge_info['entities']} loudspeaker DXF entities preserved "
            f"({merge_info['source_units']}, scale {merge_info['scale_to_meters']:.12g}, "
            f"source DXF {merge_info['source_dxf_version']} -> output {merge_info['output_dxf_version']})"
        )
    print(f"DXF: {output_path}")
    print("Units: metres")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
