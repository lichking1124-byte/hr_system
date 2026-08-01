import pdfplumber
import requests
import tempfile
import os

def extract_text_from_pdf(file_path_or_url):
    text = ""
    temp_file = None
    try:
        if file_path_or_url.startswith("http://") or file_path_or_url.startswith("https://"):
            response = requests.get(file_path_or_url)
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            temp_file.write(response.content)
            temp_file.close()
            path_to_open = temp_file.name
        else:
            path_to_open = file_path_or_url

        with pdfplumber.open(path_to_open) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        text = f"Could not read file: {str(e)}"
    finally:
        if temp_file and os.path.exists(temp_file.name):
            os.remove(temp_file.name)
    return text