// Real-time Multi-track Audio Synthesizer Engine (Web Audio API)

class RealtimeAudioEngine {
  private ctx: AudioContext | null = null;
  private activeNodes: Map<string, { osc: OscillatorNode; gain: GainNode }> = new Map();

  private getContext(): AudioContext {
    if (!this.ctx) {
      const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
      this.ctx = new AudioCtx();
    }
    if (this.ctx.state === 'suspended') {
      this.ctx.resume();
    }
    return this.ctx;
  }

  // MIDI Note to Frequency
  public midiToFreq(midi: number): number {
    return 440 * Math.pow(2, (midi - 69) / 12);
  }

  // Play Note Preview Real-time
  public playNotePreview(
    noteNum: number,
    durationSec: number = 0.4,
    volume: number = 0.5,
    pitchCentOffset: number = 0
  ) {
    try {
      const ctx = this.getContext();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      const filter = ctx.createBiquadFilter();

      const baseFreq = this.midiToFreq(noteNum);
      const freqWithPitch = baseFreq * Math.pow(2, pitchCentOffset / 1200);

      osc.type = 'sawtooth';
      osc.frequency.setValueAtTime(freqWithFreqBoundary(freqWithPitch), ctx.currentTime);

      // クリアなボーカルフォルマント・プレゼンスフィルター (篭もりを排除し抜けの良い音に)
      filter.type = 'lowpass';
      filter.frequency.setValueAtTime(8500, ctx.currentTime);
      filter.Q.setValueAtTime(0.707, ctx.currentTime);

      const presenceFilter = ctx.createBiquadFilter();
      presenceFilter.type = 'peaking';
      presenceFilter.frequency.setValueAtTime(3600, ctx.currentTime);
      presenceFilter.gain.setValueAtTime(3.5, ctx.currentTime);
      presenceFilter.Q.setValueAtTime(1.0, ctx.currentTime);

      // Envelope
      const now = ctx.currentTime;
      gain.gain.setValueAtTime(0, now);
      gain.gain.linearRampToValueAtTime(volume, now + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, now + durationSec);

      osc.connect(presenceFilter);
      presenceFilter.connect(filter);
      filter.connect(gain);
      gain.connect(ctx.destination);

      osc.start(now);
      osc.stop(now + durationSec);
    } catch (e) {
      console.warn('Realtime preview audio error:', e);
    }
  }

  // Realtime Multi-track Tick Scheduler
  public scheduleTickNotes(
    tracks: any[],
    currentTick: number,
    tempo: number,
    tempoTickLength: number = 60
  ) {
    try {
      const ctx = this.getContext();
      const secondsPerTick = 60 / (tempo * 480);

      tracks.forEach((track) => {
        if (track.isMuted || track.type !== 'vocal') return;

        const vol = track.volume ?? 1.0;

        track.notes.forEach((note: any) => {
          if (!note.lyric || ['r', 'r_', '息', 'br', 'pau', 'sil', '吸', '', ' ', '　', '休', '・', '-'].includes(note.lyric.trim().toLowerCase())) return;
          // Check if currentTick crosses the note start
          if (currentTick >= note.tick && currentTick < note.tick + tempoTickLength) {
            const noteDurSec = Math.max(0.08, note.length * secondsPerTick);
            this.playNotePreview(note.noteNum, noteDurSec, Math.min(0.8, vol * 0.6));
          }
        });
      });
    } catch (e) {
      // Ignore audio suspended errors
    }
  }
}

function freqWithFreqBoundary(freq: number): number {
  return Math.max(20, Math.min(20000, freq));
}

export const realtimeEngine = new RealtimeAudioEngine();
export default realtimeEngine;
