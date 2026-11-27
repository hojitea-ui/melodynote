"""음 길이 분리: 프레임별 음높이를 '음' 단위(시작·끝·음높이)로 나눈다.

음이 바뀌는 지점은 두 가지로 찾는다.
- 음높이가 바뀐 상태가 몇 프레임 이어질 때 (비브라토·흔들림은 무시)
- 같은 음을 다시 낼 때 생기는 소리 세기의 골(dip)과 어택
"""
from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np

from .pitch import PitchTrack

PITCH_JUMP = 0.75  # 반음 단위. 이보다 크게 바뀌어야 새 음으로 본다
JUMP_HOLD = 5  # 바뀐 음높이가 이 프레임 수(약 58ms) 이상 이어져야 한다
MIN_NOTE_SEC = 0.06  # 이보다 짧은 음은 버린다
DIP_DB = 5.0  # 같은 음 안에서 소리가 이만큼(dB) 줄었다 다시 커지면 새 음으로 본다
DROP_PROB = 0.2  # 평균 유성음 확률이 이보다 낮은 음은 잡음으로 보고 버린다
UNCERTAIN_PROB = 0.5  # 평균 유성음 확률이 이보다 낮으면 불확실
UNCERTAIN_DEV = 0.35  # 반올림한 음에서 이만큼(반음) 넘게 벗어나면 불확실
UNCERTAIN_STD = 0.6  # 음 안의 음높이 흔들림이 이보다 크면 불확실
LOW_HARMONIC_RATIO = 0.2  # 1·2배음이 가장 센 배음의 이 비율보다 약하면 화음 혼입을 의심


@dataclass
class Note:
    start: float  # 초
    end: float  # 초
    pitch: int  # MIDI 번호
    pitch_raw: float  # 보정 후 음높이(실수)
    confidence: float  # 0~1
    uncertain: bool
    reason: str | None = None  # 불확실한 이유


def _reattack_frames(y: np.ndarray, sr: int, hop: int, n: int) -> np.ndarray:
    """소리 세기가 골을 이뤘다가 다시 커질 때, 다시 커지는 지점(어택)을 True로 표시한다."""
    rms = librosa.feature.rms(y=y, frame_length=512, hop_length=hop)[0][:n]
    db = librosa.amplitude_to_db(np.maximum(rms, 1e-6))
    out = np.zeros(n, dtype=bool)
    w = 5  # 앞뒤 약 58ms 안에서 비교
    for i in range(w, len(db) - w):
        if db[i] != db[i - w : i + w + 1].min():
            continue
        before = db[i - w : i].max()
        after = db[i + 1 : i + w + 1].max()
        if min(before, after) - db[i] >= DIP_DB:
            # 골 바닥이 아니라 가장 가파르게 커지는 프레임을 새 음의 시작으로 둔다
            rise = np.diff(db[i : i + w + 1])
            out[i + 1 + int(np.argmax(rise))] = True
    return out


TAIL_SEC = 0.1  # 앞 음과 같은 높이의 이보다 짧은 조각이 다음 음 직전에 붙어 있으면 앞 음의 꼬리로 본다


def _merge_tails(notes: list[Note]) -> list[Note]:
    out: list[Note] = []
    for i, nt in enumerate(notes):
        prev = out[-1] if out else None
        nxt = notes[i + 1] if i + 1 < len(notes) else None
        if (
            prev is not None
            and nxt is not None
            and nt.end - nt.start < TAIL_SEC
            and prev.pitch == nt.pitch
            and nt.start - prev.end < 0.02
            and nxt.start - nt.end < 0.03
        ):
            prev.end = nt.end
            continue
        out.append(nt)
    return out


def _harmonic_profile(S: np.ndarray, freqs: np.ndarray, a: int, b: int, f0: float) -> np.ndarray:
    """프레임 [a, b) 평균 스펙트럼에서 1~8배음의 세기."""
    spec = S[:, a:b].mean(axis=1)
    hs = []
    for k in range(1, 9):
        fk = k * f0
        sel = (freqs > fk * 0.97) & (freqs < fk * 1.03)
        hs.append(spec[sel].max() if sel.any() else 0.0)
    return np.array(hs)


