import os
from dotenv import load_dotenv
load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SECRET_KEY = "hrschool2026"

CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")

CAMPUSES = [
    "Main Campus - Malolos",
    "Bustos Campus",
    "Sarmiento Campus - San Jose Del Monte",
    "Meneses Campus - Bulakan",
    "Hagonoy Campus",
    "San Rafael Campus"
]