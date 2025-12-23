import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pypdf import PdfReader, PdfWriter


def is_blank_page_by_text(page) -> bool:
    text = page.extract_text() or ""
    return text.strip() == ""


def clean_pdf(input_path: str, output_path: str) -> dict:
    reader = PdfReader(input_path)
    writer = PdfWriter()

    removed = 0
    for page in reader.pages:
        if is_blank_page_by_text(page):
            removed += 1
            continue
        writer.add_page(page)

    with open(output_path, "wb") as f:
        writer.write(f)

    return {"total": len(reader.pages), "removed": removed}


def find_ghostscript_exe() -> str | None:
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

    # Ghostscript recomprime y puede reducir imágenes (dependiendo del PDF)
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

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "Ghostscript falló al comprimir.\n"
            f"STDERR:\n{result.stderr}\n"
            f"STDOUT:\n{result.stdout}"
        )


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

    # 1) Limpiar a un temporal
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_clean = os.path.join(tmpdir, "cleaned.pdf")

        stats = clean_pdf(args.input, tmp_clean)

        # 2) Si no hay compresión, movemos el limpio a output
        if not args.compress:
            shutil.copyfile(tmp_clean, args.output)
            in_kb = file_size_kb(args.input)
            out_kb = file_size_kb(args.output)
            print(
                f"✅ OK (solo clean)\n"
                f"📄 Total: {stats['total']} | Eliminadas: {stats['removed']}\n"
                f"📦 Tamaño: {in_kb} KB → {out_kb} KB\n"
                f"📁 Salida: {args.output}"
            )
            return

        # 3) Compresión real con Ghostscript
        compress_with_ghostscript(tmp_clean, args.output, args.quality)

        in_kb = file_size_kb(args.input)
        out_kb = file_size_kb(args.output)
        print(
            f"✅ OK (clean + compress)\n"
            f"📄 Total: {stats['total']} | Eliminadas: {stats['removed']}\n"
            f"⚙️ Calidad: {args.quality}\n"
            f"📦 Tamaño: {in_kb} KB → {out_kb} KB\n"
            f"📁 Salida: {args.output}"
        )


if __name__ == "__main__":
    main()
