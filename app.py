import uuid
import tempfile
from pathlib import Path
from datetime import date

from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, Response, PlainTextResponse

from clean_pdf import clean_pdf, compress_with_ghostscript

app = FastAPI(title="PDF Cleaner & Compressor")

# =========
# VERSION
# =========
APP_VERSION = "2025-12-24-v4"

# =========
# LIMITES (por IP)
# =========
# Free real (lo único que aplicamos ahora mismo)
FREE_MAX_MB = 5
FREE_MONTHLY_LIMIT = 5  # 5 PDFs / mes

# Límites por plan (por ahora solo para mostrar en landing; luego Stripe)
PRO_MAX_MB = 15
PRO_MONTHLY_LIMIT = 50

# "Ilimitado" práctico para Business para que no reviente Render
# (en Render Free si subes 200MB puede petar por RAM/tiempo)
BUSINESS_MAX_MB = 60
BUSINESS_MONTHLY_LIMIT = 200

# Contador en memoria: key=(ip, YYYY-MM) -> count
# Nota: in-memory se reinicia con redeploy / sleep. Para producción: Redis/DB.
MONTHLY_COUNTER = {}


# =========
# HTML: LANDING (/)
# =========
LANDING_HTML = rf"""
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>PDF Cleaner — comprime y limpia PDFs en segundos</title>
  <style>
    :root{{
      --bg:#f7f8fa; --card:#fff; --text:#0f172a; --muted:#475569;
      --line:#e5e7eb; --shadow:0 10px 30px rgba(0,0,0,0.08);
      --btn:#111; --btn2:#fff;
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);
      font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial}}
    .wrap{{max-width:1080px;margin:0 auto;padding:28px 18px 70px}}
    .top{{display:flex;align-items:center;justify-content:space-between;gap:14px}}
    .brand{{display:flex;align-items:center;gap:10px;font-weight:900}}
    .badge{{font-size:12px;padding:6px 10px;border-radius:999px;background:#111;color:#fff}}
    .nav{{display:flex;gap:14px;font-size:13px;color:var(--muted)}}
    a{{color:inherit;text-decoration:none}}

    .hero{{margin-top:18px;background:var(--card);border:1px solid var(--line);
      border-radius:18px;padding:26px;box-shadow:var(--shadow);
      display:grid;grid-template-columns:1.25fr 0.75fr;gap:18px}}
    @media(max-width:900px){{.hero{{grid-template-columns:1fr}}}}
    h1{{margin:0 0 10px;font-size:46px;line-height:1.05;letter-spacing:-1px}}
    @media(max-width:520px){{h1{{font-size:34px}}}}
    .sub{{margin:0 0 18px;color:var(--muted);font-size:15px;line-height:1.5}}
    .ctaRow{{display:flex;gap:12px;flex-wrap:wrap}}
    .btn{{border-radius:12px;padding:12px 16px;border:1px solid #111;
      font-weight:800;font-size:14px;cursor:pointer;display:inline-flex;align-items:center;gap:8px}}
    .btn.primary{{background:var(--btn);color:#fff}}
    .btn.secondary{{background:var(--btn2);color:#111}}
    .mini{{margin-top:10px;color:var(--muted);font-size:12px}}

    .proof{{border:1px solid var(--line);border-radius:16px;padding:16px;background:#fafafa}}
    .proof b{{display:block;margin-bottom:6px}}
    .proof ul{{margin:0;padding-left:18px;color:var(--muted);font-size:13px}}
    .proof li{{margin:6px 0}}

    .pricing{{margin-top:18px;display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
    @media(max-width:900px){{.pricing{{grid-template-columns:1fr}}}}
    .plan{{background:#fff;border:1px solid var(--line);border-radius:18px;
      padding:18px;box-shadow:0 8px 22px rgba(0,0,0,0.05)}}
    .plan h3{{margin:0 0 6px}}
    .price{{font-size:28px;font-weight:950;margin:6px 0 10px;letter-spacing:-0.5px}}
    .plan ul{{margin:0;padding-left:18px;color:var(--muted);font-size:13px}}
    .plan li{{margin:6px 0}}
    .tag{{display:inline-block;font-size:12px;padding:4px 10px;border-radius:999px;border:1px solid var(--line);
      color:var(--muted);margin-top:8px}}
    .footer{{margin-top:22px;text-align:center;color:var(--muted);font-size:12px}}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="brand"><span class="badge">B2B</span> PDF Cleaner</div>
      <div class="nav">
        <a href="#precios">Precios</a>
        <a href="/app">Herramienta</a>
      </div>
    </div>

    <section class="hero">
      <div>
        <h1>PDFs listos para enviar en segundos</h1>
        <p class="sub">
          Limpia páginas en blanco y comprime tus PDFs para email y portales.
          Sin instalaciones. Resultado inmediato.
        </p>

        <div class="ctaRow">
          <a class="btn primary" href="/app">✅ Probar gratis</a>
          <a class="btn secondary" href="#precios">Ver planes</a>
        </div>

        <div class="mini">
          Gratis: <b>{FREE_MONTHLY_LIMIT} PDFs/mes</b> · máx. <b>{FREE_MAX_MB} MB</b> · sin registro
        </div>
      </div>

      <div class="proof">
        <b>Perfecto si…</b>
        <ul>
          <li>Te rechazan el PDF por tamaño</li>
          <li>Necesitas enviarlo por email rápido</li>
          <li>Trabajas con PDFs escaneados pesados</li>
        </ul>
      </div>
    </section>

    <section id="precios" class="pricing">
      <div class="plan">
        <h3>Gratis</h3>
        <div class="price">0€</div>
        <ul>
          <li>Hasta <b>{FREE_MONTHLY_LIMIT} PDFs/mes</b></li>
          <li>Máx. <b>{FREE_MAX_MB} MB</b> por PDF</li>
          <li>3 calidades (máxima por defecto)</li>
        </ul>
        <div class="tag">Para probar rápido</div>
      </div>

      <div class="plan">
        <h3>Pro</h3>
        <div class="price">9€ / mes</div>
        <ul>
          <li>Hasta <b>{PRO_MONTHLY_LIMIT} PDFs/mes</b></li>
          <li>Máx. <b>{PRO_MAX_MB} MB</b> por PDF</li>
          <li>Para uso frecuente</li>
        </ul>
        <div class="ctaRow" style="margin-top:12px;">
          <a class="btn primary" href="/app">Empezar</a>
        </div>
      </div>

      <div class="plan">
        <h3>Business</h3>
        <div class="price">A medida</div>
        <ul>
          <li>Hasta <b>{BUSINESS_MONTHLY_LIMIT} PDFs/mes</b></li>
          <li><b>MB “ilimitados”</b> (práctico)</li>
          <li>Prioridad alta</li>
        </ul>
        <div class="ctaRow" style="margin-top:12px;">
          <a class="btn secondary" href="/app">Contactar</a>
        </div>
      </div>
    </section>

    <div class="footer">Versión: {APP_VERSION}</div>
  </div>
</body>
</html>
"""


