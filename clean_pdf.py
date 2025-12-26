import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Optional

from pypdf import PdfReader, PdfWriter


# =========================
# BLANK PAGE DETECTION (SAFE)
# =========================

def _safe_extract_text(page) -> str:
    try:
        txt = page.extract_text() or ""
        return txt
    except Exception:
        return ""


def _has_xobject_images_or_forms(page) -> bool:
    """
    Detecta si la página tiene XObjects (imágenes/formularios).
    Si hay XObject, NO la consideramos "en blanco".
    """
    try:
        resources = page.get("/Resources") or {}
        xobj = resources.get("/XObject")
        if not xobj:
            return False

        # xobj puede ser indirecto; iterar en keys es suficiente
        try:
            return len(xobj.keys()) > 0
        except Exception:
            # si no tiene keys, asumimos que existe algo
            return True
    except Exception:
        return False


def _content_stream_bytes(page) -> bytes:
    """
    Devuelve bytes del content stream de la página.
    Si no hay content stream, devuelve b"".
    """
    try:
        contents = page.get_contents()
        if contents is None:
            return b""

        # En pypdf, contents puede ser un objeto o una lista de objetos
        if isinstance(contents, list):
            data = b""
            for c in contents:
                try:
                    data += c.get_data() or b""
                except Exception:
                    pass
            return data

        try:
            return contents.get_data() or b""
        except Exception:
            return b""
    except Exception:
        return b""


def is_probably_blank_page(page) -> bool:
    """
    Heurística CONSERVADORA:
    Solo devuelve True si estamos muy seguros de que está vacía.

    - Si hay texto extraíble -> NO es blanca
    - Si hay XObjects (imágenes/forms) -> NO es blanca
    - Si el content stream está vacío o casi vacío -> blanca
    """
    text = _safe_extract_text(page).strip()
    if text:
        return False

    # Si hay imágenes/xobjects, NO borrar (aunque no haya texto extraíble)
    if _has_xobject_images_or_forms(page):
        return False

    data = _content_stream_bytes(page)
    if not data:
        return True

    stripped = data.strip()
    if len(stripped) == 0:
        return True

    # Umbral conservador: si hay muy poco contenido en stream y no hay texto ni xobject,
    # normalmente es una página vacía o casi vacía.
    # (Si te pasas de agresivo aquí, borrarías contenido real.)
    if len(stripped) < 30:
        return True

    return False


def clean_pdf(input_path: str, output_path: str) -> dict:
    """
    Limpia páginas "probablemente en blanco" (muy conservador).
    Failsafe: si el resultado quedaría sin páginas, NO borra nada.
    """
    reader = PdfReader(input_path)
    writer = PdfWriter()

    total = len(reader.pages)
    removed = 0
    kept_pages = 0

    for page in reader.pages:
        if is_probably_blank_page(page):
            removed += 1
            continue
        writer.add_page(page)
        kept_pages += 1

    # FAILSAFE: si nos quedamos sin páginas, NO eliminamos nada
    if kept_pages == 0 and total > 0:
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        removed = 0
        kept_pages = total

    with open(output_path, "wb") as f:
        writer.write(f)

    return {"total": total, "removed": removed, "remaining": kept_pages}


# =========================
# GHOSTSCRIPT
# =========================

def find_ghostscript_exe() -> Optional[str]:
    """
    Busca Ghostscript en PATH. En Windows suele ser: gswin64c o gswin32c.
    En Mac/Linux: gs
    """
    candidates = ["gs", "gswin64c", "gswin32c"]
    for c in candidates:
        path = shutil.which(c)
        if path:
            return path
    return None


def compress_with_ghostscript(input_pdf: str, output_pdf: str, quality: str) -> None:
    """
    quality: screen | ebook | printer | prepress
    Añadimos timeout para que no se quede colgado con PDFs raros.
    """
    gs = find_ghostscript_exe()
    if not gs:
        raise RuntimeError(
            "Ghostscript no está instalado o no está en PATH.\n"
            "Instálalo y vuelve a intentar:\n"
            "- Windows: instala 'Ghostscript' y reinicia VS Code.\n"
            "  (Luego 'gswin64c' debería funcionar en terminal)\n"
            "- Mac: brew install ghostscript\n"
            "- Linux: sudo apt-get install ghostscript\n"
        )

    cmd = [
        gs,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        f"-dPDFSETTINGS=/{quality}",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        f"-sOutputFile={output_pdf}",
        input_pdf,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90,   # ✅ seguridad (Render/servers)
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Ghostscript tardó demasiado (timeout). Prueba con otra calidad o un PDF más pequeño.")

    if result.returncode != 0:
        raise RuntimeError(
            "Ghostscript falló al comprimir.\n"
            f"STDERR:\n{result.stderr}\n"
            f"STDOUT:\n{result.stdout}"
        )


# =========================
# CLI (local)
# =========================

def file_size_kb(path: str) -> int:
    return int(os.path.getsize(path) / 1024)


def main():
    parser = argparse.ArgumentParser(description="PDF Cleaner & Compressor (MVP)")
    parser.add_argument("input", help="PDF de entrada (ej: input.pdf)")
    parser.add_argument("output", help="PDF de salida (ej: salida.pdf)")
    parser.add_argument(
        "--compress",
        action="store_true",
        help="Activa compresión real con Ghostscript",
    )
    parser.add_argument(
        "--quality",
        default="ebook",
        choices=["screen", "ebook", "printer", "prepress"],
        help="Nivel de compresión (screen=agresivo, prepress=mejor calidad)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"❌ No existe el archivo de entrada: {args.input}")
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_clean = os.path.join(tmpdir, "cleaned.pdf")

        stats = clean_pdf(args.input, tmp_clean)

        if not args.compress:
            shutil.copyfile(tmp_clean, args.output)
            in_kb = file_size_kb(args.input)
            out_kb = file_size_kb(args.output)
            print(
                f"✅ OK (solo clean)\n"
                f"📄 Total: {stats['total']} | Eliminadas: {stats['removed']} | Restantes: {stats['remaining']}\n"
                f"📦 Tamaño: {in_kb} KB → {out_kb} KB\n"
                f"📁 Salida: {args.output}"
            )
            return

        compress_with_ghostscript(tmp_clean, args.output, args.quality)

        in_kb = file_size_kb(args.input)
        out_kb = file_size_kb(args.output)
        print(
            f"✅ OK (clean + compress)\n"
            f"📄 Total: {stats['total']} | Eliminadas: {stats['removed']} | Restantes: {stats['remaining']}\n"
            f"⚙️ Calidad: {args.quality}\n"
            f"📦 Tamaño: {in_kb} KB → {out_kb} KB\n"
            f"📁 Salida: {args.output}"
        )


if __name__ == "__main__":
    main()
