"""멜로디노트 파이프라인: 음성 → 음높이·박자 → 악보 데이터·MIDI."""
from .transcribe import Result, transcribe

__all__ = ["Result", "transcribe"]
