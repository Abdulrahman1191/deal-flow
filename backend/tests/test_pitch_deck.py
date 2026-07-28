"""Regression tests for the pitch-deck garble guard.

Motivated by Arabic decks (e.g. lead "بيناتنا") whose broken font CMaps made
pypdf emit Latin-1 mojibake like "GþþÿN þþþþÿ", which the AI assessment then
scored as noise. The guard must flag such output so it gets re-extracted via
OCR rather than stored as-is.
"""
from types import SimpleNamespace

from app.config import settings
from app.services import claude_agent, pitch_deck
from app.services.pitch_deck import (
    MatchCandidate,
    _company_context,
    _garble_ratio,
    _looks_garbled,
    extract_text_from_pdf,
    find_lead_match,
    verify_match_candidates,
)

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


def _lead(company_name, description=None, copper_description=None):
    """Minimal Lead-shaped stand-in for the verification-tier tests (issue #74)."""
    raw = None
    if copper_description is not None:
        raw = {"custom_fields": [{"custom_field_definition_id": 536851, "value": copper_description}]}
    return SimpleNamespace(company_name=company_name, description=description, raw_copper_data=raw)


class TestFuzzyVerificationTier:
    """find_lead_match surfaces near-miss/ambiguous candidates for content
    verification instead of just giving up (issue #74)."""

    def test_real_transliteration_near_miss_surfaces_for_verification(self):
        # wathiq.pdf <-> "Watieq": 0.83 similarity, below MATCH_THRESHOLD (0.85)
        # but a genuine transliteration -- worth checking against deck content.
        leads = [_lead("Watieq")]
        result = find_lead_match("wathiq.pdf", leads)
        assert result.lead is None
        assert [c.company_name for c in result.needs_verification] == ["Watieq"]

    def test_coincidental_near_miss_still_surfaces_by_filename_alone(self):
        # Glow Therapeutics.pdf <-> "Lifesome Therapeutics": 0.79 similarity --
        # filename similarity alone can't distinguish this from a real
        # transliteration, so it also clears the default floor. It's
        # verify_match_candidates' job (checked below) to reject it on content.
        leads = [_lead("Lifesome Therapeutics")]
        result = find_lead_match("Glow Therapeutics.pdf", leads)
        assert result.lead is None
        assert [c.company_name for c in result.needs_verification] == ["Lifesome Therapeutics"]

    def test_custom_fuzzy_floor_excludes_lower_scoring_candidates(self):
        leads = [_lead("Lifesome Therapeutics")]
        result = find_lead_match("Glow Therapeutics.pdf", leads, fuzzy_floor=0.8)
        assert result.needs_verification == []

    def test_high_confidence_exact_match_has_no_verification_candidates(self):
        leads = [_lead("Ailoo")]
        result = find_lead_match("Ailoo.pdf", leads)
        assert result.lead is leads[0]
        assert result.needs_verification == []

    def test_clearly_unrelated_filename_never_surfaces_for_verification(self):
        leads = [_lead("Ailoo")]
        result = find_lead_match("Totally Unrelated Co.pdf", leads)
        assert result.lead is None
        assert result.needs_verification == []


class TestVerifyMatchCandidates:
    """verify_match_candidates makes one cheap LLM call per candidate and
    only resolves a lead on an unambiguous confident yes (issue #74)."""

    def test_single_confirmed_candidate_is_returned(self, monkeypatch):
        lead = _lead("Watieq", description="Digital notarization platform for MENA SMEs")
        candidate = MatchCandidate(lead=lead, company_name="Watieq", score=0.83)
        monkeypatch.setattr(claude_agent, "verify_pitch_deck_match", lambda *a, **k: True)
        assert verify_match_candidates([candidate], "Watieq -- digital notarization for SMEs") is lead

    def test_unconfirmed_candidate_returns_none(self, monkeypatch):
        lead = _lead("Lifesome Therapeutics", description="Longevity supplements brand")
        candidate = MatchCandidate(lead=lead, company_name="Lifesome Therapeutics", score=0.79)
        monkeypatch.setattr(claude_agent, "verify_pitch_deck_match", lambda *a, **k: False)
        assert verify_match_candidates([candidate], "Glow Therapeutics skincare serum deck") is None

    def test_ambiguous_pair_resolves_to_the_one_that_verifies(self, monkeypatch):
        lead_a = _lead("Ailooza", description="Fintech for freelancers")
        lead_b = _lead("Ailoozb", description="Logistics marketplace")
        candidates = [
            MatchCandidate(lead=lead_a, company_name="Ailooza", score=0.86),
            MatchCandidate(lead=lead_b, company_name="Ailoozb", score=0.86),
        ]

        def _fake_verify(company_name, company_context, deck_text):
            return company_name == "Ailoozb"

        monkeypatch.setattr(claude_agent, "verify_pitch_deck_match", _fake_verify)
        assert verify_match_candidates(candidates, "logistics marketplace deck") is lead_b

    def test_two_confirmed_candidates_never_guesses(self, monkeypatch):
        """Two distinct leads both verifying as a 'yes' should never happen in
        practice, but if it does, guessing between them is worse than skipping."""
        lead_a = _lead("Alpha Tech")
        lead_b = _lead("Alpha Co")
        candidates = [
            MatchCandidate(lead=lead_a, company_name="Alpha Tech", score=1.0),
            MatchCandidate(lead=lead_b, company_name="Alpha Co", score=1.0),
        ]
        monkeypatch.setattr(claude_agent, "verify_pitch_deck_match", lambda *a, **k: True)
        assert verify_match_candidates(candidates, "ambiguous deck text") is None

    def test_llm_error_is_treated_as_not_confirmed(self, monkeypatch):
        lead = _lead("Watieq")
        candidate = MatchCandidate(lead=lead, company_name="Watieq", score=0.83)

        def _boom(*_a, **_k):
            raise RuntimeError("LLM unavailable")

        monkeypatch.setattr(claude_agent, "verify_pitch_deck_match", _boom)
        assert verify_match_candidates([candidate], "deck text") is None

    def test_empty_deck_text_short_circuits_without_calling_the_llm(self, monkeypatch):
        lead = _lead("Watieq")
        candidate = MatchCandidate(lead=lead, company_name="Watieq", score=0.83)

        def _boom(*_a, **_k):
            raise AssertionError("must not call the LLM when there is no deck text")

        monkeypatch.setattr(claude_agent, "verify_pitch_deck_match", _boom)
        assert verify_match_candidates([candidate], "") is None

    def test_no_candidates_short_circuits_without_calling_the_llm(self, monkeypatch):
        def _boom(*_a, **_k):
            raise AssertionError("must not call the LLM with no candidates")

        monkeypatch.setattr(claude_agent, "verify_pitch_deck_match", _boom)
        assert verify_match_candidates([], "deck text") is None


