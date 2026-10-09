"""Turn a text PDF into a believable image-only "scan".

Each page is rasterized, converted to grayscale, rotated, washed out, made
noisy and blurred, saved as JPEG, and wrapped back into a PDF that has no
text layer.
"""

import io
import random

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageEnhance, ImageFilter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def scan_pdf(pdf: bytes, rng: random.Random, *, dpi: int = 110) -> tuple[bytes, dict[str, float]]:
    """Return (scanned_pdf_bytes, effect parameters used)."""
    angle = rng.choice([rng.uniform(-4.0, -0.8), rng.uniform(0.8, 4.0)])
    if rng.random() < 0.15:  # fed into the scanner sideways or upside down
        angle += rng.choice([90.0, 180.0, -90.0])
    contrast = rng.uniform(0.45, 0.7)
    brightness = rng.uniform(1.0, 1.15)
    noise_sigma = rng.uniform(8.0, 18.0)
    blur = rng.uniform(0.3, 0.9)
    quality = rng.randint(45, 65)
    np_rng = np.random.default_rng(rng.getrandbits(64))

    doc = pdfium.PdfDocument(pdf)
    out = io.BytesIO()
    c = canvas.Canvas(out, invariant=True, pageCompression=1)
    for page in doc:
        width_pt, height_pt = page.get_size()
        img: Image.Image = page.render(scale=dpi / 72).to_pil().convert("L")
        img = img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=235)
        img = ImageEnhance.Contrast(img).enhance(contrast)
        img = ImageEnhance.Brightness(img).enhance(brightness)
        arr = np.asarray(img, dtype=np.float64)
        arr = arr + np_rng.normal(0.0, noise_sigma, arr.shape)
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="L")
        img = img.filter(ImageFilter.GaussianBlur(blur))
        jpg = io.BytesIO()
        img.save(jpg, format="JPEG", quality=quality, optimize=False)
        jpg.seek(0)
        # Sideways scans come out landscape; keep the page aspect of the image.
        if img.width > img.height:
            width_pt, height_pt = height_pt, width_pt
        c.setPageSize((width_pt, height_pt))
        c.drawImage(ImageReader(jpg), 0, 0, width=width_pt, height=height_pt)
        c.showPage()
    doc.close()
    c.save()
    params = {
        "rotation_deg": round(angle, 2),
        "contrast": round(contrast, 2),
        "noise_sigma": round(noise_sigma, 1),
        "jpeg_quality": float(quality),
    }
    return out.getvalue(), params
