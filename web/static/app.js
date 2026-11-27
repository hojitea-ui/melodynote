import { decodeToMono, encodeWav, MAX_SEC, Player, Recorder } from "./audio.js";
import { measureLen, noteName, render } from "./score.js";

const $ = (id) => document.getElementById(id);
const MSG_EDIT = "틀린 음이 있나요? 눌러서 고칠 수 있어요";
const MSG_TOO_LONG = "1분 이내 녹음만 변환할 수 있어요";
const MSG_MIC = "마이크를 쓸 수 없어요. 브라우저 주소창의 권한 설정을 확인해 주세요";
const MSG_DECODE = "이 파일은 읽을 수 없어요. wav, mp3, m4a 파일이나 브라우저 녹음을 써 주세요";
const LEN_KO = { 1: "16분음표", 2: "8분음표", 3: "점8분음표", 4: "4분음표", 6: "점4분음표", 8: "2분음표", 12: "점2분음표", 16: "온음표" };
const REASON_KO = {
  weak_voicing: "소리가 약하거나 흐림",
  between_pitches: "두 음 사이 음높이",
  unstable_pitch: "음높이가 많이 흔들림",
  missing_fundamental: "화음·여러 소리 의심",
  isolated_leap: "앞뒤 음과 동떨어진 높이 · 반주 음 의심",
};
const STAGE_KO = {
  poly: { pitch: "음 인식 · 다성", segment: "멜로디 고르기" },
  mono: { pitch: "음높이 인식", segment: "음 나누기" },
};

const S = {
  wav: null, result: null, original: new Map(), notes: [], key: "C", bpm: 120, bpmSet: false,
  selectedId: null, history: [], busy: false,
  mode: "poly", floor: null, floorAuto: true,
  timeSig: { num: 4, den: 4 },
};
const player = new Player();

/* ── 서버 상태 (실제 값) ── */
async function checkServer() {
  const el = $("srvState");
  try {
    const r = await fetch("/api/health");
    const h = await r.json();
    if (h.ready) {
      el.className = "srv ok";
      el.innerHTML = `<b>●</b> SERVER READY · WARM-UP ${h.warmup_sec}S`;
    } else {
      el.className = "srv";
      el.textContent = "● SERVER WARMING";
      setTimeout(checkServer, 1000);
    }
  } catch {
    el.className = "srv bad";
    el.textContent = "○ SERVER OFFLINE";
    setTimeout(checkServer, 3000);
  }
}

/* ── 지금 보고 있는 장 ── */
function watchSections() {
  const meter = $("secMeter");
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) meter.textContent = e.target.id === "secScore" ? "SEC 02 / SCORE" : "SEC 01 / INPUT";
    }
  }, { rootMargin: "-40% 0px -55% 0px" });
  io.observe($("secInput"));
  io.observe($("secScore"));
}

/* ── 처리 단계 띠 ── */
const SYM = { WAITING: "□", RUNNING: "■", DONE: "■", FAILED: "○" };
let runTimer = null;
function stage(key, st, time) {
  const li = document.querySelector(`.stage[data-key="${key}"]`);
  li.dataset.st = st;
  li.querySelector(".st b").textContent = `${SYM[st]} ${st}`;
  if (time !== undefined) li.querySelector(".t").textContent = time;
  const en = li.querySelector(".en").textContent, ko = li.querySelector(".ko").textContent;
  li.setAttribute("aria-label", `${en} ${ko} · ${st} · ${li.querySelector(".t").textContent}`);
}
function runState(word, cls) {
  const el = $("runState");
  el.textContent = word;
  el.className = "state " + (cls || "");
}
function resetBand() {
  ["prepare", "pitch", "segment", "rhythm"].forEach((k) => stage(k, "WAITING", "--"));
  runState("IDLE");
  const r = $("runResult");
  r.className = "band-result";
  r.innerHTML = 'RESULT <span class="ko">— 변환하면 결과 요약이 여기 나옵니다</span>';
}
const fmt = (s) => `${s.toFixed(1)}S`;
function startServerStages() {
  const t0 = performance.now();
  ["pitch", "segment", "rhythm"].forEach((k) => stage(k, "RUNNING", "경과 0.0S"));
  clearInterval(runTimer);
  runTimer = setInterval(() => {
    const s = (performance.now() - t0) / 1000;
    ["pitch", "segment", "rhythm"].forEach((k) => {
      document.querySelector(`.stage[data-key="${k}"] .t`).textContent = `경과 ${fmt(s)}`;
    });
  }, 100);
  return t0;
}

