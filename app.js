"use strict";

const PYODIDE_INDEX = "https://cdn.jsdelivr.net/pyodide/v314.0.6/full/";
const EZDXF_VERSION = "1.4.4";

const $ = (id) => document.getElementById(id);
const svInput = $("svFile");
const speakerInput = $("speakerFile");
const svDrop = $("svDrop");
const speakerDrop = $("speakerDrop");
const clearSpeaker = $("clearSpeaker");
const convertButton = $("convertButton");
const engineStatus = $("engineStatus");
const resultPanel = $("resultPanel");
const stats = $("stats");
const downloadButton = $("downloadButton");

let pyodide = null;
let converterReady = false;
let projectFile = null;
let loudspeakerFile = null;
let currentDownloadUrl = null;

function setStatus(text, kind = "info") {
  engineStatus.textContent = text;
  engineStatus.className = `status ${kind}`;
}

function humanSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function extensionOf(name) {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i).toLowerCase() : "";
}

function validSoundvisionFile(file) {
  return [".xmls", ".xmlp"].includes(extensionOf(file.name));
}

function validDxfFile(file) {
  return extensionOf(file.name) === ".dxf";
}

function resetResult() {
  resultPanel.classList.add("hidden");
  stats.innerHTML = "";
  if (currentDownloadUrl) {
    URL.revokeObjectURL(currentDownloadUrl);
    currentDownloadUrl = null;
  }
  downloadButton.removeAttribute("href");
}

function refreshButton() {
  convertButton.disabled = !(converterReady && projectFile);
  convertButton.textContent = converterReady ? "Convert to DXF" : "Initializing converter…";
}

function setProjectFile(file) {
  resetResult();
  if (!validSoundvisionFile(file)) {
    projectFile = null;
    svDrop.classList.remove("has-file");
    $("svFileName").textContent = "Please select a .xmls or .xmlp file";
    setStatus("Unsupported project file type. Choose a Soundvision .xmls or .xmlp file.", "error");
    refreshButton();
    return;
  }
  projectFile = file;
  svDrop.classList.add("has-file");
  $("svFileName").textContent = `${file.name} · ${humanSize(file.size)}`;
  if (converterReady) setStatus("Converter ready. Your file stays in this browser tab.", "success");
  refreshButton();
}

function setSpeakerFile(file) {
  resetResult();
  if (!validDxfFile(file)) {
    loudspeakerFile = null;
    speakerDrop.classList.remove("has-file");
    $("speakerFileName").textContent = "Please select a .dxf file";
    clearSpeaker.classList.add("hidden");
    setStatus("The optional loudspeaker file must be a DXF.", "error");
    return;
  }
  loudspeakerFile = file;
  speakerDrop.classList.add("has-file");
  $("speakerFileName").textContent = `${file.name} · ${humanSize(file.size)}`;
  clearSpeaker.classList.remove("hidden");
}

