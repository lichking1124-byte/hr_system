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

FONT_COND_BOLD_CANDIDATES = [
    os.path.join(BASE_DIR, "static", "fonts", "DejaVuSansCondensed-Bold.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
]

def get_font(size, bold=False, condensed=False):
    if condensed:
        candidates = FONT_COND_BOLD_CANDIDATES + FONT_BOLD_CANDIDATES
    else:
        candidates = FONT_BOLD_CANDIDATES if bold else FONT_CANDIDATES
    for path in candidates:
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


# ── Poster layout (pixel boxes measured on static/ad_template.png, 1254x1254) ──
MAROON = (140, 8, 20)
INK = (38, 26, 26)
BAR_INK = (20, 0, 0)

PH_TZ = timezone(timedelta(hours=8))   # Vercel runs in UTC; posters use Philippine time

DATE_RIGHT_X, DATE_BASELINE_Y = 1222, 44       # "(Reposting as of ...)" line
TITLE_BOX = (150, 236, 1030, 332)              # gold band: position title
QUAL_BOX = (74, 424, 1180, 664)                # qualifications box interior
DESC_BOX = (74, 778, 972, 964)                 # description-of-work box interior
DEADLINE_X, DEADLINE_CY, DEADLINE_MAX_W = 388, 1031, 360

def _to_datetime(d):
    if isinstance(d, str):
        try:
            return datetime.fromisoformat(d.replace("Z", "+00:00")[:26])
        except ValueError:
            return None
    if isinstance(d, datetime):
        return d
    if hasattr(d, "year") and hasattr(d, "month") and hasattr(d, "day"):   # date
        return datetime(d.year, d.month, d.day)
    return None

def _fmt_date(d):
    return d.strftime("%B %d, %Y").replace(" 0", " ")

def _posting_date(job):
    """Date the job was posted (created_at), falling back to today (PH time)."""
    created = _to_datetime(job.get("created_at"))
    if created is not None and created.tzinfo is None and isinstance(job.get("created_at"), (datetime, str)):
        created = created + timedelta(hours=8)    # DB stores UTC; poster uses Philippine date
    return created or datetime.now(PH_TZ)

def _wrap(draw, text, font, max_width):
    lines = []
    for para in str(text).replace("\r", "").split("\n"):
        cur = ""
        for w in para.split():
            test = (cur + " " + w).strip()
            if draw.textlength(test, font=font) <= max_width:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = w
        lines.append(cur)
    while lines and not lines[-1]:
        lines.pop()
    return lines or [""]

def _clip_lines(draw, lines, font, max_width, max_lines):
    """Keep max_lines lines, ending the last with an ellipsis if text was cut."""
    if len(lines) <= max_lines:
        return lines
    lines = lines[:max_lines]
    last = lines[-1]
    while last and draw.textlength(last + "…", font=font) > max_width:
        last = last[:-1]
    lines[-1] = last.rstrip() + "…"
    return lines

def _draw_posting_date(draw, job):
    text = f"(Reposting as of {_fmt_date(_posting_date(job))})"
    font = get_font(24, bold=True)
    draw.text((DATE_RIGHT_X, DATE_BASELINE_Y), text, fill=MAROON, font=font, anchor="rs")

def _draw_title(draw, job):
    x0, y0, x1, y1 = TITLE_BOX
    campus = (job.get("campus") or "").strip()
    campus_h = 34 if campus else 0
    max_w, avail_h = x1 - x0, (y1 - y0) - campus_h
    for size in range(60, 25, -2):
        font = get_font(size, bold=True)
        lines = _wrap(draw, job["title"], font, max_w)
        if len(lines) <= 2 and len(lines) * size * 1.12 <= avail_h:
            break
    else:
        size, font = 26, get_font(26, bold=True)
        lines = _clip_lines(draw, _wrap(draw, job["title"], font, max_w), font, max_w, 2)
    block_h = int(len(lines) * size * 1.12) + campus_h
    y = y0 + ((y1 - y0) - block_h) // 2
    cx = (x0 + x1) // 2
    for line in lines:
        draw.text((cx, y), line, fill=MAROON, font=font, anchor="ma")
        y += int(size * 1.12)
    if campus:
        draw.text((cx, y + 2), campus, fill=BAR_INK, font=get_font(24, bold=True), anchor="ma")

def _draw_qualifications(draw, job):
    x0, y0, x1, y1 = QUAL_BOX
    rows = [
        ("Education", job.get("education")),
        ("Experience", job.get("experience") or "None required"),
        ("Training", job.get("skills") or "None required"),
        ("Eligibility", job.get("other_requirements")),
    ]
    rows = [(l, v) for l, v in rows if v]
    for size in range(30, 17, -1):
        label_font, value_font = get_font(size, bold=True), get_font(size)
        value_x = x0 + int(max(draw.textlength(l + ":", font=label_font) for l, _ in rows)) + 22
        max_w = x1 - value_x
        line_h, gap = int(size * 1.3), int(size * 0.5)
        wrapped = [(l, _wrap(draw, v, value_font, max_w)) for l, v in rows]
        total = sum(len(w) * line_h for _, w in wrapped) + gap * (len(wrapped) - 1)
        if total <= (y1 - y0):
            break
    y = y0 + max(0, ((y1 - y0) - total) // 2)
    for label, lines in wrapped:
        draw.text((x0, y), label + ":", fill=MAROON, font=label_font)
        for line in lines:
            draw.text((value_x, y), line, fill=INK, font=value_font)
            y += line_h
        y += gap

def _draw_description(draw, job):
    x0, y0, x1, y1 = DESC_BOX
    text = (job.get("description") or "").strip()
    if not text:
        return
    max_w, avail = x1 - x0, y1 - y0
    for size in range(28, 17, -1):
        font = get_font(size)
        line_h = int(size * 1.3)
        lines = _wrap(draw, text, font, max_w)
        if len(lines) * line_h <= avail:
            break
    else:
        lines = _clip_lines(draw, lines, font, max_w, avail // line_h)
    y = y0 + max(0, (avail - len(lines) * line_h) // 2)
    for line in lines:
        draw.text((x0, y), line, fill=INK, font=font)
        y += line_h

def _draw_deadline(draw, job):
    d = _to_datetime(job.get("deadline"))
    text = _fmt_date(d).upper() if d else "TO BE ANNOUNCED"
    for size in range(38, 21, -1):
        font = get_font(size, condensed=True)
        if draw.textlength(text, font=font) <= DEADLINE_MAX_W:
            break
    draw.text((DEADLINE_X, DEADLINE_CY), text, fill=BAR_INK, font=font, anchor="lm")

def generate_job_ad_image(job):
    """Render the BulSU 'We Are Hiring' poster fully in memory.
    Returns a BytesIO PNG. Nothing is written to disk (Vercel is read-only)."""
    img = Image.open(TEMPLATE_PATH).convert("RGB")
    draw = ImageDraw.Draw(img)
    _draw_posting_date(draw, job)
    _draw_title(draw, job)
    _draw_qualifications(draw, job)
    _draw_description(draw, job)
    _draw_deadline(draw, job)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
