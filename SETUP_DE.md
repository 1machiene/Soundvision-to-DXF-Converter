# Einrichtung auf GitHub Pages

## 1. Repository anlegen

1. Auf GitHub oben rechts **+ → New repository** wählen.
2. Repository-Name, z. B. `sv-to-dxf`.
3. **Public** auswählen. GitHub Pages ist mit GitHub Free für öffentliche Repositories verfügbar.
4. **Create repository**.

## 2. Dateien hochladen

Im neuen Repository **Add file → Upload files** wählen und den kompletten Inhalt dieses Ordners hochladen:

- `.nojekyll`
- `index.html`
- `style.css`
- `app.js`
- `web_converter.py`
- `soundvision_to_dxf_converter_v18.py`
- `icon-256.png`
- `README.md`
- `SETUP_DE.md`
- `LICENSE_NOTE.txt`

Wichtig: Die Dateien müssen direkt im Repository-Root liegen, nicht noch einmal in einem Unterordner `sv_to_dxf_web`.

Danach **Commit changes**.

> Hinweis: `.nojekyll` ist eine versteckte Datei. Falls dein Betriebssystem sie beim manuellen Upload nicht zeigt, kannst du sie auf GitHub über **Add file → Create new file** anlegen. Als Dateiname einfach `.nojekyll` eingeben und leer speichern.

## 3. GitHub Pages einschalten

1. Repository öffnen.
2. **Settings**.
3. Links **Pages**.
4. Unter **Build and deployment → Source**: **Deploy from a branch**.
5. Branch: **main**.
6. Folder: **/(root)**.
7. **Save**.

Die Seite ist danach typischerweise unter folgender Adresse erreichbar:

`https://DEIN-GITHUB-NAME.github.io/sv-to-dxf/`

Bei einem anderen Repository-Namen ändert sich der letzte Teil entsprechend.

## 4. Benutzung

1. Website öffnen.
2. Warten, bis unten **Converter ready** steht. Beim ersten Laden werden Pyodide und die Python-Abhängigkeiten geladen.
3. `.xmls` oder `.xmlp` auswählen bzw. auf die Drop-Zone ziehen.
4. Optional den von Soundvision exportierten Loudspeaker-DXF auswählen.
5. Exportoptionen wählen. Standard wie v18:
   - 3D Faces: aus
   - 3D Outlines: an
   - Vertices: aus
6. **Convert to DXF**.
7. Fertige `.dxf` herunterladen.

## 5. Datenschutz / Dateiverarbeitung

GitHub Pages dient nur die statischen Dateien der Website aus. Die ausgewählte Soundvision-Datei wird nicht an einen eigenen Server hochgeladen. Sie liegt während der Konvertierung im virtuellen Dateisystem von Pyodide innerhalb des Browser-Tabs.

Die Laufzeit selbst lädt Pyodide und Python-Pakete von öffentlichen CDN/Paketquellen. Deshalb ist zum Initialisieren der Website eine Internetverbindung notwendig.

## 6. Lokal testen

Im Projektordner:

```bash
python3 -m http.server 8000
```

Dann im Browser öffnen:

`http://localhost:8000`

Nicht einfach `index.html` per Doppelklick als `file://` öffnen.

## 7. Änderungen veröffentlichen

Wenn du später `index.html`, `app.js`, `style.css` oder den Python-Code auf GitHub änderst und auf `main` commitest, veröffentlicht GitHub Pages die neue Version automatisch.
