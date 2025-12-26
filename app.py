import os
import secrets
import uuid
import tempfile
from pathlib import Path
from datetime import date

from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import (
    HTMLResponse,
    Response,
    PlainTextResponse,
    RedirectResponse,
)

import stripe

from clean_pdf import clean_pdf, compress_with_ghostscript
from db import init_db, save_token, get_token, get_used, inc_used


app = FastAPI(title="PDF Cleaner & Compressor")

# =========
# VERSION
# =========
APP_VERSION = "2025-12-26-v11-stable"

# =========
# STRIPE
# =========
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
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
# PATHS
# =========
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"


@app.on_event("startup")
def startup():
    init_db()


# =========
# HTML HELPERS
# =========
def _read_template(name: str) -> str:
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


def _apply_vars(html: str) -> str:
    values = {
        "FREE_MONTHLY_LIMIT": str(FREE_MONTHLY_LIMIT),
        "FREE_MAX_MB": str(FREE_MAX_MB),
        "PRO_MONTHLY_LIMIT": str(PRO_MONTHLY_LIMIT),
        "PRO_MAX_MB": str(PRO_MAX_MB),
        "BUSINESS_MONTHLY_LIMIT": str(BUSINESS_MONTHLY_LIMIT),
        "BUSINESS_MAX_MB": str(BUSINESS_MAX_MB),
        "APP_VERSION": APP_VERSION,
    }
    for k, v in values.items():
        html = html.replace(f"{{{k}}}", v)
        html = html.replace(f"%%{k}%%", v)
    return html


def render_landing_html() -> str:
    return _apply_vars(_read_template("landing.html"))


def render_app_html(token: str = "") -> str:
    html = _apply_vars(_read_template("app.html"))
    return html.replace("%%TOKEN%%", token).replace("{TOKEN}", token)


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
    if token:
        row = get_token(token)
        if row:
            if row["plan"] == "pro":
                return PRO_MAX_MB, PRO_MONTHLY_LIMIT, "pro"
            if row["plan"] == "business":
                return BUSINESS_MAX_MB, BUSINESS_MONTHLY_LIMIT, "business"
    return FREE_MAX_MB, FREE_MONTHLY_LIMIT, "free"


def create_access_token(plan: str, email: str = "") -> str:
    t = secrets.token_urlsafe(24)
    save_token(token=t, plan=plan, email=email or "")
    return t


def stripe_ready():
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


@app.get("/app", response_class=HTMLResponse)
def app_page(token: str = ""):
    return render_app_html(token)


@app.get("/free")
def go_free():
    return RedirectResponse("/app", status_code=303)


@app.get("/try")
def go_try():
    return RedirectResponse("/app", status_code=303)


# =========
# STRIPE
# =========
@app.get("/checkout/pro")
def checkout_pro():
    missing = stripe_ready()
    if missing or not STRIPE_PRICE_PRO:
        return HTMLResponse(f"❌ Stripe mal configurado: {missing}", 500)

    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": STRIPE_PRICE_PRO, "quantity": 1}],
        success_url=f"{PUBLIC_BASE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{PUBLIC_BASE_URL}/#precios",
    )
    return RedirectResponse(session.url, status_code=303)


@app.get("/checkout/business")
def checkout_business():
    missing = stripe_ready()
    if missing or not STRIPE_PRICE_BUSINESS:
        return HTMLResponse(f"❌ Stripe mal configurado: {missing}", 500)

    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": STRIPE_PRICE_BUSINESS, "quantity": 1}],
        success_url=f"{PUBLIC_BASE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{PUBLIC_BASE_URL}/#precios",
    )
    return RedirectResponse(session.url, status_code=303)


@app.get("/success")
def success(session_id: str = ""):
    if not (STRIPE_SECRET_KEY and session_id):
        return HTMLResponse("Pago recibido.")

    sess = stripe.checkout.Session.retrieve(session_id, expand=["line_items"])
    items = sess["line_items"]["data"]

    plan = "pro"
    if items and items[0]["price"]["id"] == STRIPE_PRICE_BUSINESS:
        plan = "business"

    email = (sess.get("customer_details") or {}).get("email") or ""
    token = create_access_token(plan, email)

    return RedirectResponse(f"/app?token={token}", status_code=303)


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
    if not file.filename.lower().endswith(".pdf"):
        return HTMLResponse("❌ Solo PDFs.", 400)

    if quality not in {"screen", "ebook", "printer"}:
        quality = "screen"

    max_mb, monthly_limit, plan = plan_limits_for_token(token)
    month = month_str()

    if plan == "free":
        key_type, key_value = "ip", get_client_ip(request)
    else:
        key_type, key_value = "token", token

    if get_used(key_type, key_value, month) >= monthly_limit:
        return HTMLResponse("❌ Límite mensual alcanzado.", 429)

    data_in = await file.read()
    original_bytes = len(data_in)

    if original_bytes > max_mb * 1024 * 1024:
        return HTMLResponse(f"❌ Máximo {max_mb} MB.", 413)

    stats = {"total": "", "removed": ""}

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        inp = tmp / "in.pdf"
        cleaned = tmp / "clean.pdf"
        outp = tmp / "out.pdf"

        inp.write_bytes(data_in)

        stats = clean_pdf(str(inp), str(cleaned))
        compress_with_ghostscript(str(cleaned), str(outp), quality)

        data_out = outp.read_bytes()
        final_bytes = len(data_out)

    reduction_pct = (
        0.0 if original_bytes == 0
        else max(0.0, (1 - final_bytes / original_bytes) * 100)
    )

    inc_used(key_type, key_value, month)

    return Response(
        content=data_out,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{file.filename}"',
            "X-Total-Pages": str(stats.get("total", "")),
            "X-Removed-Pages": str(stats.get("removed", "")),
            "X-Input-Bytes": str(original_bytes),
            "X-Output-Bytes": str(final_bytes),
            "X-Reduction-Pct": f"{reduction_pct:.1f}",
        },
    )
