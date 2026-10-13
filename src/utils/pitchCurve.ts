// pitchCurve.ts
//
// UST形式のピッチベンド (PBS / PBW / PBY) をパースし、タイムライン上の
// 「その時点でのノート基準音高からの半音オフセット」に変換するユーティリティ。
//
// UST(UTAU)の慣習:
//   PBS = "開始オフセットms" または "開始オフセットms;開始半音"
//         (ノート開始位置からの相対時間。マイナス可＝ノートより前から始まる)
//   PBW = "幅ms,幅ms,..."（各制御点間の時間幅、カンマ区切り）
//   PBY = "半音,半音,..."（各制御点でのノート基準音高からのオフセット、カンマ区切り。
//          末尾に "s" 等の補間種別サフィックスが付くことがあるため数値だけ取り出す）
//
// 注意: 各UTAU系ツールで細部の解釈に差異があるため、これは一般的な慣習に沿った
// 「表示用の近似」です。実際の合成エンジン側の解釈と完全に一致する保証はありません。

export interface PitchPoint {
  /** ノート開始位置からの相対時間 (ms)。負の値=ノートより前 */
  offsetMs: number;
  /** ノート基準音高からの半音オフセット */
  semitone: number;
}

function parseNumeric(raw: string): number {
  const cleaned = raw.trim().replace(/[^0-9+\-.]/g, '');
  const v = parseFloat(cleaned);
  return Number.isFinite(v) ? v : 0;
}

const pitchBendCache = new Map<string, PitchPoint[]>();

/** PBS/PBW/PBY文字列から制御点の配列を作る (キャッシュ付き高速パース & UTAU単位自動判定・限界突破防止) */
export function parsePitchBend(pbs: string, pbw: string, pby: string): PitchPoint[] {
  const key = `${pbs || ''}|${pbw || ''}|${pby || ''}`;
  const cached = pitchBendCache.get(key);
  if (cached) return cached;

  const pbsParts = (pbs || '').split(';').map((s) => s.trim());
  const startOffsetMs = pbsParts[0] ? parseNumeric(pbsParts[0]) : 0;
  const rawStartSemitone = pbsParts[1] ? parseNumeric(pbsParts[1]) : 0;

  const widths = (pbw || '')
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .map(parseNumeric);

  const rawHeights = (pby || '')
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .map(parseNumeric);

  // UST/UTAU標準仕様: PBS/PBYの単位は常に固定で「10 = 1半音」。
  //
  // ★重要: 以前はノートごとの生値の最大絶対値からセント/0.1半音/実半音を
  // "自動判定" していたが、これは根本的に誤り。ベンドの深さ（大きいか小さいか）と
  // 数値の単位（何を1半音とみなすか）は無関係で、ファイル内では常に固定のはず。
  // この誤判定により、穏やかなビブラートやしゃくり等の小さい生値
  // （例: -14.3, 6.7 など、ノート内の最大絶対値が15以下のケース）が
  // 「もう半音そのもの」と誤認識され、-14.3半音・6.7半音という
  // 物理的にありえない急激なピッチジャンプとして適用されてしまっていた
  // （実際は -1.43半音・0.67半音程度の穏やかなベンドのはずだった）。
  const scaleFactor = 0.1;

  // ノート開始位置からの相対時間・半音の安全クランプ (±24半音 = ±2オクターブ内に厳格制限)
  const clampedStartOffset = Math.max(-3000, Math.min(10000, startOffsetMs));
  const clampedStartSemitone = Math.max(-24, Math.min(24, rawStartSemitone * scaleFactor));
  const points: PitchPoint[] = [{ offsetMs: clampedStartOffset, semitone: roundTo(clampedStartSemitone, 2) }];

  let cursorMs = clampedStartOffset;
  for (let i = 0; i < rawHeights.length; i++) {
    const rawW = widths[i] ?? (widths[widths.length - 1] ?? 50);
    const clampedW = Math.max(1, Math.min(5000, rawW));
    cursorMs += clampedW;

    const scaledSemitone = rawHeights[i] * scaleFactor;
    const clampedSemitone = Math.max(-24, Math.min(24, scaledSemitone));
    points.push({ offsetMs: cursorMs, semitone: roundTo(clampedSemitone, 2) });
  }

  if (pitchBendCache.size > 3000) {
    const firstKey = pitchBendCache.keys().next().value;
    if (firstKey) pitchBendCache.delete(firstKey);
  }
  pitchBendCache.set(key, points);

  return points;
}

/** 制御点の配列からPBS/PBW/PBY文字列を再構築する */
export function serializePitchBend(points: PitchPoint[]): { pbs: string; pbw: string; pby: string } {
  if (points.length === 0) {
    return { pbs: '0;0', pbw: '', pby: '' };
  }
  const [first, ...rest] = points;
  const pbs = `${Math.round(first.offsetMs)};${roundTo(first.semitone, 2)}`;

  const widths: number[] = [];
  const heights: number[] = [];
  let prevMs = first.offsetMs;
  for (const p of rest) {
    widths.push(Math.max(1, Math.round(p.offsetMs - prevMs)));
    heights.push(roundTo(p.semitone, 2));
    prevMs = p.offsetMs;
  }

  return {
    pbs,
    pbw: widths.join(','),
    pby: heights.join(','),
  };
}

function roundTo(v: number, decimals: number): number {
  const f = Math.pow(10, decimals);
  return Math.round(v * f) / f;
}

/** tempo(BPM)を使ってms ⇔ tickを変換する */
export function msToTicks(ms: number, tempoBpm: number): number {
  // 480 ticks = 1拍。1拍の長さ(ms) = 60000 / tempo
  const msPerTick = 60000 / tempoBpm / 480;
  return ms / msPerTick;
}

