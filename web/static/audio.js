// 소리 다루기: 마이크 녹음, 브라우저에서 디코딩 → 22.05kHz 모노 WAV, 미리 듣기 합성

export const TARGET_SR = 22050;
export const MAX_SEC = 60;

/** 어떤 형식이든 브라우저가 읽을 수 있으면 22.05kHz 모노로 바꾼다. 서버는 WAV만 받으면 된다. */
export async function decodeToMono(arrayBuffer) {
  const AC = window.AudioContext || window.webkitAudioContext;
  const ctx = new AC();
  let buf;
  try {
    buf = await ctx.decodeAudioData(arrayBuffer.slice(0));
  } finally {
    ctx.close();
  }
  const length = Math.max(1, Math.ceil(buf.duration * TARGET_SR));
  const off = new OfflineAudioContext(1, length, TARGET_SR);
  const src = off.createBufferSource();
  src.buffer = buf;
  src.connect(off.destination);
  src.start();
  const rendered = await off.startRendering();
  return { samples: rendered.getChannelData(0), sr: TARGET_SR, duration: buf.duration, channels: buf.numberOfChannels, srcRate: buf.sampleRate };
}

export function encodeWav(samples, sr) {
  const n = samples.length;
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  w(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); w(8, "WAVE");
  w(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sr, true); v.setUint32(28, sr * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  w(36, "data"); v.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: "audio/wav" });
}

/** 마이크 녹음. 음높이 인식에 방해되지 않게 잡음 제거·자동 음량은 끈다. */
export class Recorder {
  constructor(onLevel) { this.onLevel = onLevel; }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
    });
    const types = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"];
    const mimeType = types.find((t) => window.MediaRecorder && MediaRecorder.isTypeSupported(t));
    this.rec = new MediaRecorder(this.stream, mimeType ? { mimeType } : undefined);
    this.chunks = [];
    this.rec.ondataavailable = (e) => e.data.size && this.chunks.push(e.data);
    this.rec.start(250);
    // 입력 음량 표시 (실제 마이크 값)
    const AC = window.AudioContext || window.webkitAudioContext;
    this.ctx = new AC();
    const an = this.ctx.createAnalyser();
    an.fftSize = 1024;
    this.ctx.createMediaStreamSource(this.stream).connect(an);
    const data = new Float32Array(an.fftSize);
    const tick = () => {
      an.getFloatTimeDomainData(data);
      let peak = 0;
      for (const x of data) peak = Math.max(peak, Math.abs(x));
      this.onLevel?.(peak);
      this.raf = requestAnimationFrame(tick);
    };
    tick();
    return this.rec.mimeType;
  }

  stop() {
    return new Promise((resolve) => {
      cancelAnimationFrame(this.raf);
      this.onLevel?.(0);
      this.rec.onstop = () => {
        this.stream.getTracks().forEach((t) => t.stop());
        this.ctx.close();
        resolve(new Blob(this.chunks, { type: this.rec.mimeType || "audio/webm" }));
      };
      this.rec.stop();
    });
  }
}

/** 악보를 간단한 소리로 들려준다. onNote(index)로 지금 나는 음을 알린다. */
export class Player {
  play(notes, bpm, onNote, onEnd) {
    this.stop();
    const AC = window.AudioContext || window.webkitAudioContext;
    const ctx = (this.ctx = new AC());
    const sec16 = 60 / bpm / 4;
    const t0 = ctx.currentTime + 0.12;
    const master = ctx.createGain();
    master.gain.value = 0.22;
    master.connect(ctx.destination);
    this.timers = [];
    let end = 0;
    notes.forEach((n, i) => {
      const start = t0 + n.start16 * sec16;
      const dur = n.dur16 * sec16;
      end = Math.max(end, n.start16 * sec16 + dur);
      const osc = ctx.createOscillator();
      const g = ctx.createGain();
      osc.type = "triangle";
      osc.frequency.value = 440 * Math.pow(2, (n.pitch - 69) / 12);
      g.gain.setValueAtTime(0, start);
      g.gain.linearRampToValueAtTime(1, start + 0.015);
      g.gain.setValueAtTime(1, start + Math.max(0.03, dur * 0.85));
      g.gain.linearRampToValueAtTime(0, start + Math.max(0.05, dur * 0.95));
      osc.connect(g).connect(master);
      osc.start(start);
      osc.stop(start + dur);
      this.timers.push(setTimeout(() => onNote?.(i), (start - ctx.currentTime) * 1000));
    });
    this.timers.push(setTimeout(() => { this.stop(); onEnd?.(); }, (end + 0.3) * 1000));
  }

  stop() {
    (this.timers || []).forEach(clearTimeout);
    this.timers = [];
    if (this.ctx) { this.ctx.close(); this.ctx = null; }
  }

  get playing() { return !!this.ctx; }
}
