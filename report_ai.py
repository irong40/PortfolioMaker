"""
Sortie — AI Report Narrative Generator

Sends representative drone photos to Gemini Vision API to generate
site-specific observations, findings, and recommendations per job type.

Falls back gracefully if no API key, no network, or API errors.
"""

import os
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# ─── API SETUP ────────────────────────────────────────────────────────────

def _get_api_key():
    """Look for Gemini API key in env or .env file."""
    # Load .env files (project root, then home) without overwriting existing env
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent / ".env", override=False)
        load_dotenv(Path.home() / ".env", override=False)
    except ImportError:
        pass

    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


#: Pinned deliberately. The previous value, ``gemini-2.0-flash``, was retired by
#: Google and every call 404s. Because this path had never run here -- no
#: GEMINI_API_KEY was in this project's .env until 2026-08-05 -- nothing caught
#: it. ``list_models`` still advertises retired models, so an availability check
#: passes while ``generateContent`` fails; only a live call tells the truth.
#:
#: Pinned rather than ``gemini-flash-latest`` so a report generated next quarter
#: matches one generated today. That trade means this WILL go stale: when it
#: does, the symptom is a 404 naming this constant.
#:
#: Verified by live generateContent 2026-08-05.
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"


def _encode_image(path, max_size=1024):
    """Read and resize an image for API submission. Returns (bytes, mime_type).

    google-genai takes raw bytes via ``types.Part.from_bytes``; the old
    ``google-generativeai`` SDK wanted base64 in an ``inline_data`` dict.
    Returning bytes drops a needless encode/decode round trip on every photo.
    """
    from PIL import Image
    import io

    img = Image.open(path)
    img.thumbnail((max_size, max_size), Image.LANCZOS)

    # Convert to RGB JPEG for consistent API input
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue(), "image/jpeg"


# ─── PROMPT TEMPLATES PER JOB TYPE ────────────────────────────────────────

_BASE_SYSTEM_PROMPT = """\
You are a professional drone inspection analyst for Sentinel Aerial Inspections, \
a veteran-owned FAA Part 107 certified company. You write clear, factual \
observations based on aerial photographs. Be specific about what you see — \
reference locations (north/south/east/west, corners, edges), colors, textures, \
and conditions. Never fabricate measurements you cannot derive from photos alone.

{type_addendum}

Output valid JSON matching this schema:
{schema}
"""


# ─── PHOTO SELECTION ──────────────────────────────────────────────────────

def select_representative_photos(photos, max_photos=6):
    """Pick a diverse set of photos for AI analysis.

    Strategy:
    - 2 nadir (overview shots) — pick from spread GPS positions
    - Up to 4 oblique (detail shots) — pick from different angles
    - Fallback: whatever is available

    Args:
        photos: list of PhotoMeta objects
        max_photos: max photos to send to API (cost control)

    Returns:
        list of PhotoMeta objects
    """
    nadir = [p for p in photos if p.classification == "nadir"]
    oblique = [p for p in photos if p.classification == "oblique"]
    unknown = [p for p in photos if p.classification == "unknown"]

    selected = []

    # Pick nadir shots spread across the site
    if nadir:
        nadir_with_gps = [p for p in nadir if p.latitude is not None]
        if len(nadir_with_gps) >= 2:
            # Pick first and last by latitude for max spread
            sorted_by_lat = sorted(nadir_with_gps, key=lambda p: p.latitude)
            selected.append(sorted_by_lat[0])
            selected.append(sorted_by_lat[-1])
            if len(sorted_by_lat) > 2:
                selected.append(sorted_by_lat[len(sorted_by_lat) // 2])
        else:
            selected.extend(nadir[:2])

    # Pick oblique shots from different yaw angles
    if oblique:
        oblique_with_yaw = [p for p in oblique if p.yaw is not None]
        if oblique_with_yaw:
            sorted_by_yaw = sorted(oblique_with_yaw, key=lambda p: p.yaw)
            step = max(1, len(sorted_by_yaw) // 4)
            for i in range(0, len(sorted_by_yaw), step):
                if len(selected) >= max_photos:
                    break
                selected.append(sorted_by_yaw[i])
        else:
            remaining = max_photos - len(selected)
            selected.extend(oblique[:remaining])

    # Fill with unknowns if needed
    if len(selected) < 3 and unknown:
        remaining = min(3 - len(selected), len(unknown))
        selected.extend(unknown[:remaining])

    return selected[:max_photos]


# ─── MAIN API CALL ────────────────────────────────────────────────────────

def analyze_photos(photos, job_type, site_name="Site", max_photos=None):
    """Send representative drone photos to Gemini for analysis.

    Uses the report template for the job type to determine prompts,
    expected JSON schema, photo strategy, and max photos.

    Args:
        photos: list of PhotoMeta objects (from ClassificationResult.photos)
        job_type: key from REPORT_TYPES
        site_name: human name for the site
        max_photos: override max photos (otherwise from template)

    Returns:
        dict matching the template's ai_schema, plus "selected_photos".
        OR None if analysis unavailable.
    """
    api_key = _get_api_key()
    if not api_key:
        log.info("No Gemini API key found — skipping AI analysis")
        return None

    # Load template for this job type
    from report_templates import get_template
    template = get_template(job_type)
    if not template:
        log.warning(f"No template for job type: {job_type}")
        return None

    effective_max = max_photos or template.max_ai_photos
    selected = select_representative_photos(photos, effective_max)
    if not selected:
        log.warning("No photos available for AI analysis")
        return None

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        log.warning("google-genai not installed — skipping AI analysis")
        return None

    try:
        import json

        client = genai.Client(api_key=api_key)
        model_name = DEFAULT_GEMINI_MODEL

        # Build system prompt from template
        system_prompt = _BASE_SYSTEM_PROMPT.format(
            type_addendum=template.ai_system_addendum,
            schema=json.dumps(template.ai_schema, indent=2),
        )

        # Build multimodal content. The system prompt moves out of the parts
        # list into system_instruction, which is where google-genai wants it;
        # the old SDK had no such field so it was prepended as plain text.
        parts = [types.Part.from_text(
            text=f"Site: {site_name}\n\n{template.ai_prompt}\n\n"
                 f"I'm sending {len(selected)} representative aerial photos.")]

        for i, photo in enumerate(selected):
            data, mime = _encode_image(photo.path)
            parts.append(types.Part.from_bytes(data=data, mime_type=mime))
            meta_line = f"Photo {i+1}: {photo.classification}"
            if photo.pitch is not None:
                meta_line += f", pitch={photo.pitch:.1f}°"
            if photo.altitude is not None:
                meta_line += f", alt={photo.altitude:.0f}m"
            parts.append(types.Part.from_text(text=meta_line))

        response = client.models.generate_content(
            model=model_name,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                temperature=0.3,
            ),
        )


        # google-genai types .text as optional: a safety block or an empty
        # candidate list yields None, and json.loads(None) would raise inside
        # the generic handler below, logging a TypeError instead of the actual
        # cause. Name it.
        raw = response.text
        if not raw:
            log.warning("AI analysis returned no text (model=%s, feedback=%s)"
                        " — falling back", model_name,
                        getattr(response, "prompt_feedback", None))
            return None

        result = json.loads(raw)

        # Attach which photos were analyzed
        result["selected_photos"] = selected

        log.info(f"AI analysis complete: {len(result.get('observations', []))} observations")
        return result

    except Exception as e:
        log.warning(f"AI analysis failed: {e}")
        return None
