from groq import Groq
from PIL import Image, ImageDraw, ImageFont
import textwrap
import os
from config import GROQ_API_KEY

client = Groq(api_key=GROQ_API_KEY)

# BulSU Official Colors
PLUM = (103, 30, 30)        # #671E1E Persian Plum
GOLD = (255, 235, 91)       # #FFEB5B Official Gold
WHITE = (255, 255, 255)
LIGHT_PLUM = (140, 60, 60)
DARK_PLUM = (70, 15, 15)

FONT_PATH = "C:\\Windows\\Fonts\\arial.ttf"
FONT_BOLD = "C:\\Windows\\Fonts\\arialbd.ttf"

def get_font(size, bold=False):
    try:
        return ImageFont.truetype(FONT_BOLD if bold else FONT_PATH, size)
    except:
        return ImageFont.load_default()

def generate_ad_text(job):
    prompt = f"""
Write a short professional job advertisement for Bulacan State University for:
Position: {job['title']}
Campus: {job['campus']}
Skills needed: {job['skills']}
Experience: {job['experience']}
Education: {job['education']}

Keep it under 50 words. Make it clear and professional.
"""
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )
    return response.choices[0].message.content.strip()

def remove_bg(logo_path):
    try:
        with open(logo_path, "rb") as f:
            data = f.read()
        output = remove(data)
        from io import BytesIO
        logo = Image.open(BytesIO(output)).convert("RGBA")
        return logo
    except Exception as e:
        print(f"BG removal failed: {e}")
        return None

def generate_job_ad(job):
    os.makedirs("static/ads", exist_ok=True)

    W, H = 850, 650
    img = Image.new("RGB", (W, H), color=WHITE)
    draw = ImageDraw.Draw(img)

    # ── Left sidebar (Persian Plum) ──
    draw.rectangle([0, 0, 200, H], fill=PLUM)

    # Gold accent stripe on sidebar
    draw.rectangle([185, 0, 200, H], fill=GOLD)

    # ── Top bar (Dark Plum) ──
    draw.rectangle([200, 0, W, 70], fill=DARK_PLUM)

    # ── Bottom bar (Persian Plum) ──
    draw.rectangle([200, 570, W, H], fill=PLUM)

    # ── BulSU Logo on sidebar ──
    logo_path = "static/bulsulogo.jfif"
    logo = remove_bg(logo_path)
    if logo:
        logo = logo.resize((160, 160))
        img.paste(logo, (20, 30), logo)
    else:
        draw.text((100, 100), "BulSU", fill=GOLD,
                  anchor="mm", font=get_font(28, bold=True))

    # University name on sidebar
    draw.text((100, 210), "BULACAN", fill=GOLD,
              anchor="mm", font=get_font(13, bold=True))
    draw.text((100, 228), "STATE", fill=GOLD,
              anchor="mm", font=get_font(13, bold=True))
    draw.text((100, 246), "UNIVERSITY", fill=GOLD,
              anchor="mm", font=get_font(13, bold=True))

    # Divider on sidebar
    draw.rectangle([20, 260, 180, 263], fill=GOLD)

    # Campus name on sidebar
    campus_lines = textwrap.wrap(job['campus'], width=14)
    y_camp = 275
    for line in campus_lines:
        draw.text((100, y_camp), line, fill=WHITE,
                  anchor="mm", font=get_font(11))
        y_camp += 16

    # ── Top bar text ──
    draw.text((530, 35), "HUMAN RESOURCE MANAGEMENT OFFICE",
              fill=GOLD, anchor="mm", font=get_font(13, bold=True))

    # ── "We are Hiring!" ──
    draw.text((530, 110), "We are", fill=PLUM,
              anchor="mm", font=get_font(36))
    draw.text((530, 155), "Hiring!", fill=GOLD,
              anchor="mm", font=get_font(52, bold=True))

    # Gold underline
    draw.rectangle([310, 178, 750, 182], fill=GOLD)

    # ── Position Title ──
    draw.text((530, 210), job['title'],
              fill=PLUM, anchor="mm", font=get_font(28, bold=True))

    # Department line
    dept = job.get('other_requirements', '') or job['campus']
    draw.text((530, 245), f"For the {job['campus']}",
              fill=LIGHT_PLUM, anchor="mm", font=get_font(13))

    # ── Qualifications Section ──
    draw.text((220, 275), "QUALIFICATIONS:",
              fill=PLUM, font=get_font(13, bold=True))

    # Education
    draw.text((220, 305), "Education:", fill=PLUM, font=get_font(12, bold=True))
    edu_lines = textwrap.wrap(job['education'], width=55)
    y = 305
    for line in edu_lines:
        draw.text((320, y), line, fill=(60, 60, 60), font=get_font(12))
        y += 17

    # Experience
    y += 8
    draw.text((220, y), "Experience:", fill=PLUM, font=get_font(12, bold=True))
    draw.text((320, y), job['experience'] or "None required",
              fill=(60, 60, 60), font=get_font(12))

    # Training/Skills
    y += 22
    draw.text((220, y), "Training:", fill=PLUM, font=get_font(12, bold=True))
    skills_lines = textwrap.wrap(job['skills'] or "None required", width=55)
    for line in skills_lines:
        draw.text((320, y), line, fill=(60, 60, 60), font=get_font(12))
        y += 17

    # Eligibility/Other
    if job.get('other_requirements'):
        y += 5
        draw.text((220, y), "Eligibility:", fill=PLUM, font=get_font(12, bold=True))
        draw.text((320, y), job['other_requirements'][:60],
                  fill=(60, 60, 60), font=get_font(12))

    # ── Thin gold divider ──
    div_y = max(y + 25, 490)
    draw.rectangle([220, div_y, 830, div_y + 2], fill=GOLD)

    # ── Application instructions ──
    note_y = div_y + 12
    draw.text((220, note_y),
              "Qualified applicants are advised to send their application letter via email to:",
              fill=(80, 80, 80), font=get_font(10))
    draw.text((220, note_y + 14), "hrmo@bulsu.edu.ph",
              fill=PLUM, font=get_font(11, bold=True))
    draw.text((220, note_y + 28),
              "Upload all required documents in one (1) PDF File at the BulSU HR Portal.",
              fill=(80, 80, 80), font=get_font(10))

    # ── Bottom bar ──
    draw.text((530, 593), "For more details, visit: bulsu.edu.ph/hr-portal",
              fill=GOLD, anchor="mm", font=get_font(11))
    draw.text((530, 615), "Bulacan State University — Quality Education for Relevant Development",
              fill=WHITE, anchor="mm", font=get_font(10))

    filename = f"ad_{job['title'].replace(' ', '_')}_{job['campus'].replace(' ', '_')}.png"
    filepath = os.path.join("static/ads", filename)
    img.save(filepath)

    return filename, job['title']