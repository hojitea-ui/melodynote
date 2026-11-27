// 악보 그리기 (VexFlow 4). 음 목록(16분음표 격자) → 마디·쉼표·붙임줄 → SVG

/** 박자표 → 한 마디의 16분음표 칸 수 (4/4 = 16, 3/4 = 12, 6/8 = 12, 2/4 = 8) */
export function measureLen(ts) {
  return ts.num * (16 / ts.den);
}
const VALUES = [16, 12, 8, 6, 4, 3, 2, 1];
const DUR = { 16: "w", 12: "h", 8: "h", 6: "q", 4: "q", 3: "8", 2: "8", 1: "16" };
const DOTTED = new Set([12, 6, 3]);
const FLAT_KEYS = new Set(["F", "Bb", "Eb", "Ab", "Db", "Gb", "Dm", "Gm", "Cm", "Fm", "Bbm", "Ebm"]);
const SHARP = ["c", "c#", "d", "d#", "e", "f", "f#", "g", "g#", "a", "a#", "b"];
const FLAT = ["c", "db", "d", "eb", "e", "f", "gb", "g", "ab", "a", "bb", "b"];

// 조표의 올림·내림표 개수 (자리 계산용)
const KEY_ACC = {
  C: 0, G: 1, D: 2, A: 3, E: 4, B: 5, "F#": 6, F: 1, Bb: 2, Eb: 3, Ab: 4, Db: 5, Gb: 6,
  Am: 0, Em: 1, Bm: 2, "F#m": 3, "C#m": 4, "G#m": 5, Dm: 1, Gm: 2, Cm: 3, Fm: 4, Bbm: 5, Ebm: 6,
};

const COLOR = { ink: "#30383d", unsure: "#646f75", sel: "#287cc2", play: "#1f69a8" };

export function noteName(pitch, key) {
  const names = FLAT_KEYS.has(key) ? FLAT : SHARP;
  const n = names[pitch % 12];
  return n[0].toUpperCase() + n.slice(1).replace("b", "♭").replace("#", "♯") + (Math.floor(pitch / 12) - 1);
}

function vfKey(pitch, key) {
  const names = FLAT_KEYS.has(key) ? FLAT : SHARP;
  return `${names[pitch % 12]}/${Math.floor(pitch / 12) - 1}`;
}

function splitLen(len) {
  const out = [];
  while (len > 0) {
    const v = VALUES.find((x) => x <= len);
    out.push(v);
    len -= v;
  }
  return out;
}

/** 음과 쉼표를 마디 단위 조각으로 나눈다. 마디를 넘는 음은 붙임줄로 잇는다. */
export function layout(notes, STEP = 16) {
  const sorted = [...notes].sort((a, b) => a.start16 - b.start16);
  const end = sorted.reduce((m, n) => Math.max(m, n.start16 + n.dur16), 0);
  const total = Math.max(1, Math.ceil(end / STEP)) * STEP;
  const events = [];
  let cur = 0;
  for (const n of sorted) {
    if (n.start16 > cur) events.push({ rest: true, start: cur, len: n.start16 - cur });
    events.push({ rest: false, start: n.start16, len: n.dur16, note: n });
    cur = n.start16 + n.dur16;
  }
  if (cur < total) events.push({ rest: true, start: cur, len: total - cur });

  const measures = Array.from({ length: total / STEP }, () => []);
  for (const ev of events) {
    let start = ev.start, left = ev.len;
    const pieces = [];
    while (left > 0) {
      const m = Math.floor(start / STEP);
      const take = Math.min(left, (m + 1) * STEP - start);
      // 쉼표는 박 위치에 맞춰 잘게, 음은 큰 값부터
      for (const v of splitLen(take)) {
        const p = { ...ev, start, len: v, measure: m };
        pieces.push(p);
        measures[m].push(p);
        start += v;
      }
      left -= take;
    }
    if (!ev.rest) pieces.forEach((p, i) => { p.tieNext = i < pieces.length - 1; p.first = i === 0; });
  }
  return measures;
}

/**
 * @param {HTMLElement} el
 * @param {{notes, key, selectedId, playingId}} model
 * @param {(id) => void} onSelect
 */