function bindDropzone(button, input, setter) {
  button.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    if (input.files && input.files[0]) setter(input.files[0]);
  });

  ["dragenter", "dragover"].forEach((eventName) => {
    button.addEventListener(eventName, (event) => {
      event.preventDefault();
      button.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((eventName) => {
    button.addEventListener(eventName, (event) => {
      event.preventDefault();
      button.classList.remove("dragover");
    });
  });
  button.addEventListener("drop", (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (file) setter(file);
  });
}

function cleanupVirtualFiles() {
  ["/tmp/sv_project.xmls", "/tmp/sv_speakers.dxf", "/tmp/sv_output.dxf"].forEach((path) => {
    try { pyodide.FS.unlink(path); } catch (_) { /* not present */ }
  });
}

function renderStats(result, outputBytes) {
  const counts = result.source_counts || {};
  const merge = result.loudspeaker_merge;
  const cards = [
    [String(result.patches), "DXF faces / patches"],
    [String(counts.Surface ?? 0), "Native Surfaces"],
    [String(counts.Balcony ?? 0), "Balcony objects"],
    [String(counts.Revolution ?? 0), "Revolution objects"],
    [result.decrypt_method, "Read / decrypt method", "wide"],
    [merge ? `${merge.entities} entities` : "No", "Loudspeaker DXF", "wide"],
    [humanSize(outputBytes.length), "DXF file size"],
    [result.units, "Drawing units"],
  ];

  stats.innerHTML = cards.map(([value, label, extra = ""]) =>
    `<div class="stat ${extra}"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`
  ).join("");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function loadConverter() {
  try {
    setStatus("Loading Python runtime…");
    pyodide = await loadPyodide({ indexURL: PYODIDE_INDEX });

    setStatus("Loading cryptography and Python package installer…");
    await pyodide.loadPackage(["micropip", "cryptography"]);

    setStatus(`Loading ezdxf ${EZDXF_VERSION} and dependencies…`);
    await pyodide.runPythonAsync(`
import micropip
await micropip.install("ezdxf==${EZDXF_VERSION}")
`);

    setStatus("Loading Soundvision converter v18…");
    for (const fileName of ["soundvision_to_dxf_converter_v18.py", "web_converter.py"]) {
      const response = await fetch(`./${fileName}`, { cache: "no-cache" });
      if (!response.ok) throw new Error(`Could not load ${fileName} (${response.status}).`);
      const source = await response.text();
      pyodide.FS.writeFile(`/home/pyodide/${fileName}`, source, { encoding: "utf8" });
    }

    pyodide.runPython(`
import sys
if "/home/pyodide" not in sys.path:
    sys.path.insert(0, "/home/pyodide")
import web_converter
`);

    converterReady = true;
    setStatus("Converter ready. Your selected files are processed locally in this browser tab.", "success");
    refreshButton();
  } catch (error) {
    console.error(error);
    converterReady = false;
    refreshButton();
    setStatus(`Converter initialization failed: ${error.message || error}`, "error");
  }
}

async function convert() {
  if (!converterReady || !projectFile) return;

  const faces = $("faces").checked;
  const outlines = $("outlines").checked;
  const points = $("points").checked;
  if (!faces && !outlines && !points) {
    setStatus("Select at least one export option.", "error");
    return;
  }

  resetResult();
  convertButton.disabled = true;
  convertButton.textContent = "Converting…";
  setStatus("Reading and converting Soundvision geometry…");

  try {
    cleanupVirtualFiles();

    const projectBytes = new Uint8Array(await projectFile.arrayBuffer());
    pyodide.FS.writeFile("/tmp/sv_project.xmls", projectBytes);

    let speakerPath = null;
    if (loudspeakerFile) {
      const speakerBytes = new Uint8Array(await loudspeakerFile.arrayBuffer());
      pyodide.FS.writeFile("/tmp/sv_speakers.dxf", speakerBytes);
      speakerPath = "/tmp/sv_speakers.dxf";
    }

    pyodide.globals.set("web_input_path", "/tmp/sv_project.xmls");
    pyodide.globals.set("web_input_name", projectFile.name);
    pyodide.globals.set("web_output_path", "/tmp/sv_output.dxf");
    pyodide.globals.set("web_faces", faces);
    pyodide.globals.set("web_outlines", outlines);
    pyodide.globals.set("web_points", points);
    pyodide.globals.set("web_speaker_path", speakerPath);

    const resultJson = pyodide.runPython(`
web_converter.convert_file(
    web_input_path,
    web_input_name,
    web_output_path,
    web_faces,
    web_outlines,
    web_points,
    web_speaker_path,
)
`);
    const result = JSON.parse(resultJson);
    const outputBytes = pyodide.FS.readFile("/tmp/sv_output.dxf");

    const blob = new Blob([outputBytes], { type: "application/dxf" });
    currentDownloadUrl = URL.createObjectURL(blob);
    downloadButton.href = currentDownloadUrl;
    downloadButton.download = result.output_name;
    downloadButton.textContent = `Download ${result.output_name}`;

    renderStats(result, outputBytes);
    resultPanel.classList.remove("hidden");
    setStatus(`Done. ${result.exported} room faces / patches exported.`, "success");
    resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    console.error(error);
    const message = String(error?.message || error)
      .replace(/^PythonError:\s*/i, "")
      .split("\n")
      .filter(Boolean)
      .slice(-3)
      .join(" · ");
    setStatus(`Conversion failed: ${message}`, "error");
  } finally {
    refreshButton();
  }
}

bindDropzone(svDrop, svInput, setProjectFile);
bindDropzone(speakerDrop, speakerInput, setSpeakerFile);
clearSpeaker.addEventListener("click", () => {
  loudspeakerFile = null;
  speakerInput.value = "";
  speakerDrop.classList.remove("has-file");
  $("speakerFileName").textContent = "No DXF selected";
  clearSpeaker.classList.add("hidden");
  resetResult();
});
convertButton.addEventListener("click", convert);
window.addEventListener("beforeunload", () => {
  if (currentDownloadUrl) URL.revokeObjectURL(currentDownloadUrl);
});

loadConverter();
