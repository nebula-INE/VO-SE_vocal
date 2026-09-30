"""CVVC alias resolver for UTAU-style voicebanks.

This module deliberately keeps CVVC as a phoneme-plan layer.  It does not
replace the existing VCV resolver or alter rendering until the render path can
represent a CV and a VC segment as separate timed events.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from modules.data.oto_parser import OtoEntry
from modules.audio.vcv_resolver import VowelClassifier


_VOWELS = frozenset("aiueon")


@dataclass(frozen=True)
class CvvcSegment:
    """One CVVC phoneme segment selected from oto.ini."""

    note_index: int
    kind: str  # "cv" or "vc"
    alias: str
    oto_entry: OtoEntry
    start_time: float
    duration: float


class CvvcResolver:
    """Resolve standard UTAU CV + VC aliases without changing existing VCV behavior.

    Typical Japanese CVVC banks contain aliases such as:
      - か / - か       (CV)
      - a k / i k      (VC)

    The resolver only emits a VC transition when the exact VC alias exists.
    Missing VC aliases are never substituted with another consonant.
    """

    _VC_RE = re.compile(r"^([aiueon])\s+(.+)$", re.IGNORECASE)

    def __init__(self, oto_parser) -> None:
        self._oto = oto_parser

    @staticmethod
    def _clean_lyric(lyric: str) -> str:
        value = str(lyric or "").strip()
        value = re.sub(r"_?[A-Ga-g][#b]?[0-9]$", "", value).strip()
        value = re.sub(r"_[0-9]$", "", value).strip()
        if value.startswith("- "):
            value = value[2:].strip()
        elif value.startswith("_"):
            value = value[1:].strip()
        return value

    @classmethod
    def _is_vc_alias(cls, alias: str) -> bool:
        match = cls._VC_RE.match(str(alias or "").strip())
        if not match:
            return False
        tail = match.group(2).strip().lower()
        # CVVC VC aliases use a consonant token such as "k", "ky", or "sh".
        # Japanese kana and VCV syllables such as "a か" / "a ka" are not VC.
        if not tail or tail[0] in _VOWELS:
            return False
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", tail):
            return False
        return True

    @classmethod
    def _vc_aliases(cls, parser) -> List[str]:
        db = getattr(parser, "_db", {})
        return [alias for alias in db if cls._is_vc_alias(alias)]

    def has_vc(self) -> bool:
        return bool(self._vc_aliases(self._oto))

    def classify_voicebank(self) -> str:
        """Return a conservative capability label: cv, vcv, cvvc, or mixed."""
        db = getattr(self._oto, "_db", {})
        aliases = list(db)
        has_vc = any(self._is_vc_alias(alias) for alias in aliases)
        has_vcv = bool(getattr(self._oto, "has_vcv", lambda: False)())
        if has_vc and has_vcv:
            return "mixed"
        if has_vc:
            return "cvvc"
        if has_vcv:
            return "vcv"
        return "cv"

    def resolve_cv(self, lyric: str) -> Tuple[str, Optional[OtoEntry]]:
        """Resolve the CV side using the existing safe OTO fallback order."""
        clean = self._clean_lyric(lyric)
        entry = self._oto.resolve_alias(clean, None)
        if entry is not None:
            return entry.alias, entry
        return clean, None

    def resolve_vc(
        self,
        previous_vowel: str,
        next_consonant: str,
    ) -> Tuple[str, Optional[OtoEntry]]:
        """Resolve an exact VC alias; never fall back to another VC."""
        vowel = str(previous_vowel or "").strip().lower()
        consonant = str(next_consonant or "").strip()
        if vowel not in _VOWELS or not consonant:
            return "", None

        candidates = (
            f"{vowel} {consonant}",
            f"{vowel}_{consonant}",
            f"{vowel}{consonant}",
        )
        db = getattr(self._oto, "_db", {})
        for alias in candidates:
            entry = db.get(alias)
            if entry is not None and self._is_vc_alias(entry.alias):
                return entry.alias, entry
        return "", None

    def resolve_transition(
        self,
        previous_vowel: str,
        next_consonant: str,
    ) -> Optional[CvvcSegment]:
        """Resolve a VC transition without inventing timing."""
        alias, entry = self.resolve_vc(previous_vowel, next_consonant)
        if entry is None:
            return None
        return CvvcSegment(
            note_index=-1,
            kind="vc",
            alias=alias,
            oto_entry=entry,
            start_time=0.0,
            duration=0.0,
        )

    def _initial_consonant(self, note) -> Optional[str]:
        phonemes = getattr(note, "phonemes", None)
        if not phonemes:
            return None
        for phoneme in phonemes:
            value = str(phoneme or "").strip().lower()
            if not value or value in {"sil", "pau", "cl"} or value in _VOWELS:
                continue
            return value
        return None

    @staticmethod
    def _trailing_vowel_from_phonemes(note) -> Optional[str]:
        """Prefer analyzed phonemes over lyric text for the previous vowel."""
        phonemes = getattr(note, "phonemes", None)
        if isinstance(phonemes, str):
            phonemes = phonemes.split()
        if not phonemes:
            return None
        for phoneme in reversed(phonemes):
            value = str(phoneme or "").strip().lower()
            if value in _VOWELS:
                return value
        return None

    @staticmethod
    def _is_rest(note) -> bool:
        lyric = str(getattr(note, "lyric", "") or "").strip().lower()
        return lyric in {
            "", "r", "r_", "r_0", "[r]", "息", "br", "pau", "sil",
            "吸", "吸気", "息吸い", "休", "休符", "・", "-", "ー", "~",
        }

    def resolve_notes(self, notes: Sequence) -> List[CvvcSegment]:
        """Resolve CV/VC segments from already-analyzed NoteEvent objects.

        This is still a phoneme-plan layer: it never mutates Timeline notes and
        does not invent render timing for the VC sample.
        """
        result: List[CvvcSegment] = []
        previous_note = None
        lyric_classifier = VowelClassifier(use_g2p=False)
        for index, note in enumerate(notes):
            cv_alias, cv_entry = self.resolve_cv(getattr(note, "lyric", ""))
            if cv_entry is not None:
                result.append(CvvcSegment(
                    note_index=index,
                    kind="cv",
                    alias=cv_alias,
                    oto_entry=cv_entry,
                    start_time=float(getattr(note, "start_time", 0.0)),
                    duration=max(0.0, float(getattr(note, "duration", 0.0))),
                ))

            if previous_note is not None and not self._is_rest(note) and not self._is_rest(previous_note):
                previous_vowel = self._trailing_vowel_from_phonemes(previous_note)
                if previous_vowel is None:
                    previous_vowel = lyric_classifier.trailing_vowel(
                        str(getattr(previous_note, "lyric", "") or "")
                    )
                consonant = self._initial_consonant(note)
                vc_alias, vc_entry = self.resolve_vc(previous_vowel or "", consonant or "")
                if vc_entry is not None:
                    result.append(CvvcSegment(
                        note_index=index,
                        kind="vc",
                        alias=vc_alias,
                        oto_entry=vc_entry,
                        start_time=float(getattr(note, "start_time", 0.0)),
                        duration=0.0,
                    ))
            previous_note = note
        return result

    def expand_notes_for_render(self, notes: Sequence) -> List:
        """Expand a pure CVVC note list into render-only CV/VC events.

        The VC length follows the following CV note preutterance, matching the
        standard Japanese CVVC phonemizer behavior. Timeline notes are deep-copied
        and never mutated.
        """
        expanded = []
        previous_note = None
        for index, note in enumerate(notes):
            if previous_note is not None and not self._is_rest(previous_note) and not self._is_rest(note):
                previous_vowel = self._trailing_vowel_from_phonemes(previous_note)
                if previous_vowel is None:
                    previous_vowel = VowelClassifier(use_g2p=False).trailing_vowel(
                        str(getattr(previous_note, "lyric", "") or "")
                    )
                consonant = self._initial_consonant(note)
                vc_alias, vc_entry = self.resolve_vc(previous_vowel or "", consonant or "")
                try:
                    next_preutterance = max(0.0, float(getattr(note, "pre_utterance", 0.0) or 0.0))
                except (TypeError, ValueError):
                    next_preutterance = 0.0
                if vc_entry is not None and next_preutterance > 0.0:
                    vc_note = copy.deepcopy(note)
                    vc_note.start_time = max(
                        0.0, float(getattr(note, "start_time", 0.0)) - next_preutterance
                    )
                    vc_note.duration = next_preutterance
                    vc_note.lyric = vc_alias
                    vc_note.phonemes = [vc_alias]
                    vc_note.pre_utterance = 0.0
                    vc_note.overlap = 0.0
                    vc_note.vibrato_depth = 0.0
                    for attr in ("_ust_vibrato",):
                        if hasattr(vc_note, attr):
                            delattr(vc_note, attr)
                    vc_note._cvvc_render_alias = vc_alias
                    vc_note._cvvc_render_kind = "vc"
                    expanded.append(vc_note)

            cv_note = copy.deepcopy(note)
            cv_alias, cv_entry = self.resolve_cv(getattr(note, "lyric", ""))
            if cv_entry is not None:
                cv_note.lyric = cv_alias
                cv_note._cvvc_render_alias = cv_alias
                cv_note._cvvc_render_kind = "cv"
            expanded.append(cv_note)
            previous_note = note
        return expanded

    def resolve_sequence(
        self,
        notes: Sequence,
        previous_vowels: Sequence[Optional[str]],
        next_consonants: Sequence[Optional[str]],
    ) -> List[CvvcSegment]:
        """Create a non-timed CV/VC plan for diagnostics and future render integration.

        Timing is intentionally not guessed here.  The render layer must decide
        how the VC sample is placed relative to the following CV note.
        """
        if not (
            len(notes) == len(previous_vowels) == len(next_consonants)
        ):
            raise ValueError("notes/previous_vowels/next_consonants must have equal length")

        result: List[CvvcSegment] = []
        for index, note in enumerate(notes):
            lyric = getattr(note, "lyric", "") or getattr(note, "lyrics", "")
            cv_alias, cv_entry = self.resolve_cv(lyric)
            if cv_entry is not None:
                result.append(
                    CvvcSegment(
                        note_index=index,
                        kind="cv",
                        alias=cv_alias,
                        oto_entry=cv_entry,
                        start_time=float(getattr(note, "start_time", 0.0)),
                        duration=max(0.0, float(getattr(note, "duration", 0.0))),
                    )
                )

            if index > 0:
                vc = self.resolve_transition(
                    previous_vowels[index],
                    next_consonants[index],
                )
                if vc is not None:
                    result.append(
                        CvvcSegment(
                            note_index=index,
                            kind=vc.kind,
                            alias=vc.alias,
                            oto_entry=vc.oto_entry,
                            start_time=0.0,
                            duration=0.0,
                        )
                    )
        return result
