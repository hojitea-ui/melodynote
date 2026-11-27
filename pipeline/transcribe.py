"""전체 파이프라인: 음성 파일 → 음높이 인식 → 음 길이 분리 → 박자 보정 → 결과(JSON·MIDI)."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

import numpy as np

from .pitch import MAX_SECONDS, load_audio, track_pitch
from .rhythm import GRID_PER_BEAT, estimate_key, estimate_tempo, note_name, quantize
from .segment import segment_notes

# PRD 5장 오류 대응 문구
MSG_NO_NOTES = "음을 찾지 못했어요. 조용한 곳에서 다시 녹음해 주세요"
MSG_POLY = "멜로디 하나만 인식할 수 있어요. 불확실한 음은 ?로 표시했어요"
MSG_TOO_LONG = "1분 이내 녹음만 변환할 수 있어요"
POLY_RATIO = 0.3  # 불확실한 음이 이 비율을 넘으면 화음·잡음 혼입을 의심한다


@dataclass
class Result:
    status: str  # ok | uncertain | no_notes | too_long
    message: str | None
    duration_sec: float
    bpm: float
    time_signature: str
    key: str
    tuning_cents: float
    notes: list[dict] = field(default_factory=list)
    timings: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def transcribe(path: str, bpm: float | None = None) -> tuple[Result, list]:
    """음성 파일을 변환한다. (결과, 양자화된 음 목록)을 돌려준다."""
    t = {}
    t0 = time.perf_counter()
    y, sr = load_audio(path)
    t["load"] = time.perf_counter() - t0
    dur = len(y) / sr

    def done(status, message, **kw):
        t["total"] = sum(t.values())
        base = dict(
            status=status, message=message, duration_sec=round(dur, 2), bpm=0.0,
            time_signature="4/4", key="C", tuning_cents=0.0,
        )
        base.update(kw)
        return Result(**base, timings={k: round(v, 3) for k, v in t.items()})

    if dur > MAX_SECONDS:
        return done("too_long", MSG_TOO_LONG), []

    t1 = time.perf_counter()
    track = track_pitch(y, sr)
    t["pitch"] = time.perf_counter() - t1

    t1 = time.perf_counter()
    notes = segment_notes(track, y)
    t["segment"] = time.perf_counter() - t1

    if not notes:
        return done("no_notes", MSG_NO_NOTES, tuning_cents=round(track.tuning * 100, 1)), []

    t1 = time.perf_counter()
    tempo = float(bpm) if bpm else estimate_tempo(notes)
    qnotes = quantize(notes, tempo)
    key = estimate_key(qnotes)
    t["rhythm"] = time.perf_counter() - t1

    uncertain_ratio = float(np.mean([q.uncertain for q in qnotes]))
    status, message = ("uncertain", MSG_POLY) if uncertain_ratio > POLY_RATIO else ("ok", None)

    note_dicts = [
        {
            "pitch": q.pitch,
            "name": note_name(q.pitch, key),
            "start16": q.start16,
            "dur16": q.dur16,
            "start_beat": q.start16 / GRID_PER_BEAT,
            "dur_beat": q.dur16 / GRID_PER_BEAT,
            "start_sec": round(q.start_sec, 3),
            "end_sec": round(q.end_sec, 3),
            "confidence": round(q.confidence, 2),
            "uncertain": q.uncertain,
            "reason": n.reason,
        }
        for q, n in zip(qnotes, notes)
    ]
    res = done(
        status, message, bpm=round(tempo, 1), key=key,
        tuning_cents=round(track.tuning * 100, 1), notes=note_dicts,
    )
    return res, qnotes
