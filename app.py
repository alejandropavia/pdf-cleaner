import uuid
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, Response

from clean_pdf import clean_pdf, compress_with_ghostscript

app = FastAPI(title="PDF Cleaner & Compressor")

# =========================
# LIMITES FREE (MVP)
# =========================
MAX_FREE_MB = 5
MAX_FREE_BYTES = MAX_FREE_MB * 1024 * 1024

# 1 PDF/día por IP (simple, MVP)
# Nota: en Render Free el servicio puede "dormirse" y reiniciarse -> esta memoria se puede resetear.
DAILY_USAGE_BY_IP = {}  # { "ip": "YYYY-MM-DD" }


APP_HTML = r"""
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>PDF Cleaner — PDFs listos para enviar, subir y archivar</title>
  <style>
    :root{
      --bg:#f7f8fa; --card:#ffffff; --text:#0f172a; --muted:#475569;
      --line:#e5e7eb; --shadow:0 10px 30px rgba(0,0,0,0.08);
      --btn:#111; --btn2:#fff;
      --okbg:#ecfeff; --okline:#a5f3fc;
      --errbg:#fff2f2; --errline:#ffd0d0; --err:#7a1b1b;
    }
    *{ box-sizing:border-box; }
    body{
      margin:0; background:var(--bg); color:var(--text);
      font-family:-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial;
    }
    a{ color:inherit; text-decoration:none; }
    .wrap{ max-width:1080px; margin:0 auto; padding:28px 18px 80px; }

    .topbar{
      display:flex; align-items:center; justify-content:space-between;
      gap:14px; margin-bottom:18px;
    }
    .brand{ display:flex; align-items:center; gap:10px; font-weight:800; letter-spacing:-0.2px; }
    .badge{
      font-size:12px; padding:6px 10px; border-radius:999px;
      background:#111; color:#fff; display:inline-flex; gap:6px; align-items:center;
    }
    .nav{ display:flex; gap:14px; font-size:13px; color:var(--muted); }

    .hero{
      background:var(--card); border:1px solid var(--line); border-radius:18px;
      padding:26px; box-shadow:var(--shadow);
      display:grid; grid-template-columns: 1.2fr 0.8fr; gap:22px;
    }
    @media(max-width:880px){ .hero{ grid-template-columns:1fr; } }
    h1{ margin:10px 0 10px; font-size:44px; line-height:1.05; letter-spacing:-1px; }
    @media(max-width:520px){ h1{ font-size:34px; } }
    .sub{ color:var(--muted); font-size:15px; line-height:1.45; margin:0 0 18px; }
    .ctaRow{ display:flex; gap:12px; flex-wrap:wrap; }
    .btn{
      border-radius:12px; padding:12px 16px; border:1px solid #111;
      font-weight:700; font-size:14px; cursor:pointer; display:inline-flex; align-items:center; gap:8px;
    }
    .btn.primary{ background:var(--btn); color:#fff; }
    .btn.secondary{ background:var(--btn2); color:#111; }
    .btn:hover{ transform: translateY(-1px); }
    .note{ margin-top:10px; font-size:12px; color:var(--muted); }

    .box{
      border:1px solid var(--line); border-radius:16px; padding:16px;
      background:#fafafa;
    }
    .box h3{ margin:0 0 10px; font-size:16px; }
    .box ul{ margin:0; padding-left:18px; color:var(--muted); }
    .box li{ margin:6px 0; }

    .trustRow{
      margin-top:18px;
      display:grid; grid-template-columns: repeat(3, 1fr); gap:12px;
    }
    @media(max-width:880px){ .trustRow{ grid-template-columns:1fr; } }
    .trust{
      background:#fff; border:1px solid var(--line); border-radius:16px;
      padding:14px 14px; box-shadow:0 8px 22px rgba(0,0,0,0.05);
    }
    .trust b{ display:block; margin-bottom:6px; }
    .trust span{ color:var(--muted); font-size:13px; line-height:1.35; }

    .segments{
      margin-top:18px;
      display:grid; grid-template-columns: repeat(3, 1fr); gap:12px;
    }
    @media(max-width:880px){ .segments{ grid-template-columns:1fr; } }
    .seg{
      border:1px solid var(--line); border-radius:16px; padding:14px; background:#fff;
    }
    .seg b{ display:block; margin-bottom:4px; }
    .seg span{ color:var(--muted); font-size:13px; }

    .tool{
      margin-top:18px;
      background:#fff; border:1px solid var(--line); border-radius:18px;
      padding:22px; box-shadow:var(--shadow);
    }
    .toolHead{
      display:flex; justify-content:space-between; align-items:flex-end; gap:12px; flex-wrap:wrap;
      margin-bottom:12px;
    }
    .toolHead h2{ margin:0; font-size:22px; letter-spacing:-0.2px; }
    .toolHead p{ margin:0; color:var(--muted); font-size:13px; }

    label{ font-weight:800; display:block; margin-top:14px; font-size:13px; }
    input, select{
      width:100%; margin-top:6px; padding:12px;
      border-radius:10px; border:1px solid #d6d6d6; font-size:14px;
      background:#fff;
    }
    .row{ display:grid; grid-template-columns: 1fr 1fr; gap:12px; }
    @media(max-width:700px){ .row{ grid-template-columns:1fr; } }

    .submit{
      width:100%; margin-top:16px; padding:14px; border-radius:12px;
      border:none; background:#111; color:#fff; font-size:15px; font-weight:900;
      cursor:pointer;
    }
    .submit:hover{ background:#000; }
    .submit:disabled{ opacity:0.75; cursor:not-allowed; }

    .hint{ margin-top:10px; font-size:12px; color:var(--muted); line-height:1.35; }
    #fileName{ margin-top:8px; }

    .spinner{
      display:inline-block; width:14px; height:14px;
      border:2px solid rgba(255,255,255,0.35); border-top-color:#fff;
      border-radius:50%; animation:spin 0.7s linear infinite;
      vertical-align:-2px; margin-right:8px;
    }
    @keyframes spin{ to{ transform:rotate(360deg); } }

    .error{
      margin-top:12px; background:var(--errbg); border:1px solid var(--errline);
      color:var(--err); padding:10px 12px; border-radius:12px;
      font-size:13px; display:none; white-space:pre-wrap;
    }
    .result{
      margin-top:12px; background:var(--okbg); border:1px solid var(--okline);
      color:#0f172a; padding:10px 12px; border-radius:12px;
      font-size:13px; display:none;
    }
    .result b{ color:#111; }

    .explain{
      margin-top:8px;
      padding:10px 12px;
      border:1px dashed #d6d6d6;
      border-radius:12px;
      color:var(--muted);
      font-size:12px;
      line-height:1.4;
      background:#fafafa;
    }

    .pricing{ margin-top:18px; display:grid; grid-template-columns: repeat(3, 1fr); gap:12px; }
    @media(max-width:880px){ .pricing{ grid-template-columns:1fr; } }
    .plan{
      background:#fff; border:1px solid var(--line); border-radius:18px;
      padding:16px; box-shadow:0 8px 22px rgba(0,0,0,0.05);
    }
    .plan h3{ margin:0 0 6px; }
    .price{ font-size:26px; font-weight:900; margin:6px 0 10px; letter-spacing:-0.5px; }
    .plan ul{ margin:0; padding-left:18px; color:var(--muted); font-size:13px; }
    .plan li{ margin:6px 0; }
    .fine{ margin-top:10px; font-size:12px; color:var(--muted); }

    .footer{ margin-top:24px; color:var(--muted); font-size:12px; text-align:center; }
  </style>
</head>

<body>
  <div class="wrap">
    <div class="topbar">
      <div class="brand">
        <div class="badge">B2B</div>
        <div>PDF Cleaner</div>
      </div>
      <div class="nav">
        <a href="#herramienta">Herramienta</a>
        <a href="#precios">Precios</a>
        <a href="#privacidad">Privacidad</a>
      </div>
    </div>

    <section class="hero">
      <div>
        <h1>PDFs listos para enviar, subir y archivar</h1>
        <p class="sub">
          Limpia y comprime PDFs profesionales en segundos. Hecho para <b>asesorías</b>, <b>inmobiliarias</b>,
          <b>arquitectos</b> e <b>ingenierías</b> que trabajan con documentos pesados o mal escaneados.
        </p>

        <div class="ctaRow">
          <a class="btn primary" href="#herramienta">✅ Limpiar 1 PDF gratis</a>
          <a class="btn secondary" href="#precios">Ver planes</a>
        </div>

        <div class="note">
          Gratis: <b>1 PDF al día</b> (máx. <b>5MB</b>). Sin instalaciones. Procesas y descargas al momento.
        </div>

        <div class="trustRow" id="privacidad">
          <div class="trust">
            <b>Privacidad real</b>
            <span>No guardamos tus PDFs. Se procesan y se eliminan al terminar.</span>
          </div>
          <div class="trust">
            <b>Hecho para empresa</b>
            <span>Ideal para email, CRM, portales y plataformas que rechazan PDFs pesados.</span>
          </div>
          <div class="trust">
            <b>Resultado visible</b>
            <span>Mostramos la reducción de peso al finalizar (antes → después).</span>
          </div>
        </div>
      </div>

      <div class="box">
        <h3>Casos típicos</h3>
        <ul>
          <li>PDF demasiado pesado para enviar por email</li>
          <li>Documento rechazado por plataformas/portales</li>
          <li>Escaneos mal optimizados (lentitud, peso, páginas en blanco)</li>
        </ul>
      </div>
    </section>

    <div class="segments">
      <div class="seg"><b>Asesorías / Gestorías</b><span>Modelos, facturas, trámites.</span></div>
      <div class="seg"><b>Inmobiliarias</b><span>Contratos, documentación cliente.</span></div>
      <div class="seg"><b>Técnicos</b><span>Planos, memorias, informes.</span></div>
    </div>

    <section id="herramienta" class="tool">
      <div class="toolHead">
        <div>
          <h2>Procesar PDF</h2>
          <p>Sube tu archivo → limpiamos → comprimimos → descargas al momento.</p>
        </div>
        <div style="color:#475569; font-size:12px;">
          Tip: “Máxima compresión” suele dar el mejor resultado en PDFs escaneados.
        </div>
      </div>

      <form id="pdfForm" enctype="multipart/form-data">
        <label>Archivo PDF</label>
        <input id="file" type="file" name="file" accept="application/pdf" required>
        <div id="fileName" class="hint">Ningún archivo seleccionado</div>

        <div class="row">
          <div>
            <label>Acción</label>
            <select id="compress" name="compress">
              <option value="yes">Limpiar + comprimir</option>
              <option value="no">Solo limpiar</option>
            </select>
          </div>

          <div>
            <label>Calidad</label>
            <select id="quality" name="quality">
              <option value="screen">Máxima compresión</option>
              <option value="ebook">Equilibrado</option>
              <option value="printer">Alta calidad</option>
            </select>
            <div id="qualityHelp" class="explain"></div>
          </div>
        </div>

        <button id="submitBtn" class="submit" type="submit">Procesar PDF</button>

        <div id="resultBox" class="result"></div>
        <div id="errBox" class="error"></div>

        <div class="hint">
          <b>Gratis:</b> 1 PDF/día (máx. 5MB).<br/>
          <b>Privacidad:</b> no se guardan archivos. Se procesan en un directorio temporal y se eliminan.<br/>
          Si procesas el mismo PDF varias veces, tu navegador puede guardarlo como (1), (2) en Descargas.
        </div>
      </form>
    </section>

    <section id="precios" class="pricing">
      <div class="plan">
        <h3>Gratis</h3>
        <div class="price">0€</div>
        <ul>
          <li>1 PDF al día</li>
          <li>Máximo 5MB por archivo</li>
          <li>Limpieza + compresión</li>
          <li>Sin registro (por ahora)</li>
        </ul>
        <div class="fine">Perfecto para probar con documentos reales.</div>
      </div>

      <div class="plan">
        <h3>Pro</h3>
        <div class="price">9€/mes</div>
        <ul>
          <li>Uso ilimitado (equipos pequeños)</li>
          <li>Prioridad de procesamiento</li>
          <li>Soporte por email</li>
        </ul>
        <div class="fine">Para asesorías, inmobiliarias y despachos con volumen.</div>
      </div>

      <div class="plan">
        <h3>Empresa</h3>
        <div class="price">A medida</div>
        <ul>
          <li>Límites más altos</li>
          <li>Acuerdos por volumen</li>
          <li>Integración (email / bandeja de entrada) en el futuro</li>
        </ul>
        <div class="fine">Si procesas PDFs cada día, hablamos.</div>
      </div>
    </section>

    <div class="footer">
      PDF Cleaner — pensado para uso profesional. Si no baja mucho el peso, es que el PDF ya venía optimizado.
    </div>
  </div>

  <script>
    const fileInput = document.getElementById("file");
    const fileName = document.getElementById("fileName");
    const form = document.getElementById("pdfForm");
    const btn = document.getElementById("submitBtn");
    const errBox = document.getElementById("errBox");
    const resultBox = document.getElementById("resultBox");
    const qualitySel = document.getElementById("quality");
    const qualityHelp = document.getElementById("qualityHelp");

    const MAX_FREE_MB = 5;
    const MAX_FREE_BYTES = MAX_FREE_MB * 1024 * 1024;

    const QUALITY_DESC = {
      "screen": "Reduce el peso al máximo manteniendo el PDF legible. Ideal para enviar por email y subir a plataformas.",
      "ebook": "Compresión moderada. Buena opción si quieres balance entre tamaño y calidad visual.",
      "printer": "Prioriza calidad (impresión/planos). Reduce menos el peso."
    };

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

    function showResult(html) {
      resultBox.style.display = "block";
      resultBox.innerHTML = html;
    }
    function clearResult() {
      resultBox.style.display = "none";
      resultBox.innerHTML = "";
    }

    function fmtBytes(n) {
      if (n < 1024) return n + " B";
      if (n < 1024 * 1024) return (n / 1024).toFixed(0) + " KB";
      return (n / (1024 * 1024)).toFixed(2) + " MB";
    }

    function updateQualityHelp() {
      const v = qualitySel.value;
      qualityHelp.textContent = QUALITY_DESC[v] || "";
    }

    // Default: Máxima compresión
    qualitySel.value = "screen";
    updateQualityHelp();
    qualitySel.addEventListener("change", updateQualityHelp);

    fileInput.addEventListener("change", () => {
      fileName.textContent = fileInput.files?.[0]?.name || "Ningún archivo seleccionado";
      clearError();
      clearResult();
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      clearError();
      clearResult();

      const f = fileInput.files?.[0];
      if (!f) {
        showError("Selecciona un PDF primero.");
        return;
      }

      // Límite FREE en el navegador (para evitar subir de más)
      if (f.size > MAX_FREE_BYTES) {
        showError("❌ Límite gratis: máximo " + MAX_FREE_MB + "MB por PDF.");
        return;
      }

      setLoading(true);

      try {
        const fd = new FormData();
        fd.append("file", f);
        fd.append("compress", document.getElementById("compress").value);
        fd.append("quality", qualitySel.value);

        const res = await fetch("/process", { method: "POST", body: fd });

        if (!res.ok) {
          const txt = await res.text();
          showError(txt);
          setLoading(false);
          return;
        }

        const blob = await res.blob();

        const original = f.size;
        const final = blob.size;
        const pct = original > 0 ? ((original - final) / original) * 100 : 0;

        showResult(
          "✅ <b>Listo</b> — " +
          "<b>" + fmtBytes(original) + "</b> → <b>" + fmtBytes(final) + "</b> " +
          "(<b>" + pct.toFixed(1) + "%</b> menos)."
        );

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
    return APP_HTML


def _today_utc_date_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _get_client_ip(request: Request) -> str:
    # Render / proxies suelen enviar X-Forwarded-For
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # puede venir como "ip1, ip2, ip3"
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.post("/process")
async def process(
    request: Request,
    file: UploadFile = File(...),
    compress: str = Form("yes"),
    quality: str = Form("screen"),  # default: máxima compresión
):
    # Validación básica
    if not (file.filename or "").lower().endswith(".pdf"):
        return HTMLResponse("❌ Solo se aceptan PDFs.", status_code=400)

    # Limitar solo a las 3 opciones permitidas (seguridad)
    if quality not in {"screen", "ebook", "printer"}:
        return HTMLResponse("❌ Opción de calidad inválida.", status_code=400)

    if compress not in {"yes", "no"}:
        return HTMLResponse("❌ Opción inválida.", status_code=400)

    # Límite 1 PDF/día por IP (FREE)
    ip = _get_client_ip(request)
    today = _today_utc_date_str()
    last_day = DAILY_USAGE_BY_IP.get(ip)
    if last_day == today:
        return HTMLResponse("❌ Límite gratis alcanzado: 1 PDF al día. Vuelve mañana.", status_code=429)

    # Leer bytes y validar tamaño (máx 5MB)
    data_in = await file.read()
    if len(data_in) > MAX_FREE_BYTES:
        return HTMLResponse(f"❌ Límite gratis: máximo {MAX_FREE_MB}MB por PDF.", status_code=413)

    job_id = str(uuid.uuid4())

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        inp = tmpdir / f"{job_id}_input.pdf"
        cleaned = tmpdir / f"{job_id}_cleaned.pdf"
        outp = tmpdir / f"{job_id}_output.pdf"

        inp.write_bytes(data_in)

        try:
            stats = clean_pdf(str(inp), str(cleaned))

            if compress == "yes":
                compress_with_ghostscript(str(cleaned), str(outp), quality)
                final_path = outp
            else:
                final_path = cleaned

            if not final_path.exists():
                return HTMLResponse("❌ No se generó el archivo final.", status_code=500)

            data_out = final_path.read_bytes()

        except Exception as e:
            return HTMLResponse(f"❌ Error procesando el PDF:\n\n{e}", status_code=500)

    # Marcar uso (solo si todo ha ido bien)
    DAILY_USAGE_BY_IP[ip] = today

    download_name = file.filename

    return Response(
        content=data_out,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{download_name}"',
            "X-Total-Pages": str(stats.get("total", "")),
            "X-Removed-Pages": str(stats.get("removed", "")),
            "X-Original-Bytes": str(len(data_in)),
            "X-Final-Bytes": str(len(data_out)),
        },
    )
