"""다성 인식 → 멜로디 고르기. 양손 연주·반주가 섞인 녹음용.

1. basic-pitch(Spotify, Apache-2.0, ONNX)로 동시에 울리는 음을 모두 잡는다
2. 멜로디 최저음(floor)을 정한다: 자동이면 음높이를 두 무리(왼손·오른손)로 나눈 경계
3. floor 이상에서 가장 높은 음을 따라간다(skyline)

실험(2026-11-27, 피아노 양손 59초)에서 단선율 방식·양손 전체보다 원본에 가장 가깝게 들렸다.
"""
from __future__ import annotations

import contextlib
import io
import logging
import os
import tempfile
from functools import lru_cache

import numpy as np
import soundfile as sf

from .segment import MIN_NOTE_SEC, Note

CHORD_SEC = 0.05  # 이 안에 같이 시작한 음은 한 화음
RING_SEC = 0.03  # 더 높은 음이 이보다 더 남아 울리면 지금 음은 반주로 본다
WEAK_AMP = 0.3  # 세기가 이보다 약하면 불확실
LEAP = 12  # 앞뒤 멜로디 음과 모두 이만큼(반음) 넘게 떨어지면 불확실
MIN_GAP = 7  # 두 무리의 중심이 이보다 가까우면 한 줄로 보고 나누지 않는다

Event = tuple[float, float, int, float]  # (시작초, 끝초, MIDI, 세기 0~1)


@lru_cache(maxsize=1)
def _model():
    # basic-pitch는 import 때 쓰지 않는 백엔드(TF·CoreML·TFLite) 경고를 남긴다
    logging.disable(logging.WARNING)
    try:
        from basic_pitch import ICASSP_2022_MODEL_PATH
        from basic_pitch.inference import Model
    finally:
        logging.disable(logging.NOTSET)
    return Model(ICASSP_2022_MODEL_PATH)


def detect_all(y: np.ndarray, sr: int) -> list[Event]:
    """녹음에서 울리는 음을 모두 잡는다 (양손 전체)."""
    from basic_pitch.inference import predict

    model = _model()
    # basic-pitch는 파일 경로를 받는다. 이미 읽은 소리를 WAV로 넘겨 형식 문제를 피한다
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    try:
        sf.write(path, y, sr)
        with contextlib.redirect_stdout(io.StringIO()):  # "Predicting MIDI for …" 출력 숨김
            _, _, events = predict(path, model)
    finally:
        os.remove(path)
    return sorted((float(s), float(e), int(p), float(a)) for s, e, p, a, _ in events)


def auto_floor(events: list[Event]) -> int | None:
    """음높이를 두 무리로 나눠 그 경계를 멜로디 최저음으로 쓴다. 한 줄이면 None."""
    if len(events) < 6:
        return None
    p = np.array([e[2] for e in events], dtype=float)
    w = np.array([max(e[1] - e[0], 0.05) for e in events])
    lo, hi = np.percentile(p, 25), np.percentile(p, 75)
    for _ in range(20):
        mid = (lo + hi) / 2
        a, b = p < mid, p >= mid
        if not a.any() or not b.any():
            return None
        lo, hi = np.average(p[a], weights=w[a]), np.average(p[b], weights=w[b])
    if hi - lo < MIN_GAP:
        return None
    return int(np.ceil((lo + hi) / 2))


def pick_melody(events: list[Event], floor: int | None) -> list[Note]:
    ev = [e for e in events if floor is None or e[2] >= floor]
    # 같이 시작한 화음에서는 가장 높은 음
    tops: list[Event] = []
    i = 0
    while i < len(ev):
        j = i
        while j + 1 < len(ev) and ev[j + 1][0] - ev[i][0] < CHORD_SEC:
            j += 1
        tops.append(max(ev[i : j + 1], key=lambda x: x[2]))
        i = j + 1
    mel: list[list] = []
    for s, e, p, a in tops:
        ringing = [m for m in mel if m[1] > s + RING_SEC]
        if ringing and max(m[2] for m in ringing) > p:
            continue  # 더 높은 음이 아직 울리는 중 → 반주
        mel.append([s, e, p, a])
    for k in range(len(mel) - 1):
        mel[k][1] = min(mel[k][1], mel[k + 1][0])
    mel = [m for m in mel if m[1] - m[0] >= MIN_NOTE_SEC]

    notes: list[Note] = []
    for k, (s, e, p, a) in enumerate(mel):
        prev = mel[k - 1][2] if k > 0 else None
        nxt = mel[k + 1][2] if k + 1 < len(mel) else None
        reason = None
        if a < WEAK_AMP:
            reason = "weak_voicing"
        elif prev is not None and nxt is not None and abs(p - prev) > LEAP and abs(p - nxt) > LEAP:
            reason = "isolated_leap"
        notes.append(Note(s, e, p, float(p), float(min(1.0, a)), reason is not None, reason))
    return notes