/* ── 메시지 (▸ 주석 띠) ── */
function message(text, isErr = false) {
  const bar = $("msgBar");
  if (!text) { bar.hidden = true; return; }
  bar.hidden = false;
  bar.classList.toggle("err", isErr);
  $("msgText").textContent = text;
}

/* ── 입력 ── */
const clock = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
let recorder = null, recTimer = null;

async function toggleRec() {
  const btn = $("btnRec");
  if (recorder) { await stopRec(); return; }
  if (S.busy) return;
  try {
    recorder = new Recorder((peak) => { $("level").style.width = `${Math.min(100, peak * 140)}%`; });
    const mime = await recorder.start();
    btn.setAttribute("aria-pressed", "true");
    btn.querySelector(".ko").textContent = "녹음 멈추기";
    $("micState").textContent = `MIC · ● 녹음 중 · ${mime || "기본 형식"}`;
    const t0 = performance.now();
    recTimer = setInterval(() => {
      const s = (performance.now() - t0) / 1000;
      $("recClock").innerHTML = `${clock(s)}<small> / 01:00</small>`;
      if (s >= MAX_SEC) stopRec();
    }, 200);
  } catch {
    recorder = null;
    message(MSG_MIC, true);
    $("micState").textContent = "MIC · ○ 권한 없음";
  }
}

async function stopRec() {
  if (!recorder) return;
  clearInterval(recTimer);
  const r = recorder;
  recorder = null;
  const blob = await r.stop();
  const btn = $("btnRec");
  btn.setAttribute("aria-pressed", "false");
  btn.querySelector(".ko").textContent = "녹음 시작";
  $("micState").textContent = "MIC · 대기";
  await handleAudio(await blob.arrayBuffer(), { name: "브라우저 녹음", type: blob.type || "audio/webm" });
}

async function handleAudio(arrayBuffer, info) {
  if (S.busy) return;
  S.busy = true;
  player.stop();
  resetBand();
  runState("RUNNING", "running");
  stage("prepare", "RUNNING", "경과 0.0S");
  message(null);
  const t0 = performance.now();
  let dec;
  try {
    dec = await decodeToMono(arrayBuffer);
  } catch {
    stage("prepare", "FAILED", `소요 ${fmt((performance.now() - t0) / 1000)}`);
    runState("FAILED", "failed");
    message(MSG_DECODE, true);
    S.busy = false;
    return;
  }
  $("sourceEmpty").hidden = true;
  $("sourceSheet").hidden = false;
  $("srcName").textContent = info.name;
  $("srcLen").textContent = `${dec.duration.toFixed(1)} s`;
  $("srcFmt").textContent = `${info.type || "?"} · ${dec.srcRate} Hz · ${dec.channels} CH`;
  if (dec.duration > MAX_SEC + 0.5) {
    stage("prepare", "FAILED", `소요 ${fmt((performance.now() - t0) / 1000)}`);
    runState("FAILED", "failed");
    $("srcSent").textContent = "보내지 않음";
    message(MSG_TOO_LONG, true);
    S.busy = false;
    return;
  }
  S.wav = encodeWav(dec.samples, dec.sr);
  $("srcSent").textContent = `WAV · ${dec.sr} Hz · MONO · ${(S.wav.size / 1024).toFixed(0)} KB`;
  stage("prepare", "DONE", `소요 ${fmt((performance.now() - t0) / 1000)}`);
  S.busy = false;
  S.bpmSet = false;
  await transcribe({});
}