class TestCompanyContext:
    def test_combines_lead_description_and_copper_field(self):
        lead = _lead(
            "Watieq",
            description="A notarization startup",
            copper_description="Digital contracts for MENA SMEs",
        )
        context = _company_context(lead)
        assert "notarization startup" in context
        assert "Digital contracts for MENA SMEs" in context

    def test_missing_description_and_copper_field_yields_empty_string(self):
        assert _company_context(_lead("Watieq")) == ""


class TestOcrFallback:
    """extract_text_from_pdf falls back to OCR only when the text layer is
    absent/garbled (issue #97); a real text layer never pays the OCR cost."""

    def _boom(self, _path):
        raise AssertionError("OCR must not run when a text layer already produced clean text")

    def test_ocr_used_when_text_layer_is_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pitch_deck, "_extract_pymupdf", lambda path: "")
        monkeypatch.setattr(pitch_deck, "_extract_pypdf", lambda path: "")
        monkeypatch.setattr(pitch_deck, "_extract_ocr", lambda path: CLEAN_ENGLISH)
        monkeypatch.setattr(settings, "pitch_deck_ocr_enabled", True)

        result = extract_text_from_pdf(tmp_path / "scanned.pdf")

        assert result == CLEAN_ENGLISH

    def test_ocr_used_when_text_layer_is_garbled(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pitch_deck, "_extract_pymupdf", lambda path: PROD_MOJIBAKE)
        monkeypatch.setattr(pitch_deck, "_extract_pypdf", lambda path: PROD_MOJIBAKE)
        monkeypatch.setattr(pitch_deck, "_extract_ocr", lambda path: CLEAN_ARABIC)
        monkeypatch.setattr(settings, "pitch_deck_ocr_enabled", True)

        result = extract_text_from_pdf(tmp_path / "scanned.pdf")

        assert result == CLEAN_ARABIC

    def test_text_layer_pdf_never_invokes_ocr(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pitch_deck, "_extract_pymupdf", lambda path: CLEAN_ENGLISH)
        monkeypatch.setattr(pitch_deck, "_extract_pypdf", self._boom)
        monkeypatch.setattr(pitch_deck, "_extract_ocr", self._boom)

        result = extract_text_from_pdf(tmp_path / "text-layer.pdf")

        assert result == CLEAN_ENGLISH

    def test_ocr_disabled_by_config_flag_short_circuits_before_any_ocr_deps(
        self, monkeypatch, tmp_path
    ):
        # Gate lives inside _extract_ocr itself, ahead of the fitz/pytesseract/
        # PIL imports -- so disabling it works even on a box without tesseract.
        monkeypatch.setattr(settings, "pitch_deck_ocr_enabled", False)

        result = pitch_deck._extract_ocr(tmp_path / "scanned.pdf")

        assert result == ""

    def test_ocr_disabled_by_config_flag_leaves_deck_empty_not_garbage(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(pitch_deck, "_extract_pymupdf", lambda path: "")
        monkeypatch.setattr(pitch_deck, "_extract_pypdf", lambda path: "")
        monkeypatch.setattr(settings, "pitch_deck_ocr_enabled", False)

        result = extract_text_from_pdf(tmp_path / "scanned.pdf")

        assert result == ""