# =========
# HTML: APP (/app)
# =========
APP_HTML = r"""
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>PDF Cleaner — herramienta</title>
  <style>
    :root{
      --bg:#f7f8fa; --card:#ffffff; --text:#0f172a; --muted:#475569;
      --line:#e5e7eb; --shadow:0 10px 30px rgba(0,0,0,0.08);
      --okbg:#ecfeff; --okline:#a5f3fc;
      --errbg:#fff2f2; --errline:#ffd0d0; --err:#7a1b1b;
    }
    *{ box-sizing:border-box; }
    body{
      margin:0; background:var(--bg); color:var(--text);
      font-family:-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial;
    }
    a{ color:inherit; text-decoration:none; }
    .wrap{ max-width:980px; margin:0 auto; padding:22px 16px 70px; }

    .topbar{
      display:flex; align-items:center; justify-content:space-between;
      gap:14px; margin-bottom:14px;
    }
    .brand{ display:flex; align-items:center; gap:10px; font-weight:800; letter-spacing:-0.2px; }
    .badge{
      font-size:12px; padding:6px 10px; border-radius:999px;
      background:#111; color:#fff; display:inline-flex; gap:6px; align-items:center;
    }
    .nav{ display:flex; gap:14px; font-size:13px; color:var(--muted); }

    .hero{
      background:var(--card); border:1px solid var(--line); border-radius:18px;
      padding:22px; box-shadow:var(--shadow);
    }
    h1{ margin:0 0 8px; font-size:38px; line-height:1.08; letter-spacing:-0.8px; }
    @media(max-width:520px){ h1{ font-size:30px; } }
    .sub{ color:var(--muted); font-size:14px; line-height:1.45; margin:0 0 12px; }

    .tool{
      margin-top:14px;
      background:#fff; border:1px solid var(--line); border-radius:18px;
      padding:18px; box-shadow:var(--shadow);
    }
    label{ font-weight:700; display:block; margin-top:12px; font-size:13px; }
    input, select{
      width:100%; margin-top:6px; padding:12px;
      border-radius:10px; border:1px solid #d6d6d6; font-size:14px;
      background:#fff;
    }
    .submit{
      width:100%; margin-top:14px; padding:14px; border-radius:12px;
      border:none; background:#111; color:#fff; font-size:15px; font-weight:800;
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

    .microinfo{
      margin-top:8px;
      font-size:12px; color:var(--muted);
      border-left:3px solid #111;
      padding-left:10px;
      line-height:1.35;
    }
    .footer{ margin-top:18px; color:var(--muted); font-size:12px; text-align:center; }
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
        <a href="/">Landing</a>
      </div>
    </div>

    <section class="hero">
      <h1>Herramienta</h1>
      <p class="sub">Sube tu PDF → lo limpiamos y comprimimos → descargas al momento.</p>
      <div class="hint"><b>Gratis:</b> 5 PDFs/mes · máx. 5 MB · sin registro</div>
    </section>

    <section class="tool">
      <form id="pdfForm" enctype="multipart/form-data">
        <label>Archivo PDF</label>
        <input id="file" type="file" name="file" accept="application/pdf" required>
        <div id="fileName" class="hint">Ningún archivo seleccionado</div>

        <label>Calidad</label>
        <select id="quality" name="quality">
          <option value="screen" selected>Máxima compresión</option>
          <option value="ebook">Equilibrado</option>
          <option value="printer">Alta calidad</option>
        </select>
        <div id="qualityHelp" class="microinfo"></div>

        <button id="submitBtn" class="submit" type="submit">Procesar PDF</button>

        <div id="resultBox" class="result"></div>
        <div id="errBox" class="error"></div>

        <div class="hint">
          Procesamos el archivo temporalmente y se elimina al terminar.
          <br/>Versión: <span id="ver"></span>
        </div>
      </form>
    </section>

    <div class="footer">
      Si un PDF no reduce mucho, puede que ya esté optimizado.
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

    const helpText = {
      "screen": "<b>Máxima compresión:</b> reduce el peso al máximo manteniendo el PDF legible.",
      "ebook": "<b>Equilibrado:</b> buena reducción sin castigar demasiado la calidad.",
      "printer": "<b>Alta calidad:</b> menor reducción; pensado para impresión."
    };

    function setQualityHelp() {
      qualityHelp.innerHTML = helpText[qualitySel.value] || "";
    }
    setQualityHelp();
    qualitySel.addEventListener("change", setQualityHelp);

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

    fileInput.addEventListener("change", () => {
      fileName.textContent = fileInput.files?.[0]?.name || "Ningún archivo seleccionado";
      clearError();
      clearResult();
    });

    async function loadVersion(){
      try{
        const r = await fetch("/version", {cache:"no-store"});
        document.getElementById("ver").textContent = await r.text();
      }catch(e){
        document.getElementById("ver").textContent = "unknown";
      }
    }
    loadVersion();

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      clearError();
      clearResult();

      const f = fileInput.files?.[0];
      if (!f) {
        showError("Selecciona un PDF primero.");
        return;
      }

      // Front limit (Free)
      const MAX_MB = 5;
      if (f.size > MAX_MB * 1024 * 1024) {
        showError("❌ Límite gratis: máximo " + MAX_MB + " MB por PDF.");
        return;
      }

      setLoading(true);

      try {
        const fd = new FormData();
        fd.append("file", f);
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

        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = f.name; // mismo nombre
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


def get_client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def current_month_key(request: Request) -> tuple[str, str]:
    ip = get_client_ip(request)
    month = date.today().strftime("%Y-%m")  # YYYY-MM
    return (ip, month)


def cleanup_old_counters():
    # Si crece demasiado, borramos meses antiguos
    current_month = date.today().strftime("%Y-%m")
    if len(MONTHLY_COUNTER) > 5000:
        to_delete = [k for k in MONTHLY_COUNTER.keys() if k[1] != current_month]
        for k in to_delete:
            MONTHLY_COUNTER.pop(k, None)


@app.get("/version", response_class=PlainTextResponse)
def version():
    return APP_VERSION


# Landing
@app.get("/", response_class=HTMLResponse)
def landing():
    return LANDING_HTML


# App
@app.get("/app", response_class=HTMLResponse)
def app_page():
    return APP_HTML


@app.post("/process")
async def process(
    request: Request,
    file: UploadFile = File(...),
    quality: str = Form("screen"),
):
    # Validación extensión
    if not (file.filename or "").lower().endswith(".pdf"):
        return HTMLResponse("❌ Solo se aceptan PDFs.", status_code=400)

    # Solo 3 opciones de calidad
    allowed_qualities = {"screen", "ebook", "printer"}
    if quality not in allowed_qualities:
        quality = "screen"

    # Contador mensual (FREE)
    cleanup_old_counters()
    key = current_month_key(request)
    used = MONTHLY_COUNTER.get(key, 0)

    if used >= FREE_MONTHLY_LIMIT:
        return HTMLResponse("❌ Límite gratis: 5 PDFs al mes.", status_code=429)

    # Leer contenido (FREE max MB)
    data_in = await file.read()
    if len(data_in) > FREE_MAX_MB * 1024 * 1024:
        return HTMLResponse(f"❌ Límite gratis: máximo {FREE_MAX_MB} MB por PDF.", status_code=413)

    job_id = str(uuid.uuid4())

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        inp = tmpdir / f"{job_id}_input.pdf"
        cleaned = tmpdir / f"{job_id}_cleaned.pdf"
        outp = tmpdir / f"{job_id}_output.pdf"

        inp.write_bytes(data_in)

        try:
            stats = clean_pdf(str(inp), str(cleaned))

            # Siempre: limpiar + comprimir
            compress_with_ghostscript(str(cleaned), str(outp), quality)
            final_path = outp

            if not final_path.exists():
                return HTMLResponse("❌ No se generó el archivo final.", status_code=500)

            data_out = final_path.read_bytes()

        except FileNotFoundError:
            return HTMLResponse(
                "❌ Error: Ghostscript no está disponible en el servidor.",
                status_code=500,
            )
        except Exception as e:
            return HTMLResponse(f"❌ Error procesando el PDF:\n\n{e}", status_code=500)

    # Cuenta uso al final si todo salió bien
    MONTHLY_COUNTER[key] = used + 1

    return Response(
        content=data_out,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{file.filename}"',
            "X-Total-Pages": str(stats.get("total", "")),
            "X-Removed-Pages": str(stats.get("removed", "")),
        },
    )