async function transcribe({ bpm = null, floor = null } = {}) {
  if (!S.wav || S.busy) return;
  S.busy = true;
  setControls();
  runState("RUNNING", "running");
  const t0 = startServerStages();
  const fd = new FormData();
  fd.append("audio", S.wav, "melody.wav");
  fd.append("mode", S.mode);
  if (bpm) fd.append("bpm", String(bpm));
  if (floor !== null && S.mode === "poly") fd.append("floor", String(floor));
  try {
    const r = await fetch("/api/transcribe", { method: "POST", body: fd });
    const body = await r.json().catch(() => ({}));
    clearInterval(runTimer);
    if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : `서버 오류 (${r.status})`);
    const roundTrip = (performance.now() - t0) / 1000;
    const tm = body.timings || {};
    stage("pitch", "DONE", `소요 ${fmt((tm.load || 0) + (tm.pitch || 0))}`);
    stage("segment", tm.segment !== undefined ? "DONE" : "WAITING", tm.segment !== undefined ? `소요 ${fmt(tm.segment)}` : "--");
    stage("rhythm", tm.rhythm !== undefined ? "DONE" : "WAITING", tm.rhythm !== undefined ? `소요 ${fmt(tm.rhythm)}` : "--");
    const unc = (body.notes || []).filter((n) => n.uncertain).length;
    const res = $("runResult");
    res.className = "band-result done";
    if (body.status === "no_notes" || body.status === "too_long") {
      ["segment", "rhythm"].forEach((k) => stage(k, body.status === "no_notes" && k === "segment" ? "DONE" : "WAITING"));
      runState("FAILED", "failed");
      res.innerHTML = `RESULT · NOTES 0 · 왕복 ${fmt(roundTrip)} <span class="ko">— ${body.message}</span>`;
      message(body.message, true);
      clearScore();
    } else {
      runState("COMPLETE", "complete");
      const flow = body.mode === "poly" ? `ALL ${body.all_notes} → MELODY ${body.notes.length}` : `OUT ${body.notes.length}`;
      res.innerHTML = `RESULT · ${body.mode.toUpperCase()} · IN ${body.duration_sec}S → ${flow} NOTES · UNCERTAIN ${unc} · 서버 ${fmt(tm.total || 0)} · 왕복 ${fmt(roundTrip)}`;
      message(body.status === "uncertain" ? body.message : MSG_EDIT, false);
      loadResult(body, !!bpm);
      $("secScore").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  } catch (e) {
    clearInterval(runTimer);
    ["pitch", "segment", "rhythm"].forEach((k) => stage(k, "FAILED"));
    runState("FAILED", "failed");
    message(e.message === "Failed to fetch" ? "서버에 닿지 않아요. 터미널에서 서버가 켜져 있는지 확인해 주세요" : e.message, true);
  } finally {
    S.busy = false;
    setControls();
  }
}

/* ── 악보 상태 ── */
function loadResult(body, bpmSet) {
  S.result = body;
  S.key = body.key;
  S.bpm = body.bpm;
  S.bpmSet = bpmSet;
  S.notes = body.notes.map((n, i) => ({
    id: i, pitch: n.pitch, start16: n.start16, dur16: n.dur16,
    uncertain: n.uncertain, confidence: n.confidence, reason: n.reason, checked: false,
  }));
  S.original = new Map(S.notes.map((n) => [n.id, { pitch: n.pitch, dur16: n.dur16 }]));
  S.nextId = S.notes.length;
  S.history = [];
  S.selectedId = S.notes.find((n) => n.uncertain)?.id ?? S.notes[0]?.id ?? null;
  S.floor = body.melody_floor ?? null;
  S.floorAuto = body.floor_auto !== false;
  $("bpmIn").value = Math.round(S.bpm);
  $("floorSel").value = S.floorAuto ? "auto" : String(S.floor);
  refresh();
}

function clearScore() {
  S.result = null; S.notes = []; S.original = new Map(); S.selectedId = null; S.history = [];
  ["roKey", "roTempo", "roNotes", "roFloor", "roUnc", "roEdited"].forEach((id) => ($(id).textContent = "-"));
  $("score").innerHTML = "";
  $("score").appendChild(scoreEmpty);
  updateSelPanel();
  setControls();
}

function editedCount() {
  let c = 0;
  for (const [id, o] of S.original) {
    const n = S.notes.find((x) => x.id === id);
    if (!n || n.pitch !== o.pitch || n.dur16 !== o.dur16) c++;
  }
  // AI가 빠뜨려서 새로 넣은 음도 고친 음으로 센다
  return c + S.notes.filter((n) => !S.original.has(n.id)).length;
}

function setRo(id, html) {
  const el = $(id);
  if (el.innerHTML === html) return;
  el.innerHTML = html;
  const ro = el.closest(".ro");
  ro.classList.remove("flash");
  void ro.offsetWidth;
  ro.classList.add("flash");
}

