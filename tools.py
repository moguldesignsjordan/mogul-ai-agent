import os
import re
from typing import Optional, Dict, Any, List
from datetime import datetime
from zoneinfo import ZoneInfo

# --- Firestore (optional; won't crash if creds are missing) ---
import firebase_admin
from firebase_admin import credentials, firestore

# Try to init firebase_admin only once.
try:
    firebase_admin.get_app()
except ValueError:
    try:
        cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(cred)
    except Exception:
        # If creds aren't available, we'll just run with db = None
        pass

try:
    db = firestore.client()
except Exception:
    db = None


# ---------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------

DEFAULT_TZ = os.getenv("DEFAULT_TZ", "America/New_York")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _is_valid_email(email: str) -> bool:
    """Basic sanity check. Assistant should do this before saving."""
    if not email:
        return False
    return EMAIL_RE.match(email.strip()) is not None


def _clean_phone(phone: str) -> str:
    """
    Normalize phone to digits plus +1 if it's obviously US.
    This is intentionally rough, just for lookup.
    """
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if not digits:
        return ""
    # If it's 10 digits, assume US +1
    if len(digits) == 10:
        return "+1" + digits
    # If it already looks like country code + number, add leading + if missing
    if not digits.startswith("+"):
        digits = "+" + digits
    return digits


def _now_iso(tz: str = DEFAULT_TZ) -> str:
    """Return ISO timestamp in your local/business timezone for logging."""
    return datetime.now(ZoneInfo(tz)).isoformat()


# ---------------------------------------------------------------------------------
# TOOL: get_booking_link
# ---------------------------------------------------------------------------------

async def get_booking_link() -> Dict[str, str]:
    """
    ALWAYS-SAFE TOOL.
    Call this whenever the user wants to book a call, check availability,
    schedule a meeting, get on Jordan's calendar, etc.

    We do NOT try to auto-book.
    We simply return the public Cal.com link.
    """
    return {
        "url": "https://cal.com/jordan-c-cmbf7z/30min?overlayCalendar=true",
        "label": "Book a 30-minute call"
    }


# ---------------------------------------------------------------------------------
# TOOL: lookup_customer
# ---------------------------------------------------------------------------------

async def lookup_customer(
    email: Optional[str] = None,
    phone: Optional[str] = None
) -> Dict[str, Any]:
    """
    Look up an existing contact in Firestore by email or phone.
    Useful for recognizing returning users.
    """

    # Firestore might not be available in local dev (no creds, etc.).
    if db is None:
        return {
            "ok": False,
            "reason": "firestore_unavailable",
            "match": None,
        }

    clean_email = email.strip().lower() if email else None
    clean_phone = _clean_phone(phone) if phone else None

    try:
        # Try matching on email first
        if clean_email:
            q = (
                db.collection("customers")
                  .where("email", "==", clean_email)
                  .limit(1)
                  .stream()
            )
            for doc in q:
                data = doc.to_dict()
                data["id"] = doc.id
                return {
                    "ok": True,
                    "reason": "match_email",
                    "match": data,
                }

        # Then phone
        if clean_phone:
            q = (
                db.collection("customers")
                  .where("phone", "==", clean_phone)
                  .limit(1)
                  .stream()
            )
            for doc in q:
                data = doc.to_dict()
                data["id"] = doc.id
                return {
                    "ok": True,
                    "reason": "match_phone",
                    "match": data,
                }

    except Exception as e:
        return {
            "ok": False,
            "reason": f"firestore_error:{e}",
            "match": None,
        }

    # Nothing found
    return {
        "ok": True,
        "reason": "no_match",
        "match": None,
    }


# ---------------------------------------------------------------------------------
# TOOL: add_note
# ---------------------------------------------------------------------------------

async def add_note(
    conversation_id: str,
    customer_id: str,
    summary: str
) -> Dict[str, Any]:
    """
    Store a short note about what this person wanted.
    Ex: "Wants branding, sent booking link."

    This becomes lightweight CRM memory for later follow-up.
    """

    if db is None:
        return {
            "ok": False,
            "reason": "firestore_unavailable",
        }

    note_doc = {
        "conversation_id": conversation_id,
        "summary": summary,
        "ts": _now_iso(),
    }

    try:
        notes_ref = (
            db.collection("customers")
              .document(customer_id)
              .collection("notes")
        )
        new_ref = notes_ref.document()
        new_ref.set(note_doc)

        return {
            "ok": True,
            "note_id": new_ref.id,
            "written": note_doc,
        }

    except Exception as e:
        return {
            "ok": False,
            "reason": f"firestore_error:{e}",
        }


# ---------------------------------------------------------------------------------
# FUTURE / NOT EXPOSED YET
# ---------------------------------------------------------------------------------
