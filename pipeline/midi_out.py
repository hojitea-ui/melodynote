"""MIDI 파일 쓰기."""
from __future__ import annotations

import mido

from .rhythm import GRID_PER_BEAT, QNote

TICKS_PER_BEAT = 480


def write_midi(
    qnotes: list[QNote], bpm: float, key: str, path: str, time_sig: tuple[int, int] = (4, 4)
) -> None:
    build_midi(qnotes, bpm, key, time_sig).save(path)


def build_midi(
    qnotes: list[QNote], bpm: float, key: str, time_sig: tuple[int, int] = (4, 4)
) -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=TICKS_PER_BEAT)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tr.append(mido.MetaMessage("track_name", name="MelodyNote", time=0))
    tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    tr.append(
        mido.MetaMessage("time_signature", numerator=time_sig[0], denominator=time_sig[1], time=0)
    )
    tr.append(mido.MetaMessage("key_signature", key=key, time=0))

    tick16 = TICKS_PER_BEAT // GRID_PER_BEAT
    events: list[tuple[int, int, mido.Message]] = []  # (tick, 순서, 메시지): 같은 tick이면 note_off 먼저
    for q in qnotes:
        on = q.start16 * tick16
        off = (q.start16 + q.dur16) * tick16
        events.append((on, 1, mido.Message("note_on", note=q.pitch, velocity=80)))
        events.append((off, 0, mido.Message("note_off", note=q.pitch, velocity=0)))
    events.sort(key=lambda e: (e[0], e[1]))

    now = 0
    for tick, _, msg in events:
        tr.append(msg.copy(time=tick - now))
        now = tick
    tr.append(mido.MetaMessage("end_of_track", time=0))
    return mid
