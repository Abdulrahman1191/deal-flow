"""Regression tests for the pitch-deck garble guard.

Motivated by Arabic decks (e.g. lead "بيناتنا") whose broken font CMaps made
pypdf emit Latin-1 mojibake like "GþþÿN þþþþÿ", which the AI assessment then
scored as noise. The guard must flag such output so it gets re-extracted via
OCR rather than stored as-is.
"""
from app.services.pitch_deck import _garble_ratio, _looks_garbled

# Real failure signature observed on the بيناتنا deck (broken ToUnicode CMap).
PROD_MOJIBAKE = "/7'/' GþþÿN þþþþÿ ?Nþþþÿ ONþÿ"
CLEAN_ARABIC = "صحتي بذكاء منصة وقائية تفاعلية مدعومة بالذكاء الاصطناعي لتحويل الرعاية الصحية"
CLEAN_ENGLISH = "Laundry Heroes Investor Presentation Saudi Arabia mobile internet penetration"


def test_guard_flags_mojibake():
    assert _garble_ratio(PROD_MOJIBAKE) > 0.30
    assert _looks_garbled(PROD_MOJIBAKE)


def test_guard_accepts_clean_arabic():
    assert _garble_ratio(CLEAN_ARABIC) < 0.05
    assert not _looks_garbled(CLEAN_ARABIC)


def test_guard_accepts_clean_english():
    assert _garble_ratio(CLEAN_ENGLISH) < 0.05
    assert not _looks_garbled(CLEAN_ENGLISH)


def test_guard_flags_empty_and_too_short():
    assert _looks_garbled("")
    assert _looks_garbled("   ")
    assert _looks_garbled("Bayanatna")  # below the minimum-usable-chars floor
