"""음높이 인식: 오디오를 읽어 프레임마다 음높이(MIDI 번호)와 유성음 여부를 구한다.

librosa의 pYIN을 쓴다. 단선율(목소리·악기 하나)을 전제로 한 알고리즘이라
PRD의 입력 범위와 맞는다.
"""
from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np

SR = 22050
HOP = 256  # 약 11.6ms
FRAME = 2048
FMIN = float(librosa.note_to_hz("C2"))  # 약 65Hz, 낮은 남성 음역
FMAX = float(librosa.note_to_hz("C6"))  # 약 1047Hz, 휘파람·고음 악기 일부까지
SILENCE_DB = -35.0  # 가장 큰 소리 대비 이보다 작으면 무음으로 본다
MAX_SECONDS = 61.0  # PRD: 1분 이내 (약간의 여유)


@dataclass
class PitchTrack:
    times: np.ndarray  # 프레임 시각(초)
    midi: np.ndarray  # 프레임 음높이(MIDI, 실수). 무성음은 nan
    voiced: np.ndarray  # 유성음 여부(bool)
    prob: np.ndarray  # 유성음 확률 0~1
    tuning: float  # 전체 음정 치우침(반음 단위, -0.5~0.5)
    hop: int
    sr: int


def load_audio(path: str, sr: int = SR) -> tuple[np.ndarray, int]:
    try:
        y, sr = librosa.load(path, sr=sr, mono=True)
    except Exception:
        # m4a·aac 등 libsndfile이 못 읽는 형식은 PyAV(자체 ffmpeg 포함)로 푼다
        y = _load_with_av(path, sr)
    return y, sr


def _load_with_av(path: str, sr: int) -> np.ndarray:
    import av

    with av.open(path) as c:
        st = c.streams.audio[0]
        res = av.AudioResampler(format="flt", layout="mono", rate=sr)
        chunks = [o.to_ndarray().reshape(-1) for fr in c.decode(st) for o in res.resample(fr)]
        chunks += [o.to_ndarray().reshape(-1) for o in res.resample(None)]
    return np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(0, np.float32)


def track_pitch(y: np.ndarray, sr: int = SR, hop: int = HOP) -> PitchTrack:
    f0, voiced, prob = librosa.pyin(
        y, fmin=FMIN, fmax=FMAX, sr=sr, frame_length=FRAME, hop_length=hop
    )
    times = librosa.times_like(f0, sr=sr, hop_length=hop)
    midi = librosa.hz_to_midi(f0)

    # 무음 구간은 pYIN이 유성음이라고 해도 버린다 (잡음 오인식 방지)
    rms = librosa.feature.rms(y=y, frame_length=FRAME, hop_length=hop)[0]
    n = min(len(rms), len(midi))
    loud = np.zeros(len(midi), dtype=bool)
    peak = float(rms.max()) if len(rms) else 0.0
    if peak > 1e-4:
        loud[:n] = rms[:n] > peak * 10 ** (SILENCE_DB / 20)
    voiced = voiced & loud & ~np.isnan(midi)
    midi = np.where(voiced, midi, np.nan)

    # 노래는 전체가 조금씩 높거나 낮게 불리는 경우가 많다. 반올림 전에 보정하려고 치우침을 잰다.
    tuning = 0.0
    if voiced.sum() > 10:
        dev = midi[voiced] - np.round(midi[voiced])
        # 원형 평균: -0.5와 +0.5가 같은 위치라서 단순 평균을 쓰면 안 된다
        ang = np.angle(np.mean(np.exp(2j * np.pi * dev)))
        tuning = float(ang / (2 * np.pi))

    return PitchTrack(times, midi, voiced, np.nan_to_num(prob), tuning, hop, sr)
