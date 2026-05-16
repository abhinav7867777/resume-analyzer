# ============================================================
# main.py — Resume Analyzer (Upgraded)
# Routes: / (upload+analyze), /rewrite (AJAX), /download (TXT)
# ============================================================

from flask import (
    Flask, request, render_template,
    session, jsonify, Response
)
import fitz          # PyMuPDF
from analyse_pdf import analyse_resume_gemini, rewrite_resume_section
import os, json

# Optional OCR support — graceful fallback if Tesseract not installed
try:
    import pytesseract
    from PIL import Image
    import io
    # Point pytesseract to the Tesseract executable (Windows default path)
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

# DOCX support
try:
    from docx import Document as DocxDocument
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", os.urandom(24))   # Set SECRET_KEY in Render env vars
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


# ── Text extraction helpers ──────────────────────────────────

def extract_from_pdf(pdf_path):
    """Normal text extraction from a digital PDF using PyMuPDF."""
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text.strip()


def extract_from_pdf_ocr(pdf_path):
    """
    OCR extraction for scanned/image PDFs.
    Renders each page via PyMuPDF → PIL image → Tesseract OCR.
    Requires Tesseract to be installed:
      Windows: https://github.com/UB-Mannheim/tesseract/wiki
    """
    if not OCR_AVAILABLE:
        return ""
    doc = fitz.open(pdf_path)
    full_text = ""
    for page in doc:
        # Render at 300 DPI for best OCR accuracy
        mat = fitz.Matrix(300 / 72, 300 / 72)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_bytes))
        page_text = pytesseract.image_to_string(img, lang="eng")
        full_text += page_text + "\n"
    doc.close()
    return full_text.strip()


def extract_from_docx(docx_path):
    """Extract all paragraph text from a .docx Word file."""
    if not DOCX_AVAILABLE:
        return ""
    doc = DocxDocument(docx_path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    # Also extract text from tables
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    paragraphs.append(cell.text.strip())
    return "\n".join(paragraphs).strip()


def extract_text_smart(file_path, filename):
    """
    Smart router: picks the right extractor based on file extension.
    For scanned PDFs, auto-falls-back to OCR if normal extraction yields < 50 chars.
    Returns (text, method_used, warning_message)
    """
    ext = os.path.splitext(filename)[1].lower()

    if ext == ".docx":
        text = extract_from_docx(file_path)
        return text, "docx", None

    if ext == ".pdf":
        text = extract_from_pdf(file_path)
        if len(text) >= 50:
            return text, "pdf", None

        # Scanned PDF detected — try OCR
        if OCR_AVAILABLE:
            ocr_text = extract_from_pdf_ocr(file_path)
            if ocr_text:
                return ocr_text, "ocr", "Scanned PDF detected — OCR used to extract text."
            return "", "ocr_failed", "OCR ran but found no text. Try a clearer scan."
        else:
            return "", "no_ocr", (
                "This appears to be a scanned PDF but Tesseract OCR is not installed. "
                "Install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki"
            )

    return "", "unsupported", f"Unsupported file type: {ext}"


# ── Home: Upload + Analyze ───────────────────────────────────
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        resume_file     = request.files.get("resume")
        job_description = request.form.get("job_description", "").strip()

        ALLOWED = {".pdf", ".docx"}
        ext = os.path.splitext(resume_file.filename)[1].lower()
        if not resume_file or ext not in ALLOWED:
            return render_template("index.html",
                result=None, error="Please upload a PDF or DOCX file.")

        if not job_description:
            return render_template("index.html",
                result=None, error="Please enter the job description.")

        # Save file
        safe_filename = resume_file.filename.replace(" ", "_")
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
        resume_file.save(file_path)

        # Smart text extraction
        resume_content, method, warning = extract_text_smart(file_path, safe_filename)

        if not resume_content.strip():
            return render_template("index.html",
                result=None,
                error=warning or "Could not extract any text from the file.")

        # AI Analysis — returns dict
        result = analyse_resume_gemini(resume_content, job_description)

        # Store in session for editor
        session['resume_text'] = resume_content
        session['job_description'] = job_description
        session['filename'] = safe_filename

        return render_template("index.html",
            result=result,
            filename=safe_filename,
            ocr_used=(method == "ocr"),
            ocr_warning=warning,
            error=result.get("error") if result.get("error") else None)

    return render_template("index.html", result=None, error=None)


# ── AJAX: AI rewrite (full or section) ──────────────────────
@app.route("/rewrite", methods=["POST"])
def rewrite():
    data          = request.get_json(force=True)
    resume_text   = data.get("resume_text") or session.get("resume_text", "")
    suggestions   = data.get("suggestions", [])
    section       = data.get("section", "full")

    if not resume_text:
        return jsonify({"error": "No resume text provided."}), 400

    improved = rewrite_resume_section(resume_text, suggestions, section)
    return jsonify({"improved_text": improved})


# ── Download edited resume as .txt ──────────────────────────
@app.route("/download", methods=["POST"])
def download():
    data        = request.get_json(force=True)
    edited_text = data.get("text", "")
    filename    = data.get("filename", "improved_resume")

    # Sanitize filename
    safe_name = "".join(c for c in filename if c.isalnum() or c in " _-").strip()
    safe_name = safe_name.replace(" ", "_") or "improved_resume"

    response = Response(
        edited_text,
        mimetype="text/plain",
        headers={
            "Content-Disposition": f"attachment; filename={safe_name}_improved.txt"
        }
    )
    return response


if __name__ == "__main__":
    print("=" * 52)
    print("  Resume Analyzer — AI Powered (v2)")
    print("  URL: http://localhost:5000")
    print("  Features: Score + Editor + Download")
    print("=" * 52)
    app.run(debug=True)