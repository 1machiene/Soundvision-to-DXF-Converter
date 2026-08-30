# Soundvision → DXF Converter Web

A browser-hosted version of **Soundvision to DXF Converter v18**.

The application accepts an encrypted Soundvision `.xmls` or `.xmlp` file directly. There is **no manual Surface input**. Conversion runs locally in the browser with Pyodide; the selected project is not uploaded to the GitHub Pages host.

## Features

- Open / drag-and-drop Soundvision `.xmls` and `.xmlp` files
- Surface, Balcony and Revolution extraction from v18
- Vectorworks-friendly hierarchy under `SV-Room Geometry`
- Optional Soundvision loudspeaker DXF merge under `SV-Loudspeakers`
- Export switches for 3D Faces, 3D Outlines and Vertices
- v18 defaults: Faces **off**, Outlines **on**, Vertices **off**
- DXF output in metres
- Browser-only conversion; no application backend

## Files

- `index.html` — web UI
- `style.css` — styling
- `app.js` — browser/Pyodide integration
- `web_converter.py` — thin browser adapter
- `soundvision_to_dxf_converter_v18.py` — unchanged v18 converter core
- `icon-256.png` — site icon
- `.nojekyll` — tells GitHub Pages to publish files directly

## Runtime dependencies

The page loads these at runtime from the internet:

- Pyodide 314.0.6
- `cryptography` from the Pyodide distribution
- `ezdxf==1.4.4` and its dependencies via `micropip`

The first page load is therefore larger than a normal static site. Browsers generally cache those runtime files after loading them.

## Privacy / security note

The selected Soundvision and loudspeaker DXF files are written only to Pyodide's virtual filesystem inside the browser tab. This app does not contain code that POSTs or uploads those files to a server. The Python runtime and Python packages themselves are downloaded from their public CDNs/package sources.

The Soundvision decryption constants are included client-side because this is a browser-only application.