function refresh() {
  const total = S.original.size;
  const keyKo = S.key.endsWith("m") ? `${S.key.slice(0, -1)} <span class="ko">단조</span>` : `${S.key} <span class="ko">장조</span>`;
  setRo("roKey", keyKo);
  $("timeSel").value = `${S.timeSig.num}/${S.timeSig.den}`;
  setRo("roTempo", `${Math.round(S.bpm)}<small>BPM</small>`);
  $("tempoTag").textContent = S.bpmSet ? "■ SET" : "≈ EST";
  $("tempoTag").title = S.bpmSet ? "직접 정한 템포" : "음 시작점으로 추정한 템포";
  setRo("roNotes", `${S.notes.length}`);
  const poly = S.result?.mode === "poly";
  setRo("roFloor", !poly ? "-" : S.floor === null ? '<span class="ko">없음</span>' : noteName(S.floor, "C"));
  $("floorTag").hidden = !poly;
  $("floorTag").textContent = S.floorAuto ? "≈ AUTO" : "■ SET";
  $("floorTag").title = S.floorAuto ? "왼손·오른손 음높이 무리의 경계로 자동으로 정함" : "직접 정한 최저음";
  setRo("roUnc", `${S.notes.filter((n) => n.uncertain).length}`);
  const ed = editedCount();
  setRo("roEdited", `${ed}<small>/ ${total} · ${total ? Math.round((ed / total) * 100) : 0}%</small>`);
  drawScore();
  updateSelPanel();
  setControls();
}

const scoreEmpty = $("scoreEmpty");
function drawScore() {
  try {
    render($("score"), { notes: S.notes, key: S.key, selectedId: S.selectedId, timeSig: S.timeSig }, (id) => {
      S.selectedId = id;
      refresh();
    });
  } catch (e) {
    console.error(e);
    $("score").innerHTML = `<div class="empty" style="margin:16px"><strong>악보를 그리지 못했어요</strong><ol><li><b>01</b><span class="mono">${String(e.message || e).replace(/[<>&]/g, "")}</span></li></ol></div>`;
  }
}

function sel() { return S.notes.find((n) => n.id === S.selectedId) || null; }

function updateSelPanel() {
  const n = sel();
  $("selName").textContent = n ? `${noteName(n.pitch, S.key)} · MIDI ${n.pitch}` : "-";
  const ML = measureLen(S.timeSig), BL = 16 / S.timeSig.den;
  $("selPos").textContent = n ? `${Math.floor(n.start16 / ML) + 1}마디 ${Math.floor((n.start16 % ML) / BL) + 1}박${(n.start16 % ML) % BL ? ` +${(n.start16 % ML) % BL}/${BL}` : ""}` : "-";
  $("selLen").textContent = n ? LEN_KO[n.dur16] || `${n.dur16}/16` : "-";
  $("selConf").textContent = n ? n.confidence.toFixed(2) : "-";
  if (!n) { $("selState").textContent = "-"; }
  else {
    const o = S.original.get(n.id);
    const changed = o && (o.pitch !== n.pitch || o.dur16 !== n.dur16);
    const parts = [];
    if (n.uncertain) parts.push(`? 불확실 · ${REASON_KO[n.reason] || "인식 불확실"}`);
    else if (n.checked) parts.push("■ 확인함");
    if (changed) parts.push(`고침 (원래 ${noteName(o.pitch, S.key)} · ${LEN_KO[o.dur16]})`);
    if (n.added) parts.push("+ 추가한 음 (AI가 빠뜨림)");
    $("selState").textContent = parts.join(" · ") || "■ 인식 그대로";
  }
  $("selInfo").textContent = n ? `선택: ${noteName(n.pitch, S.key)} (${S.notes.indexOf(n) + 1} / ${S.notes.length})` : "음을 누르면 선택됩니다";
  document.querySelectorAll("#durSeg button").forEach((b) => b.setAttribute("aria-pressed", String(!!n && Number(b.dataset.dur) === n.dur16)));
}

function setControls() {
  const has = S.notes.length > 0;
  const n = sel();
  document.querySelectorAll(".ed").forEach((b) => (b.disabled = !n || S.busy));
  $("btnUndo").disabled = !S.history.length || S.busy;
  $("btnPlay").disabled = !has;
  $("btnMidi").disabled = !has || S.busy;
  ["bpmIn", "btnHalf", "btnDouble", "btnRetempo"].forEach((id) => ($(id).disabled = !S.wav || S.busy || !S.result));
  $("floorSel").disabled = !S.wav || S.busy || !S.result || S.mode !== "poly";
  $("pickupSel").disabled = !has || S.busy;
  $("timeSel").disabled = !has || S.busy;
  const fs = firstStart();
  $("pickupSel").value = [...$("pickupSel").options].some((o) => o.value === String(fs)) ? String(fs) : "";
  document.querySelectorAll("#modeSeg button").forEach((b) => (b.disabled = S.busy || !!recorder));
  $("btnRec").disabled = S.busy && !recorder;
  $("btnFile").disabled = S.busy || !!recorder;
}

/* ── 고치기 ── */
function snapshot() {
  S.history.push({ notes: S.notes.map((n) => ({ ...n })), selectedId: S.selectedId });
  if (S.history.length > 100) S.history.shift();
}
function undo() {
  const h = S.history.pop();
  if (!h) return;
  S.notes = h.notes;
  S.selectedId = h.selectedId;
  refresh();
}
/* 빠진 음 추가: 선택한 음 바로 뒤에 같은 높이의 음을 넣는다.
   뒤에 쉼표(빈칸)가 있으면 그 자리를 채우고, 없으면 뒤 음들을 밀어서 끼워 넣는다. */
const NOTE_VALUES = [16, 12, 8, 6, 4, 3, 2, 1];
function addNote() {
  const n = sel();
  if (!n || S.busy) return;
  snapshot();
  const end = n.start16 + n.dur16;
  const next = [...S.notes].sort((a, b) => a.start16 - b.start16).find((m) => m.start16 >= end && m.id !== n.id);
  const gap = next ? next.start16 - end : n.dur16;
  let dur;
  if (gap >= 1) {
    dur = NOTE_VALUES.find((v) => v <= Math.min(gap, n.dur16));
  } else {
    dur = n.dur16;
    for (const m of S.notes) if (m.id !== n.id && m.start16 >= end) m.start16 += dur;
  }
  const id = S.nextId++;
  S.notes.push({ id, pitch: n.pitch, start16: end, dur16: dur, uncertain: false, confidence: 1, reason: null, checked: true, added: true });
  S.selectedId = id;
  refresh();
  preview(sel());
}

/* 못갖춘마디: 첫 음이 마디 안 원하는 박에서 시작하도록 전체를 옮긴다. 음 자체는 바꾸지 않는다. */
function firstStart() {
  return S.notes.length ? Math.min(...S.notes.map((n) => n.start16)) : 0;
}
function fillPickupOptions() {
  const sel = $("pickupSel"), BL = 16 / S.timeSig.den;
  sel.innerHTML = "";
  for (let b = 0; b < S.timeSig.num; b++) {
    const add = (v, label) => { const o = document.createElement("option"); o.value = String(v); o.textContent = label; sel.appendChild(o); };
    add(b * BL, b === 0 ? "1박 (정박)" : `${b + 1}박`);
    if (BL >= 4) add(b * BL + BL / 2, `${b + 1}박 뒷박`);
  }
}
/* 박자표: 마디 나누기만 바뀐다. 음은 그대로. */
function setTimeSig(value) {
  const [num, den] = value.split("/").map(Number);
  S.timeSig = { num, den };
  const ML = measureLen(S.timeSig), fs = firstStart();
  if (fs >= ML) for (const m of S.notes) m.start16 -= Math.floor(fs / ML) * ML;
  fillPickupOptions();
  refresh();
}
function setPickup(target) {
  if (!S.notes.length || S.busy) return;
  const delta = target - firstStart();
  if (!delta) return;
  snapshot();
  for (const m of S.notes) m.start16 += delta;
  refresh();
}

