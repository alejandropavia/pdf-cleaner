import os
import secrets
import uuid
import tempfile
from pathlib import Path
from datetime import date

from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, Response, PlainTextResponse, RedirectResponse

import stripe

from clean_pdf import clean_pdf, compress_with_ghostscript
from db import init_db, save_token, get_token, get_used, inc_used


app = FastAPI(title="PDF Cleaner & Compressor")

# =========
# VERSION
# =========
APP_VERSION = "2025-12-26-v11-free-try-fixed"

# =========
# STRIPE
# =========
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")  # opcional
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")  # ej: https://xxxxx.onrender.com
STRIPE_PRICE_PRO = os.getenv("STRIPE_PRICE_PRO", "")
STRIPE_PRICE_BUSINESS = os.getenv("STRIPE_PRICE_BUSINESS", "")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# =========
# LIMITES
# =========
FREE_MAX_MB = 5
FREE_MONTHLY_LIMIT = 5

PRO_MAX_MB = 15
PRO_MONTHLY_LIMIT = 50

BUSINESS_MAX_MB = 60
BUSINESS_MONTHLY_LIMIT = 200

# =========
# FILES
# =========
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"


@app.on_event("startup")
def _startup():
    init_db()


# =========
# HTML RENDER (replace seguro)
# =========
def _read_template(name: str) -> str:
    p = TEMPLATE_DIR / name
    return p.read_text(encoding="utf-8")


def _apply_vars(html: str) -> str:
    """
    Sustituye placeholders de negocio en DOS formatos:
      {FREE_MAX_MB}
      %%FREE_MAX_MB%%
    y NO toca llaves del CSS.
    """
    values = {
        "FREE_MONTHLY_LIMIT": str(FREE_MONTHLY_LIMIT),
        "FREE_MAX_MB": str(FREE_MAX_MB),
        "PRO_MONTHLY_LIMIT": str(PRO_MONTHLY_LIMIT),
        "PRO_MAX_MB": str(PRO_MAX_MB),
        "BUSINESS_MONTHLY_LIMIT": str(BUSINESS_MONTHLY_LIMIT),
        "BUSINESS_MAX_MB": str(BUSINESS_MAX_MB),
        "APP_VERSION": str(APP_VERSION),
    }

    for key, val in values.items():
        html = html.replace(f"{{{key}}}", val)   # {KEY}
        html = html.replace(f"%%{key}%%", val)   # %%KEY%%
    return html


def render_landing_html() -> str:
    return _apply_vars(_read_template("landing.html"))


def render_app_html(token: str = "") -> str:
    """
    Renderiza app.html y, si hay token, lo inyecta para que el formulario pueda enviarlo.
    En app.html puedes usar %%TOKEN%% o {TOKEN}.
    """
    html = _apply_vars(_read_template("app.html"))
    token = token or ""
    html = html.replace("%%TOKEN%%", token).replace("{TOKEN}", token)
    return html


# =========
# UTILS
# =========
def get_client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def month_str() -> str:
    return date.today().strftime("%Y-%m")


def plan_limits_for_token(token: str):
    """
    Devuelve (max_mb, monthly_limit, plan_name) según token en DB.
    Si no hay token válido -> Free.
    """
    if token:
        row = get_token(token)
        if row:
            plan = row["plan"]
            if plan == "pro":
                return PRO_MAX_MB, PRO_MONTHLY_LIMIT, "pro"
            if plan == "business":
                return BUSINESS_MAX_MB, BUSINESS_MONTHLY_LIMIT, "business"
    return FREE_MAX_MB, FREE_MONTHLY_LIMIT, "free"


def create_access_token(plan: str, email: str = "") -> str:
    t = secrets.token_urlsafe(24)
    save_token(token=t, plan=plan, email=email or "")
    return t


def _ensure_stripe_ready():
    if not STRIPE_SECRET_KEY:
        return "STRIPE_SECRET_KEY"
    if not PUBLIC_BASE_URL:
        return "PUBLIC_BASE_URL"
    return ""


# =========
# ROUTES
# =========
@app.get("/version", response_class=PlainTextResponse)
def version():
    return APP_VERSION


@app.get("/", response_class=HTMLResponse)
def landing():
    return render_landing_html()


# Rutas para tus botones /free y /try (evitan que el HTML “no haga nada”)
@app.get("/free")
def go_free():
    return RedirectResponse(url="/app", status_code=303)


@app.get("/try")
def go_try():
    return RedirectResponse(url="/app", status_code=303)


@app.get("/app", response_class=HTMLResponse)
def app_page(token: str = ""):
    return HTMLResponse(render_app_html(token=token))


# =========
# STRIPE CHECKOUT
# =========
@app.get("/checkout/pro")
def checkout_pro(request: Request):
    missing = _ensure_stripe_ready()
    if missing or not STRIPE_PRICE_PRO:
        return HTMLResponse(
            f"❌ Stripe no está configurado. Revisa {missing or 'STRIPE_PRICE_PRO'}.",
            status_code=500,
        )

    success_url = f"{PUBLIC_BASE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{PUBLIC_BASE_URL}/#precios"

    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": STRIPE_PRICE_PRO, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return RedirectResponse(session.url, status_code=303)


