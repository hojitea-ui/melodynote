"""전체 파이프라인: 음성 파일 → 음 인식 → 음 나누기/멜로디 고르기 → 박자 보정 → 결과(JSON·MIDI).

두 가지 방식(mode):
- "poly" (기본): 다성 모델로 모든 음을 잡고 가장 높은 줄을 멜로디로 고른다. 양손 연주·반주가 있어도 된다
- "mono": pYIN 단선율 인식. 허밍·노래처럼 한 줄만 있는 녹음용
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Callable

import numpy as np

from .pitch import MAX_SECONDS, load_audio, track_pitch
from .rhythm import GRID_PER_BEAT, estimate_key, estimate_tempo, note_name, quantize
from .segment import segment_notes

# PRD 5장 오류 대응 문구
MSG_NO_NOTES = "음을 찾지 못했어요. 조용한 곳에서 다시 녹음해 주세요"
MSG_POLY = "멜로디 하나만 인식할 수 있어요. 불확실한 음은 ?로 표시했어요"
MSG_ACCOMP = "반주 음이 멜로디에 섞였을 수 있어요. 불확실한 음은 ?로 표시했어요"
MSG_TOO_LONG = "1분 이내 녹음만 변환할 수 있어요"
POLY_RATIO = 0.3  # 불확실한 음이 이 비율을 넘으면 안내 문구를 띄운다
MODES = ("poly", "mono")


@dataclass
class Result:
    status: str  # ok | uncertain | no_notes | too_long
    message: str | None
    mode: str
    duration_sec: float
    bpm: float
    time_signature: str
    key: str
    tuning_cents: float
    melody_floor: int | None = None  # poly: 멜로디로 보는 가장 낮은 음 (MIDI)
    floor_auto: bool = True
    all_notes: int | None = None  # poly: 반주까지 포함해 잡힌 음 수
    notes: list[dict] = field(default_factory=list)
    timings: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def transcribe(
    path: str,
    bpm: float | None = None,
    mode: str = "mono",
    floor: int | None = None,
    events: list | None = None,
    on_events: Callable[[list], None] | None = None,
) -> tuple[Result, list]:
    """음성 파일을 변환한다. (결과, 양자화된 음 목록)을 돌려준다.

    poly 방식에서 events(이전에 잡은 모든 음)를 넘기면 인식을 건너뛴다.
    템포·최저음만 바꿔 다시 맞출 때 쓴다. 새로 잡은 events는 on_events로 알려준다.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    t = {}
    t0 = time.perf_counter()
    y, sr = load_audio(path)
    t["load"] = time.perf_counter() - t0
    dur = len(y) / sr

    def done(status, message, **kw):
        t["total"] = sum(t.values())
        base = dict(
            status=status, message=message, mode=mode, duration_sec=round(dur, 2), bpm=0.0,
            time_signature="4/4", key="C", tuning_cents=0.0,
        )
        base.update(kw)
        return Result(**base, timings={k: round(v, 3) for k, v in t.items()})

    if dur > MAX_SECONDS:
        return done("too_long", MSG_TOO_LONG), []

    extra: dict = {}
    if mode == "mono":
        t1 = time.perf_counter()
        track = track_pitch(y, sr)
        t["pitch"] = time.perf_counter() - t1
        t1 = time.perf_counter()
        notes = segment_notes(track, y)
        t["segment"] = time.perf_counter() - t1
        extra["tuning_cents"] = round(track.tuning * 100, 1)
        poly_msg = MSG_POLY
    else:
        from . import poly

        t1 = time.perf_counter()
        if events is None:
            events = poly.detect_all(y, sr)
            if on_events:
                on_events(events)
        t["pitch"] = time.perf_counter() - t1
        t1 = time.perf_counter()
        auto = poly.auto_floor(events)
        use = floor if floor is not None else auto
        notes = poly.pick_melody(events, use)
        t["segment"] = time.perf_counter() - t1
        extra.update(melody_floor=use, floor_auto=floor is None, all_notes=len(events))
        poly_msg = MSG_ACCOMP

    if not notes:
        return done("no_notes", MSG_NO_NOTES, **extra), []

    t1 = time.perf_counter()
    tempo = float(bpm) if bpm else estimate_tempo(notes)
    qnotes = quantize(notes, tempo)
    key = estimate_key(qnotes)
    t["rhythm"] = time.perf_counter() - t1

    uncertain_ratio = float(np.mean([q.uncertain for q in qnotes]))
    status, message = ("uncertain", poly_msg) if uncertain_ratio > POLY_RATIO else ("ok", None)

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
    res = done(status, message, bpm=round(tempo, 1), key=key, notes=note_dicts, **extra)
    return res, qnotes
