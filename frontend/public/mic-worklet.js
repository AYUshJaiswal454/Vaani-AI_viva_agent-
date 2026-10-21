// AudioWorklet: microphone -> 16 kHz mono PCM16 in 100 ms (1600-sample) chunks, posted to the main thread.
// If the AudioContext could not run at 16 kHz, a simple linear-interpolation resampler is used.
class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.buf = new Float32Array(1600);
    this.n = 0;
    this.pos = 0;     // fractional read position for resampling
    this.carry = new Float32Array(0);
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    let src = ch;
    if (this.carry.length) {
      src = new Float32Array(this.carry.length + ch.length);
      src.set(this.carry, 0);
      src.set(ch, this.carry.length);
    }
    if (this.ratio === 1) {
      for (let i = 0; i < src.length; i++) this.push(src[i]);
      this.carry = new Float32Array(0);
    } else {
      let p = this.pos;
      while (p + 1 < src.length) {
        const i = Math.floor(p);
        const f = p - i;
        this.push(src[i] * (1 - f) + src[i + 1] * f);
        p += this.ratio;
      }
      const keep = Math.floor(p);
      this.carry = src.slice(keep);
      this.pos = p - keep;
    }
    return true;
  }
  push(v) {
    this.buf[this.n++] = v;
    if (this.n === 1600) {
      const pcm = new Int16Array(1600);
      let sum = 0;
      for (let i = 0; i < 1600; i++) {
        const s = Math.max(-1, Math.min(1, this.buf[i]));
        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        sum += s * s;
      }
      this.port.postMessage({ pcm: pcm.buffer, rms: Math.sqrt(sum / 1600) }, [pcm.buffer]);
      this.n = 0;
    }
  }
}
registerProcessor("mic-processor", MicProcessor);
