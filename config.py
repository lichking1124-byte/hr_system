import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SECRET_KEY = "hrschool2026"

CAMPUSES = [
    "Main Campus - Malolos",
    "Bustos Campus",
    "Sarmiento Campus - San Jose Del Monte",
    "Meneses Campus - Bulakan",
    "Hagonoy Campus",
    "San Rafael Campus"
]