export function render(el, model, onSelect) {
  const VF = window.Vex?.Flow;
  if (!VF) {
    el.innerHTML = '<div class="empty" style="margin:16px"><strong>악보 그리기 도구를 불러오지 못했어요</strong><ol><li><b>01</b><span>인터넷 연결을 확인하고 새로고침하세요 (VexFlow를 CDN에서 받습니다)</span></li></ol></div>';
    return;
  }
  el.innerHTML = "";
  const { notes, key } = model;
  const pitches = notes.map((n) => n.pitch).sort((a, b) => a - b);
  const median = pitches.length ? pitches[Math.floor(pitches.length / 2)] : 60;
  const clef = median < 57 ? "bass" : "treble";
  const restKey = clef === "bass" ? "d/3" : "b/4";
  const ts = model.timeSig || { num: 4, den: 4 };
  const measures = layout(notes, measureLen(ts));

  const width = Math.max(240, el.clientWidth - 16);
  const ROW_H = 130;
  // 마디 폭: 음 개수에 비례, 줄 첫 마디는 음자리표·조표 자리만큼 더
  const keyAcc = 10 * (KEY_ACC[key] ?? 0) + 6;
  const base = (m) => Math.max(150, 34 * m.length + 40);
  const rows = [];
  let row = [], used = 0;
  measures.forEach((m, i) => {
    const extra = row.length === 0 ? 50 + keyAcc + (i === 0 ? 30 : 0) : 0;
    const w = base(m) + extra;
    if (row.length && used + w > width) { rows.push(row); row = []; used = 0; }
    const extra2 = row.length === 0 ? 50 + keyAcc + (i === 0 ? 30 : 0) : 0;
    row.push({ m, i, w: base(m) + extra2 });
    used += base(m) + extra2;
  });
  if (row.length) rows.push(row);

  const renderer = new VF.Renderer(el, VF.Renderer.Backends.SVG);
  renderer.resize(width, rows.length * ROW_H + 20);
  const ctx = renderer.getContext();
  ctx.setFont("IBM Plex Mono", 10);

  const drawn = []; // { piece, sn }
  rows.forEach((r, ri) => {
    const sum = r.reduce((s, x) => s + x.w, 0);
    const scale = ri === rows.length - 1 && sum < width * 0.7 ? 1 : (width - 10) / sum;
    let x = 4;
    const y = 10 + ri * ROW_H + 20;
    r.forEach(({ m, i, w }, k) => {
      const sw = w * scale;
      const stave = new VF.Stave(x, y, sw);
      if (k === 0) { stave.addClef(clef).addKeySignature(key); }
      if (i === 0) stave.addTimeSignature(`${ts.num}/${ts.den}`);
      if (i === measures.length - 1) stave.setEndBarType(VF.Barline.type.END);
      stave.setContext(ctx).draw();

      const tickables = m.map((p) => {
        const isSel = !p.rest && p.note.id === model.selectedId;
        const isPlay = !p.rest && p.note.id === model.playingId;
        const sn = new VF.StaveNote({
          clef,
          keys: [p.rest ? restKey : vfKey(p.note.pitch, key)],
          duration: DUR[p.len] + (p.rest ? "r" : ""),
          auto_stem: true,
        });
        if (DOTTED.has(p.len)) VF.Dot.buildAndAttach([sn], { all: true });
        if (!p.rest) {
          const color = isSel ? COLOR.sel : isPlay ? COLOR.play : p.note.uncertain ? COLOR.unsure : COLOR.ink;
          sn.setStyle({ fillStyle: color, strokeStyle: color });
          if (p.note.uncertain && p.first) {
            const a = new VF.Annotation("?").setFont("IBM Plex Mono", 13, "bold")
              .setVerticalJustification(VF.Annotation.VerticalJustify.TOP);
            sn.addModifier(a, 0);
          }
        }
        drawn.push({ p, sn });
        return sn;
      });
      tickables.forEach((t) => t.setStave(stave));
      const voice = new VF.Voice({ num_beats: ts.num, beat_value: ts.den }).setMode(VF.Voice.Mode.SOFT).addTickables(tickables);
      voice.setStave(stave);
      VF.Accidental.applyAccidentals([voice], key);
      // 8분의 6박자는 8분음표 셋씩, 나머지는 4분음표 단위로 묶는다
      const group = ts.den === 8 ? new VF.Fraction(3, 8) : new VF.Fraction(1, 4);
      const beams = VF.Beam.generateBeams(tickables.filter((t) => !t.isRest()), { groups: [group] });
      new VF.Formatter().joinVoices([voice]).format([voice], Math.max(40, sw - (stave.getNoteStartX() - x) - 14));
      voice.draw(ctx, stave);
      beams.forEach((b) => b.setContext(ctx).draw());
      x += sw;
    });
  });

  // 붙임줄
  for (let i = 0; i < drawn.length; i++) {
    const { p, sn } = drawn[i];
    if (p.rest || !p.tieNext) continue;
    const next = drawn.slice(i + 1).find((d) => !d.p.rest && d.p.note.id === p.note.id);
    if (!next) continue;
    const sameRow = Math.abs(sn.getStave().getY() - next.sn.getStave().getY()) < 1;
    const ties = sameRow
      ? [new VF.StaveTie({ first_note: sn, last_note: next.sn, first_indices: [0], last_indices: [0] })]
      : [new VF.StaveTie({ first_note: sn, last_note: null, first_indices: [0] }),
         new VF.StaveTie({ first_note: null, last_note: next.sn, last_indices: [0] })];
    ties.forEach((t) => t.setContext(ctx).draw());
  }

  // 누르면 선택
  for (const { p, sn } of drawn) {
    if (p.rest) continue;
    const g = sn.getSVGElement?.() || sn.getAttribute?.("el") || document.getElementById("vf-" + sn.getAttribute("id"));
    if (!g) continue;
    g.dataset.id = p.note.id;
    g.addEventListener("click", () => onSelect(p.note.id));
  }
}
