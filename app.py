from groq import Groq
from PIL import Image, ImageDraw, ImageFont
import io
import os
from datetime import datetime, timezone, timedelta
from config import GROQ_API_KEY

client = Groq(api_key=GROQ_API_KEY)

# BulSU Official Colors
PLUM = (103, 30, 30)        # #671E1E Persian Plum
GOLD = (255, 235, 91)       # #FFEB5B Official Gold
WHITE = (255, 255, 255)
LIGHT_PLUM = (140, 60, 60)
DARK_PLUM = (70, 15, 15)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, "static", "ad_template.png")

# Bundled fonts so rendering is identical on Windows and on Vercel/Render (Linux).
FONT_CANDIDATES = [
    os.path.join(BASE_DIR, "static", "fonts", "DejaVuSans.ttf"),
    "C:\\Windows\\Fonts\\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
FONT_BOLD_CANDIDATES = [
    os.path.join(BASE_DIR, "static", "fonts", "DejaVuSans-Bold.ttf"),
    "C:\\Windows\\Fonts\\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

def get_font(size, bold=False):
    for path in (FONT_BOLD_CANDIDATES if bold else FONT_CANDIDATES):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
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
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )
    return response.choices[0].message.content.strip()


# ── Poster layout (pixel boxes measured on static/ad_template.png, 1121x1403) ──
NAVY = (10, 40, 90)
BAR_GOLD = (255, 214, 66)
TEXT_GRAY = (45, 55, 75)

TITLE_BOX = (60, 422, 1060, 528)      # navy bar: position title
DETAIL_BOX = (70, 580, 1050, 1090)    # white box: qualifications


PH_TZ = timezone(timedelta(hours=8))   # Vercel runs in UTC; posters use Philippine time
DATE_RIGHT_X = 1086                    # right edge of the "Posting as of" line
DATE_BASELINE_Y = 52

def _posting_date(job):
    """Date the job was posted (created_at), falling back to today (PH time)."""
    d = job.get("created_at")
    if isinstance(d, str):
        try:
            d = datetime.fromisoformat(d.replace("Z", "+00:00")[:26])
        except ValueError:
            d = None
    if not isinstance(d, datetime):
        d = datetime.now(PH_TZ)
    return d.strftime("%B %d, %Y").upper().replace(" 0", " ")

def _draw_posting_date(draw, job):
    date_text = _posting_date(job)
    label = "Posting as of "
    for size in (26, 24, 22, 20):
        date_font = get_font(size, bold=True)
        label_font = get_font(size - 5)
        total = draw.textlength(label, font=label_font) + draw.textlength(date_text, font=date_font)
        if total <= 400:
            break
    x = DATE_RIGHT_X - total
    draw.text((x, DATE_BASELINE_Y), label, fill=(255, 255, 255), font=label_font, anchor="ls")
    draw.text((x + draw.textlength(label, font=label_font), DATE_BASELINE_Y), date_text,
              fill=(255, 214, 66), font=date_font, anchor="ls")

def _wrap(draw, text, font, max_width):
    words = str(text).split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]

def _fit_title(draw, text, max_width, max_height):
    """Largest bold size (<=64) where the title fits the bar in 1-2 lines."""
    for size in range(64, 27, -2):
        font = get_font(size, bold=True)
        lines = _wrap(draw, text, font, max_width)
        if len(lines) <= 2 and len(lines) * size * 1.15 <= max_height:
            return font, lines, size
    font = get_font(28, bold=True)
    return font, _wrap(draw, text, font, max_width)[:2], 28

def generate_job_ad_image(job):
    """Render the BulSU 'We Are Hiring' poster fully in memory.
    Returns a BytesIO PNG. Nothing is written to disk (Vercel is read-only)."""
    img = Image.open(TEMPLATE_PATH).convert("RGB")
    draw = ImageDraw.Draw(img)

    _draw_posting_date(draw, job)

    # ── Position title + campus inside the gold-bordered bar ──
    x0, y0, x1, y1 = TITLE_BOX
    campus_font = get_font(26)
    campus = job.get("campus") or ""
    campus_h = 34 if campus else 0
    font, lines, size = _fit_title(draw, job["title"], x1 - x0 - 40, (y1 - y0) - campus_h - 8)
    block_h = int(len(lines) * size * 1.15) + campus_h
    y = y0 + ((y1 - y0) - block_h) // 2
    cx = (x0 + x1) // 2
    for line in lines:
        draw.text((cx, y), line, fill=(255, 255, 255), font=font, anchor="ma")
        y += int(size * 1.15)
    if campus:
        draw.text((cx, y + 4), campus, fill=BAR_GOLD, font=campus_font, anchor="ma")

    # ── Qualifications inside the white box ──
    bx0, by0, bx1, by1 = DETAIL_BOX
    label_font = get_font(30, bold=True)
    value_font = get_font(30)
    head_font = get_font(38, bold=True)
    value_x = bx0 + 270
    max_w = bx1 - value_x - 10

    y = by0 + 10
    draw.text((bx0, y), "QUALIFICATIONS", fill=NAVY, font=head_font)
    y += 62
    draw.rectangle([bx0, y - 8, bx0 + 120, y - 4], fill=BAR_GOLD)
    y += 18

    rows = [
        ("Education", job.get("education")),
        ("Experience", job.get("experience") or "None required"),
        ("Training", job.get("skills") or "None required"),
        ("Eligibility", job.get("other_requirements")),
    ]
    for label, value in rows:
        if not value:
            continue
        lines = _wrap(draw, value, value_font, max_w)
        if y + len(lines) * 42 > by1:
            break
        draw.text((bx0, y), label + ":", fill=NAVY, font=label_font)
        for line in lines:
            draw.text((value_x, y), line, fill=TEXT_GRAY, font=value_font)
            y += 42
        y += 22

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf