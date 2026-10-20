import io
import requests
import pdfplumber


def _load_bytes(file_path_or_url):
    if file_path_or_url.startswith(("http://", "https://")):
        response = requests.get(file_path_or_url, timeout=30)
        response.raise_for_status()
        return response.content
    with open(file_path_or_url, "rb") as f:
        return f.read()


def _pdf_text(data):
    text = ""
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text


def _docx_text(data):
    import mammoth
    return mammoth.extract_raw_text(io.BytesIO(data)).value


def extract_text_from_pdf(file_path_or_url):
    """
    Returns the text of a PDF or a Word (.docx) file.
    (The name is kept so the rest of the app needs no change.)

    The file type is detected from the file's first bytes, not its name.
    If a file cannot be read (a scan, a photo, a damaged file) this returns an
    EMPTY string, never an error message, so the screening rules can flag the
    document for HR instead of treating an error message as applicant text.
    """
    try:
        data = _load_bytes(file_path_or_url)
        if data.startswith(b"%PDF"):
            return _pdf_text(data)
        if data.startswith(b"PK"):          # .docx files are zip archives
            return _docx_text(data)
        return ""
    except Exception as e:
        print(f"Could not read file {file_path_or_url[:80]}: {e}")
        return ""
