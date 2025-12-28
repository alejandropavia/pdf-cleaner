import os
import secrets
import uuid
import tempfile
from pathlib import Path
from datetime import date

from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, Response, PlainTextResponse, RedirectResponse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

import stripe

from clean_pdf import clean_pdf, compress_with_ghostscript
from db import init_db, save_token, get_token, get_used, inc_used


app = FastAPI(title="PDF Cleaner & Compressor")

# =========
# VERSION
# =========
APP_VERSION = "2025-12-27-v13-basic-pro-security"

# =========
# STRIPE
# =========
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")  # opcional
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")  # ej: https://xxxxx.onrender.com

# Nuevos price IDs (recomendado)
STRIPE_PRICE_BASIC = os.getenv("STRIPE_PRICE_BASIC", "")  # 5€
STRIPE_PRICE_PRO = os.getenv("STRIPE_PRICE_PRO", "")      # 9€

# Backward compatibility (si tu landing aún apunta a "business")
STRIPE_PRICE_BUSINESS = os.getenv("STRIPE_PRICE_BUSINESS", "")  # legacy (antes "business")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# =========
# LIMITES (NUEVOS)
# =========
FREE_MAX_MB = 5
FREE_MONTHLY_LIMIT = 3

BASIC_MAX_MB = 15
BASIC_MONTHLY_LIMIT = 50

PRO_MAX_MB = 100
PRO_MONTHLY_LIMIT = 200

# =========
# FILES
# =========
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"


@app.on_event("startup")
def _startup():
    init_db()


# =========
# SECURITY MIDDLEWARE
# =========
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)

        # Security headers (baratos y útiles)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        # HSTS solo tiene sentido si vas siempre en HTTPS (Render sí).
        # No pasa nada por ponerlo.
        resp.headers["Strict-Transport-Security"] = "max-age=15552000; includeSubDomains"

        return resp


app.add_middleware(SecurityHeadersMiddleware)

# Trusted hosts opcional:
# - Si defines ALLOWED_HOSTS, bloquea hosts raros (anti-ataques tontos).
# - Ej: ALLOWED_HOSTS="tudominio.com,.tudominio.com,xxxx.onrender.com"
allowed_hosts_env = os.getenv("ALLOWED_HOSTS", "").strip()
if allowed_hosts_env:
    allowed_hosts = [h.strip() for h in allowed_hosts_env.split(",") if h.strip()]
    if allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)


# =========
# HTML RENDER (replace seguro)
# =========
def _read_template(name: str) -> str:
    p = TEMPLATE_DIR / name
    return p.read_text(encoding="utf-8")


