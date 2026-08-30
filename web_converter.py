"""Browser adapter for Soundvision to DXF Converter v18.

This module intentionally keeps the proven v18 converter core unchanged and exposes
one small file-based API for Pyodide. Files live only in Pyodide's in-memory virtual
filesystem while the conversion runs in the browser.
"""
from __future__ import annotations

import json
from pathlib import Path

import soundvision_to_dxf_converter_v18 as core


def _json_safe_merge_info(merge_info: dict | None) -> dict | None:
    if not merge_info:
        return None
    result = dict(merge_info)
    anchor = result.get("vectorworks_origin_anchor")
    if isinstance(anchor, dict):
        result["vectorworks_origin_anchor"] = dict(anchor)
    return result


def convert_file(
    input_path: str,
    input_name: str,
    output_path: str,
    export_faces: bool = False,
    export_outlines: bool = True,
    export_points: bool = False,
    loudspeaker_dxf_path: str | None = None,
) -> str:
    """Convert one Soundvision file and return conversion metadata as JSON."""
    if not any((export_faces, export_outlines, export_points)):
        raise ValueError("Select at least one DXF export option.")

    source_path = Path(input_path)
    target_path = Path(output_path)
    speaker_path = Path(loudspeaker_dxf_path) if loudspeaker_dxf_path else None

    clear_xml, crypto_label = core.decrypt_soundvision(source_path)
    geometry, source_counts = core.extract_geometry(clear_xml)
    if not geometry:
        raise ValueError("No supported Surface, Balcony or Revolution geometry was found.")

    core.validate_geometry(geometry)
    exported_count, merge_info = core.write_dxf(
        geometry,
        target_path,
        bool(export_faces),
        bool(export_outlines),
        bool(export_points),
        loudspeaker_dxf_path=speaker_path,
    )

    grouped = set()
    ungrouped = set()
    for item in geometry:
        layer = core.geometry_layer_for(item)
        if item.get("group_path"):
            grouped.add(layer)
        else:
            ungrouped.add(layer)

    safe_name = Path(input_name or "soundvision").stem or "soundvision"
    return json.dumps(
        {
            "core_version": "v18",
            "input_name": input_name,
            "output_name": f"{safe_name}_converted.dxf",
            "decrypt_method": crypto_label,
            "patches": len(geometry),
            "exported": exported_count,
            "source_counts": source_counts,
            "grouped_classes": len(grouped),
            "ungrouped_classes": len(ungrouped),
            "export_faces": bool(export_faces),
            "export_outlines": bool(export_outlines),
            "export_points": bool(export_points),
            "loudspeaker_merge": _json_safe_merge_info(merge_info),
            "units": "metres",
        }
    )
