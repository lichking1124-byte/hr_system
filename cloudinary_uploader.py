import cloudinary
import cloudinary.uploader
from config import CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET

cloudinary.config(
    cloud_name=CLOUDINARY_CLOUD_NAME,
    api_key=CLOUDINARY_API_KEY,
    api_secret=CLOUDINARY_API_SECRET,
    secure=True
)

def upload_file(file_storage, folder="applicant_documents"):
    result = cloudinary.uploader.upload(
        file_storage,
        folder=folder,
        resource_type="raw"
    )
    return result["secure_url"]