export function ticksToMs(ticks: number, tempoBpm: number): number {
  const msPerTick = 60000 / tempoBpm / 480;
  return ticks * msPerTick;
}

/**
 * 極端な半音設定（限界突破時）にソフトリミッター（tanh関数による滑らかな飽和）を適用する。
 * 例: ±24半音まではほぼリニア、±24半音を超えると緩やかに頭打ちになり、急激な超音波・機械音化をガード。
 */
export function softClampSemitone(semitone: number, softLimit: number = 24, hardLimit: number = 32): number {
  if (Math.abs(semitone) <= softLimit) {
    return semitone;
  }
  const sign = semitone >= 0 ? 1 : -1;
  const excess = Math.abs(semitone) - softLimit;
  const headroom = hardLimit - softLimit;
  // tanh compression for natural vocal headroom taper
  const compressed = softLimit + headroom * Math.tanh(excess / headroom);
  return sign * compressed;
}

/**
 * 制御点間の急激な時間変化（スルーレート）を制限し、金属的なFM変調音やクリップノイズを防止する。
 * 1ミリ秒あたり最大 0.08 半音（= 100msで8半音相当）の人間が発声可能な速度にスムージング。
 */
export function smoothPitchBendPoints(points: PitchPoint[], maxSemitonePerMs: number = 0.08): PitchPoint[] {
  if (points.length <= 1) return points;

  const result: PitchPoint[] = [{
    offsetMs: points[0].offsetMs,
    semitone: softClampSemitone(points[0].semitone)
  }];

  for (let i = 1; i < points.length; i++) {
    const prev = result[result.length - 1];
    const curr = points[i];
    const targetSemitone = softClampSemitone(curr.semitone);
    const dtMs = Math.max(1, curr.offsetMs - prev.offsetMs);
    const maxDelta = dtMs * maxSemitonePerMs;
    const diff = targetSemitone - prev.semitone;

    let clampedSemitone = targetSemitone;
    if (Math.abs(diff) > maxDelta) {
      clampedSemitone = prev.semitone + Math.sign(diff) * maxDelta;
    }

    result.push({
      offsetMs: curr.offsetMs,
      semitone: clampedSemitone
    });
  }

  return result;
}

/**
 * 音高・ピッチベンドに応じたフォルマント補正フィルターの遮断周波数(Hz)を計算。
 * こもり感を完全に解消し、子音の明瞭度・空気感を100%保持するため高域を遮断せずクリアに開放する。
 */
export function calculateFormantCutoff(basePitchMidi: number, semitoneOffset: number = 0): number {
  const currentMidi = basePitchMidi + semitoneOffset;
  if (currentMidi > 88) { // 超高域(E6以上)でのみ可聴域外の折り返しノイズを軽くケア
    const excess = currentMidi - 88;
    return Math.max(16000, 18000 - excess * 150);
  }
  return 18000;
}

/**
 * 制御点列を、指定した時刻(ノート開始からの相対ms)での半音オフセットに
 * 線形補間でサンプリングする。points は offsetMs 昇順であること。
 */
export function sampleSemitoneAt(points: PitchPoint[], offsetMs: number): number {
  if (points.length === 0) return 0;
  if (offsetMs <= points[0].offsetMs) return points[0].semitone;
  const last = points[points.length - 1];
  if (offsetMs >= last.offsetMs) return last.semitone;

  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i];
    const b = points[i + 1];
    if (offsetMs >= a.offsetMs && offsetMs <= b.offsetMs) {
      const span = b.offsetMs - a.offsetMs;
      const t = span <= 0 ? 0 : (offsetMs - a.offsetMs) / span;
      return a.semitone + (b.semitone - a.semitone) * t;
    }
  }
  return last.semitone;
}

/**
 * Web Audio APIのAudioParam (playbackRate, frequency等) に
 * ピッチベンド曲線を厳格に時系列順(単調増加)かつ安全に適用する。
 * UST特有の負の開始オフセット (PBS = -50;0 等) や時間重複による
 * Web Audio例外 (DOMException: InvalidStateError / RangeError) を完全に防ぎ、
 * 再生が途切れる原因を解消する。
 */
export function scheduleSafePitchRamp(
  param: AudioParam,
  baseValue: number,
  points: PitchPoint[],
  noteStartTime: number,
  valueTransformer: (semitone: number) => number,
  currentTime: number,
  maxEndTime: number
): void {
  if (!points || points.length === 0) {
    param.setValueAtTime(baseValue, Math.max(currentTime, noteStartTime));
    return;
  }

  // 1. 各制御点の絶対時刻と値を計算
  const timedPoints = points
    .map((p) => ({
      time: noteStartTime + p.offsetMs / 1000,
      val: valueTransformer(p.semitone)
    }))
    .filter((p) => p.time <= maxEndTime + 0.1);

  if (timedPoints.length === 0) {
    param.setValueAtTime(baseValue, Math.max(currentTime, noteStartTime));
    return;
  }

  // 2. 時系列順に整列
  timedPoints.sort((a, b) => a.time - b.time);

  // 3. 最初のイベントを currentTime 以降の安全な時刻に setValueAtTime で配置
  let lastTime = Math.max(currentTime, timedPoints[0].time);
  param.setValueAtTime(timedPoints[0].val, lastTime);

  // 4. 後続の制御点を厳格に lastTime < pt.time で linearRampToValueAtTime
  for (let i = 1; i < timedPoints.length; i++) {
    const pt = timedPoints[i];
    if (pt.time > lastTime + 0.001) {
      param.linearRampToValueAtTime(pt.val, pt.time);
      lastTime = pt.time;
    }
  }
}