def _apply_vars(html: str) -> str:
    """
    Sustituye placeholders de negocio en DOS formatos:
      {KEY}
      %%KEY%%
    y NO toca llaves del CSS.
    """
    values = {
        # FREE
        "FREE_MONTHLY_LIMIT": str(FREE_MONTHLY_LIMIT),
        "FREE_MAX_MB": str(FREE_MAX_MB),

        # BASIC (5€)
        "BASIC_MONTHLY_LIMIT": str(BASIC_MONTHLY_LIMIT),
        "BASIC_MAX_MB": str(BASIC_MAX_MB),

        # PRO (9€)
        "PRO_MONTHLY_LIMIT": str(PRO_MONTHLY_LIMIT),
        "PRO_MAX_MB": str(PRO_MAX_MB),

        # Compatibilidad si tu HTML aún usa BUSINESS_*
        "BUSINESS_MONTHLY_LIMIT": str(PRO_MONTHLY_LIMIT),
        "BUSINESS_MAX_MB": str(PRO_MAX_MB),

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
            plan = (row["plan"] or "").lower().strip()
            if plan == "basic":
                return BASIC_MAX_MB, BASIC_MONTHLY_LIMIT, "basic"
            if plan == "pro":
                return PRO_MAX_MB, PRO_MONTHLY_LIMIT, "pro"

            # compatibilidad legacy (si hay tokens antiguos "business")
            if plan == "business":
                return PRO_MAX_MB, PRO_MONTHLY_LIMIT, "pro"

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
@app.get("/checkout/basic")
def checkout_basic(request: Request):
    missing = _ensure_stripe_ready()
    if missing or not STRIPE_PRICE_BASIC:
        return HTMLResponse(
            f"❌ Stripe no está configurado. Revisa {missing or 'STRIPE_PRICE_BASIC'}.",
            status_code=500,
        )

    success_url = f"{PUBLIC_BASE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{PUBLIC_BASE_URL}/#precios"

    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": STRIPE_PRICE_BASIC, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return RedirectResponse(session.url, status_code=303)


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


# Backward compatibility: si tu landing todavía llama a /checkout/business
@app.get("/checkout/business")
def checkout_business_legacy(request: Request):
    # Si tienes el viejo PRICE_BUSINESS puesto, lo usamos como PRO para no romper.
    if STRIPE_PRICE_BUSINESS and not STRIPE_PRICE_PRO:
        # Caso legacy: el business antiguo actuará como PRO.
        missing = _ensure_stripe_ready()
        if missing:
            return HTMLResponse(f"❌ Stripe no está configurado. Revisa {missing}.", status_code=500)

        success_url = f"{PUBLIC_BASE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}"
        cancel_url = f"{PUBLIC_BASE_URL}/#precios"

        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": STRIPE_PRICE_BUSINESS, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return RedirectResponse(session.url, status_code=303)

    # Si no, redirigimos al PRO nuevo.
    return RedirectResponse(url="/checkout/pro", status_code=303)


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
            price_id = items[0]["price"]["id"] if items else ""

            if STRIPE_PRICE_BASIC and price_id == STRIPE_PRICE_BASIC:
                plan = "basic"
            elif STRIPE_PRICE_PRO and price_id == STRIPE_PRICE_PRO:
                plan = "pro"
            elif STRIPE_PRICE_BUSINESS and price_id == STRIPE_PRICE_BUSINESS:
                # legacy
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
    token: str = Form(""),
):
    # 1) Validación extensión
    filename = (file.filename or "").strip()
    if not filename.lower().endswith(".pdf"):
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
            return HTMLResponse(f"Has alcanzado el límite Gratis ({FREE_MONTHLY_LIMIT} PDFs/mes).", status_code=429)
        if plan_name == "basic":
            return HTMLResponse(f"Has alcanzado el límite Básico ({BASIC_MONTHLY_LIMIT} PDFs/mes).", status_code=429)
        return HTMLResponse(f"Has alcanzado el límite Pro ({PRO_MONTHLY_LIMIT} PDFs/mes).", status_code=429)

    # 4) Tamaño max
    data_in = await file.read()
    original_bytes = len(data_in)

    if original_bytes > max_mb * 1024 * 1024:
        if plan_name == "free":
            return HTMLResponse(f"Has superado el límite Gratis ({FREE_MAX_MB} MB).", status_code=413)
        if plan_name == "basic":
            return HTMLResponse(f"Has superado el límite Básico ({BASIC_MAX_MB} MB).", status_code=413)
        return HTMLResponse(f"Has superado el límite Pro ({PRO_MAX_MB} MB).", status_code=413)

    job_id = str(uuid.uuid4())
    stats = {"total": "", "removed": ""}

    # Procesamos en carpeta temporal (se borra sola al terminar)
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
            final_bytes = len(data_out)

        except FileNotFoundError:
            return HTMLResponse("❌ Error: Ghostscript no está disponible en el servidor.", status_code=500)
        except Exception as e:
            return HTMLResponse(f"❌ Error procesando el PDF:\n\n{e}", status_code=500)

    # 5) % reducción
    if original_bytes <= 0:
        reduction_pct = 0.0
    else:
        reduction_pct = max(0.0, (1.0 - (final_bytes / original_bytes)) * 100.0)

    # 6) Cuenta uso (solo si todo OK)
    inc_used(key_type, key_value, m)

    # 7) Respuesta (sin cache, sin historias)
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "X-Total-Pages": str(stats.get("total", "")),
        "X-Removed-Pages": str(stats.get("removed", "")),
        "X-Input-Bytes": str(original_bytes),
        "X-Output-Bytes": str(final_bytes),
        "X-Reduction-Pct": f"{reduction_pct:.1f}",
    }

    return Response(
        content=data_out,
        media_type="application/pdf",
        headers=headers,
    )