@app.get("/checkout/business")
def checkout_business(request: Request):
    missing = _ensure_stripe_ready()
    if missing or not STRIPE_PRICE_BUSINESS:
        return HTMLResponse(
            f"❌ Stripe no está configurado. Revisa {missing or 'STRIPE_PRICE_BUSINESS'}.",
            status_code=500,
        )

    success_url = f"{PUBLIC_BASE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{PUBLIC_BASE_URL}/#precios"

    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": STRIPE_PRICE_BUSINESS, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return RedirectResponse(session.url, status_code=303)


@app.get("/success")
def success(session_id: str = ""):
    """
    Verifica la sesión, detecta el plan y crea un token PERSISTENTE en SQLite.
    Luego redirige a /app?token=...
    """
    if not (STRIPE_SECRET_KEY and session_id):
        return HTMLResponse("✅ Pago recibido. (No se pudo verificar session_id.)")

    try:
        sess = stripe.checkout.Session.retrieve(session_id, expand=["line_items"])

        plan = "pro"
        try:
            items = sess["line_items"]["data"]
            if items and items[0]["price"]["id"] == STRIPE_PRICE_BUSINESS:
                plan = "business"
            elif items and items[0]["price"]["id"] == STRIPE_PRICE_PRO:
                plan = "pro"
        except Exception:
            pass

        email = (sess.get("customer_details") or {}).get("email") or ""
        token = create_access_token(plan=plan, email=email)

        return RedirectResponse(url=f"/app?token={token}", status_code=303)

    except Exception as e:
        return HTMLResponse(f"✅ Pago recibido, pero error verificando Stripe:\n\n{e}")


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """
    Opcional para futuro (cancelaciones/renovaciones).
    Para el MVP no dependemos de esto.
    """
    if not (STRIPE_WEBHOOK_SECRET and STRIPE_SECRET_KEY):
        return Response(status_code=200)

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        _ = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception:
        return Response(status_code=400)

    return Response(status_code=200)


# =========
# PDF PROCESS
# =========
@app.post("/process")
async def process(
    request: Request,
    file: UploadFile = File(...),
    quality: str = Form("screen"),
    token: str = Form(""),  # IMPORTANTE: lo recibimos desde el FORM (hidden input)
):
    # 1) Validación extensión
    if not (file.filename or "").lower().endswith(".pdf"):
        return HTMLResponse("❌ Solo se aceptan PDFs.", status_code=400)

    # 2) Calidad
    allowed_qualities = {"screen", "ebook", "printer"}
    if quality not in allowed_qualities:
        quality = "screen"

    # 3) Plan + límites
    max_mb, monthly_limit, plan_name = plan_limits_for_token(token)
    m = month_str()

    if plan_name == "free":
        key_type = "ip"
        key_value = get_client_ip(request)
    else:
        key_type = "token"
        key_value = token

    used = get_used(key_type, key_value, m)
    if used >= monthly_limit:
        if plan_name == "free":
            return HTMLResponse(f"❌ Límite gratis: {FREE_MONTHLY_LIMIT} PDFs al mes.", status_code=429)
        if plan_name == "pro":
            return HTMLResponse(f"❌ Límite Pro: {PRO_MONTHLY_LIMIT} PDFs al mes.", status_code=429)
        return HTMLResponse(f"❌ Límite Business: {BUSINESS_MONTHLY_LIMIT} PDFs al mes.", status_code=429)

    # 4) Tamaño max
    data_in = await file.read()
    if len(data_in) > max_mb * 1024 * 1024:
        if plan_name == "free":
            return HTMLResponse(f"❌ Límite gratis: máximo {FREE_MAX_MB} MB por PDF.", status_code=413)
        if plan_name == "pro":
            return HTMLResponse(f"❌ Límite Pro: máximo {PRO_MAX_MB} MB por PDF.", status_code=413)
        return HTMLResponse(f"❌ Límite Business: máximo {BUSINESS_MAX_MB} MB por PDF.", status_code=413)

    job_id = str(uuid.uuid4())

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        inp = tmpdir / f"{job_id}_input.pdf"
        cleaned = tmpdir / f"{job_id}_cleaned.pdf"
        outp = tmpdir / f"{job_id}_output.pdf"

        inp.write_bytes(data_in)

        try:
            stats = clean_pdf(str(inp), str(cleaned))
            compress_with_ghostscript(str(cleaned), str(outp), quality)

            if not outp.exists():
                return HTMLResponse("❌ No se generó el archivo final.", status_code=500)

            data_out = outp.read_bytes()

        except FileNotFoundError:
            return HTMLResponse("❌ Error: Ghostscript no está disponible en el servidor.", status_code=500)
        except Exception as e:
            return HTMLResponse(f"❌ Error procesando el PDF:\n\n{e}", status_code=500)

    # 5) Cuenta uso (solo si todo OK)
    inc_used(key_type, key_value, m)

    return Response(
        content=data_out,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{file.filename}"',
            "X-Total-Pages": str(stats.get("total", "")),
            "X-Removed-Pages": str(stats.get("removed", "")),
        },
    )