function edit(op) {
  const n = sel();
  if (!n || S.busy) return;
  if (op === "add") { addNote(); return; }
  snapshot();
  if (op === "up1") n.pitch = Math.min(127, n.pitch + 1);
  if (op === "down1") n.pitch = Math.max(0, n.pitch - 1);
  if (op === "up12") n.pitch = Math.min(127, n.pitch + 12);
  if (op === "down12") n.pitch = Math.max(0, n.pitch - 12);
  if (op.startsWith("up") || op.startsWith("down")) { n.uncertain = false; n.checked = true; preview(n); }
  if (op === "ok") { n.uncertain = false; n.checked = true; }
  if (op === "del") {
    const i = S.notes.indexOf(n);
    S.notes.splice(i, 1);
    S.selectedId = (S.notes[i] || S.notes[i - 1])?.id ?? null;
  }
  refresh();
}
function setDur(dur) {
  const n = sel();
  if (!n || S.busy || n.dur16 === dur) return;
  snapshot();
  const delta = dur - n.dur16;
  for (const m of S.notes) if (m.start16 > n.start16) m.start16 += delta;
  n.dur16 = dur;
  n.uncertain = false;
  n.checked = true;
  refresh();
}
function move(dir) {
  if (!S.notes.length) return;
  const sorted = [...S.notes].sort((a, b) => a.start16 - b.start16);
  const i = sorted.findIndex((n) => n.id === S.selectedId);
  const next = sorted[Math.max(0, Math.min(sorted.length - 1, (i < 0 ? 0 : i + dir)))];
  S.selectedId = next.id;
  refresh();
  preview(next);
}

/* 고친 음을 짧게 들려준다 */
const blip = new Player();
function preview(n) {
  if (player.playing) return;
  blip.play([{ pitch: n.pitch, start16: 0, dur16: 2 }], S.bpm);
}

/* ── 재생 ── */
function togglePlay() {
  if (player.playing) { stopPlay(); return; }
  const sorted = [...S.notes].sort((a, b) => a.start16 - b.start16);
  $("playLbl").textContent = "STOP";
  $("playKo").textContent = "멈추기";
  $("playIcon").innerHTML = '<path d="M4 3h3v10H4zM9 3h3v10H9z"/>';
  player.play(sorted, S.bpm, (i) => {
    document.querySelectorAll("#score .playing").forEach((g) => g.classList.remove("playing"));
    document.querySelectorAll(`#score [data-id="${sorted[i].id}"]`).forEach((g) => g.classList.add("playing"));
  }, stopPlay);
}
function stopPlay() {
  player.stop();
  document.querySelectorAll("#score .playing").forEach((g) => g.classList.remove("playing"));
  $("playLbl").textContent = "PLAY";
  $("playKo").textContent = "들어 보기";
  $("playIcon").innerHTML = '<path d="M4 2.5v11l9-5.5z"/>';
}

/* ── 템포·최저음 다시 맞추기 ── */
function retempo() {
  const bpm = Number($("bpmIn").value);
  if (!(bpm >= 30 && bpm <= 300)) { message("템포는 30~300 사이로 정해 주세요", true); return; }
  if (editedCount() > 0 && !confirm("다시 맞추면 고친 내용이 사라져요. 계속할까요?")) {
    $("floorSel").value = S.floorAuto ? "auto" : String(S.floor);
    return;
  }
  stopPlay();
  // 템포를 건드리지 않았으면 자동 추정을 유지한다 (최저음만 바꾼 경우)
  const sendBpm = S.bpmSet || bpm !== Math.round(S.bpm) ? bpm : null;
  const v = $("floorSel").value;
  transcribe({ bpm: sendBpm, floor: v === "auto" ? null : Number(v) });
}