def _make_note(track: PitchTrack, S: np.ndarray, freqs: np.ndarray, a: int, b: int) -> Note | None:
    """프레임 [a, b) 구간을 한 음으로 만든다."""
    t = track.times
    hop_sec = track.hop / track.sr
    start, end = float(t[a]), float(t[b - 1]) + hop_sec
    if end - start < MIN_NOTE_SEC:
        return None
    seg = track.midi[a:b]
    seg = seg[~np.isnan(seg)]
    if len(seg) == 0:
        return None
    prob = float(np.mean(track.prob[a:b]))
    if prob < DROP_PROB:
        return None
    # 음 앞뒤의 꺾임(스쿠핑)을 피하려고 가운데 부분의 중앙값을 쓴다
    k = len(seg) // 5
    core = seg[k : len(seg) - k] if len(seg) - 2 * k >= 3 else seg
    raw = float(np.median(core)) - track.tuning
    pitch = int(round(raw))
    dev = abs(raw - pitch)
    std = float(np.std(core))

    reason = None
    if prob < UNCERTAIN_PROB:
        reason = "weak_voicing"
    elif dev > UNCERTAIN_DEV:
        reason = "between_pitches"
    elif std > UNCERTAIN_STD:
        reason = "unstable_pitch"
    else:
        h = _harmonic_profile(S, freqs, a, b, float(librosa.midi_to_hz(raw + track.tuning)))
        if h.max() > 0 and max(h[0], h[1]) < LOW_HARMONIC_RATIO * h.max():
            # 화음(예: A·C#·E)은 공통 저음(A2)이 기본음으로 잡히는데, 그 기본음 자체는 소리가 없다
            reason = "missing_fundamental"
    conf = float(np.clip(prob * (1 - dev / 0.5) * (1 - min(std, 1.0) / 2), 0, 1))
    if reason == "missing_fundamental":
        conf *= 0.5
    return Note(start, end, pitch, raw, conf, reason is not None, reason)


def segment_notes(track: PitchTrack, y: np.ndarray) -> list[Note]:
    midi, voiced = track.midi, track.voiced
    n = len(midi)
    min_frames = int(np.ceil(MIN_NOTE_SEC * track.sr / track.hop))
    dips = _reattack_frames(y, track.sr, track.hop, n)
    S = np.abs(librosa.stft(y, n_fft=4096, hop_length=track.hop))[:, :n]
    freqs = librosa.fft_frequencies(sr=track.sr, n_fft=4096)

    bounds: list[tuple[int, int]] = []
    start = None
    i = 0
    while i < n:
        if not voiced[i]:
            if start is not None:
                bounds.append((start, i))
                start = None
            i += 1
            continue
        if start is None:
            start = i
            i += 1
            continue
        # 기준 음높이: 지금 음의 앞부분(꺾임)을 뺀 중앙값
        body = midi[start + (i - start) // 5 : i]
        ref = np.nanmedian(body) if np.any(~np.isnan(body)) else midi[i]
        ahead = midi[i : i + JUMP_HOLD]
        ahead = ahead[~np.isnan(ahead)]
        jumped = (
            i - start >= min_frames
            and len(ahead) >= JUMP_HOLD - 1
            and abs(np.median(ahead) - ref) > PITCH_JUMP
        )
        if jumped:
            # 음높이가 처음 기준에서 벗어나기 시작한 프레임으로 경계를 당긴다
            cut = i
            while cut - 1 > start + min_frames // 2 and abs(midi[cut - 1] - ref) > 0.3:
                cut -= 1
            bounds.append((start, cut))
            start = cut
            i += 1
            continue
        if dips[i] and i - start >= min_frames:
            bounds.append((start, i))
            start = i
        i += 1
    if start is not None:
        bounds.append((start, n))

    notes = [m for a, b in bounds if (m := _make_note(track, S, freqs, a, b)) is not None]
    return _merge_tails(notes)
