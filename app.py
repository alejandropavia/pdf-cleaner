import uuid
import tempfile
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, Response

from clean_pdf import clean_pdf, compress_with_ghostscript

app = FastAPI(title="PDF Cleaner & Compressor")

HTML = """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <title>PDF Cleaner</title>
  <style>
    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial;
      background: #f7f8fa;
      margin: 0;
      padding: 0;
    }
    .container {
      max-width: 520px;
      margin: 80px auto;
      background: #fff;
      padding: 32px;
      border-radius: 14px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.08);
    }
    h1 { text-align: center; margin-bottom: 8px; }
    p.subtitle { text-align: center; color: #666; margin-bottom: 22px; font-size: 14px; }

    label { font-weight: 600; margin-top: 18px; display: block; font-size: 14px; }
    input, select {
      width: 100%;
      margin-top: 6px;
      padding: 12px;
      border-radius: 8px;
      border: 1px solid #ddd;
      font-size: 14px;
    }

    button {
      width: 100%;
      margin-top: 26px;
      padding: 14px;
      background: #111;
      color: white;
      border: none;
      border-radius: 10px;
      font-size: 15px;
      cursor: pointer;
    }
    button:hover { background: #000; }
    button:disabled { opacity: 0.75; cursor: not-allowed; }

    .hint {
      margin-top: 10px;
      font-size: 12px;
      color: #888;
      text-align: center;
      line-height: 1.35;
    }
    #fileName { text-align: left; margin-top: 8px; }

    .spinner {
      display: inline-block;
      width: 14px;
      height: 14px;
      border: 2px solid rgba(255,255,255,0.35);
      border-top-color: #fff;
      border-radius: 50%;
      animation: spin 0.7s linear infinite;
      vertical-align: -2px;
      margin-right: 8px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    .error {
      margin-top: 14px;
      background: #fff2f2;
      border: 1px solid #ffd0d0;
      color: #7a1b1b;
      padding: 10px 12px;
      border-radius: 10px;
      font-size: 13px;
      display: none;
      white-space: pre-wrap;
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>PDF Cleaner</h1>
    <p class="subtitle">Limpia y comprime PDFs en segundos</p>

    <form id="pdfForm" enctype="multipart/form-data">
      <label>Archivo PDF</label>
      <input id="file" type="file" name="file" accept="application/pdf" required>
      <div id="fileName" class="hint">Ningún archivo seleccionado</div>

      <label>Compresión</label>
      <select id="compress" name="compress">
        <option value="yes">Limpiar + comprimir</option>
        <option value="no">Solo limpiar</option>
      </select>

      <label>Calidad</label>
      <select id="quality" name="quality">
        <option value="ebook">Equilibrado (recomendado)</option>
        <option value="screen">Máxima compresión</option>
        <option value="printer">Alta calidad</option>
        <option value="prepress">Máxima calidad</option>
      </select>

      <button id="submitBtn" type="submit">Procesar PDF</button>

      <div class="hint">
        Tus archivos no se guardan.<br/>
        Si procesas el mismo PDF varias veces, tu navegador puede guardarlo como <b>(1)</b>, <b>(2)</b> en Descargas.
      </div>

      <div id="errBox" class="error"></div>
    </form>
  </div>

  <script>
    const fileInput = document.getElementById("file");
    const fileName = document.getElementById("fileName");
    const form = document.getElementById("pdfForm");
    const btn = document.getElementById("submitBtn");
    const errBox = document.getElementById("errBox");

    function setLoading(isLoading) {
      if (isLoading) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span>Procesando...';
      } else {
        btn.disabled = false;
        btn.textContent = 'Procesar PDF';
      }
    }

    function showError(msg) {
      errBox.style.display = "block";
      errBox.textContent = msg;
    }

    function clearError() {
      errBox.style.display = "none";
      errBox.textContent = "";
    }

    fileInput.addEventListener("change", () => {
      fileName.textContent = fileInput.files?.[0]?.name || "Ningún archivo seleccionado";
      clearError();
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      clearError();

      const f = fileInput.files?.[0];
      if (!f) {
        showError("Selecciona un PDF primero.");
        return;
      }

      setLoading(true);

      try {
        const fd = new FormData();
        fd.append("file", f);
        fd.append("compress", document.getElementById("compress").value);
        fd.append("quality", document.getElementById("quality").value);

        const res = await fetch("/process", { method: "POST", body: fd });

        if (!res.ok) {
          const txt = await res.text();
          showError(txt);
          setLoading(false);
          return;
        }

        const blob = await res.blob();

        // Descargar con el mismo nombre que el archivo original
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = f.name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.URL.revokeObjectURL(url);

        setLoading(false);

      } catch (err) {
        showError("Error inesperado: " + err);
        setLoading(false);
      }
    });
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML


@app.post("/process")
async def process(
    file: UploadFile = File(...),
    compress: str = Form("yes"),
    quality: str = Form("ebook"),
):
    if not (file.filename or "").lower().endswith(".pdf"):
        return HTMLResponse("❌ Solo se aceptan PDFs.", status_code=400)

    job_id = str(uuid.uuid4())

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        inp = tmpdir / f"{job_id}_input.pdf"
        cleaned = tmpdir / f"{job_id}_cleaned.pdf"
        out = tmpdir / f"{job_id}_output.pdf"

        inp.write_bytes(await file.read())

        try:
            stats = clean_pdf(str(inp), str(cleaned))

            if compress == "yes":
                compress_with_ghostscript(str(cleaned), str(out), quality)
                final_path = out
            else:
                final_path = cleaned

            if not final_path.exists():
                return HTMLResponse(
                    f"<h3>❌ Error</h3><pre>No se generó el archivo final: {final_path}</pre>",
                    status_code=500,
                )

            data = final_path.read_bytes()

        except Exception as e:
            return HTMLResponse(
                f"<h3>❌ Error procesando el PDF</h3><pre>{e}</pre>",
                status_code=500,
            )

    download_name = file.filename  # mismo nombre que el original

    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{download_name}"',
            "X-Total-Pages": str(stats["total"]),
            "X-Removed-Pages": str(stats["removed"]),
        },
    )