/* ── 녹음 종류 ── */
function setMode(mode) {
  if (mode === S.mode || S.busy) return;
  if (S.result && editedCount() > 0 && !confirm("녹음 종류를 바꾸면 다시 변환해서 고친 내용이 사라져요. 계속할까요?")) return;
  S.mode = mode;
  document.querySelectorAll("#modeSeg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
  setStageLabels();
  if (S.wav) { stopPlay(); S.bpmSet = false; transcribe({}); }
  else setControls();
}
function setStageLabels() {
  for (const k of ["pitch", "segment"]) {
    document.querySelector(`.stage[data-key="${k}"] .ko`).textContent = STAGE_KO[S.mode][k];
  }
}
function fillFloorOptions() {
  const sel = $("floorSel");
  sel.innerHTML = '<option value="auto">자동</option>';
  for (let p = 36; p <= 72; p++) {
    const o = document.createElement("option");
    o.value = String(p);
    o.textContent = noteName(p, "C");
    sel.appendChild(o);
  }
}

/* ── MIDI 내보내기 + 피드백 기록 ── */
async function exportMidi() {
  const notes = [...S.notes].sort((a, b) => a.start16 - b.start16).map(({ pitch, start16, dur16 }) => ({ pitch, start16, dur16 }));
  try {
    const r = await fetch("/api/midi", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bpm: S.bpm, key: S.key, numerator: S.timeSig.num, denominator: S.timeSig.den, notes }),
    });
    if (!r.ok) throw new Error();
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `melodynote-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "")}.mid`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  } catch {
    message("MIDI 파일을 만들지 못했어요. 서버가 켜져 있는지 확인해 주세요", true);
    return;
  }
  const ed = editedCount();
  fetch("/api/feedback", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      duration_sec: S.result?.duration_sec, bpm: S.bpm, bpm_set: S.bpmSet, key: S.key,
      time_signature: `${S.timeSig.num}/${S.timeSig.den}`, mode: S.result?.mode,
      timings: S.result?.timings, original: S.result?.notes, edited: S.notes,
      edited_count: ed, original_count: S.original.size,
      edited_ratio: S.original.size ? ed / S.original.size : 0,
    }),
  }).catch(() => {});
  message(`MIDI로 내보냈어요. 고친 음 ${ed} / ${S.original.size}`, false);
}

/* ── 연결 ── */
$("btnRec").addEventListener("click", toggleRec);
$("btnFile").addEventListener("click", () => $("fileInput").click());
$("fileInput").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  if (f) await handleAudio(await f.arrayBuffer(), { name: f.name, type: f.type });
});
document.querySelectorAll(".btn.ed[data-op]").forEach((b) => b.addEventListener("click", () => edit(b.dataset.op)));
document.querySelectorAll("#durSeg button").forEach((b) => b.addEventListener("click", () => setDur(Number(b.dataset.dur))));
$("btnUndo").addEventListener("click", undo);
$("btnPlay").addEventListener("click", togglePlay);
$("btnHalf").addEventListener("click", () => { $("bpmIn").value = Math.round(Number($("bpmIn").value || S.bpm) / 2); });
$("btnDouble").addEventListener("click", () => { $("bpmIn").value = Math.round(Number($("bpmIn").value || S.bpm) * 2); });
$("btnRetempo").addEventListener("click", retempo);
$("floorSel").addEventListener("change", retempo);
$("pickupSel").addEventListener("change", (e) => setPickup(Number(e.target.value)));
$("timeSel").addEventListener("change", (e) => setTimeSig(e.target.value));
document.querySelectorAll("#modeSeg button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
$("bpmIn").addEventListener("keydown", (e) => { if (e.key === "Enter") retempo(); });
$("btnMidi").addEventListener("click", exportMidi);

document.addEventListener("keydown", (e) => {
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || !S.notes.length) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); undo(); return; }
  const map = { ArrowLeft: () => move(-1), ArrowRight: () => move(1),
    ArrowUp: () => edit(e.shiftKey ? "up12" : "up1"), ArrowDown: () => edit(e.shiftKey ? "down12" : "down1"),
    Delete: () => edit("del"), Backspace: () => edit("del"),
    "+": () => edit("add"), "=": () => edit("add"), Insert: () => edit("add") };
  if (map[e.key] && (tag !== "button" || e.key.startsWith("Arrow") || ["Delete", "+", "=", "Insert"].includes(e.key))) { e.preventDefault(); map[e.key](); }
});

// 악보 판 폭이 바뀌면 다시 그린다 (창 크기, 휴대폰 회전)
let resizeT, lastW = 0;
function onPlateResize() {
  const w = document.querySelector(".plate").clientWidth;
  if (w === lastW) return;
  lastW = w;
  clearTimeout(resizeT);
  resizeT = setTimeout(() => S.notes.length && drawScore(), 150);
}
const plateObserver = new ResizeObserver(onPlateResize);
plateObserver.observe(document.querySelector(".plate"));
window.addEventListener("resize", onPlateResize);

fillFloorOptions();
fillPickupOptions();
setStageLabels();
resetBand();
setControls();
checkServer();
watchSections();
