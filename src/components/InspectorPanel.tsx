import React from 'react';
import { Trash2, Type, AudioWaveform, Sliders } from 'lucide-react';
import PitchCurveMiniEditor from './PitchCurveMiniEditor';

export interface Note {
  id: string;
  lyric: string;
  noteNum: number;
  tick: number;
  length: number;
  intensity: number;
  flags: string;
  pbs: string;
  pbw: string;
  pby: string;
  tempo?: number;
}

export interface InspectorPanelProps {
  selectedNote: Note | null;
  onUpdateNote: (field: keyof Note, value: any) => void;
  onDeleteNote: (noteId: string) => void;
  onOpenBatchLyrics?: () => void;
  tempo: number;
  getNoteName: (midi: number) => string;
  isCompact?: boolean;
}

export const InspectorPanel: React.FC<InspectorPanelProps> = ({
  selectedNote,
  onUpdateNote,
  onDeleteNote,
  onOpenBatchLyrics,
  tempo,
  getNoteName,
  isCompact = false,
}) => {
  if (!selectedNote) {
    return (
      <div className="flex flex-col items-center justify-center p-5 text-center h-full min-h-[180px]">
        <div className="w-10 h-10 rounded-xl bg-[var(--vose-bg-elevated)] border border-[var(--vose-border)] text-[var(--vose-text-secondary)] flex items-center justify-center mb-3">
          <Sliders className="w-5 h-5 opacity-70" />
        </div>
        <p className="text-sm font-semibold text-[var(--vose-text-primary)]">ノートを選択してください</p>
        <p className="mt-1 text-[11px] leading-5 text-[var(--vose-text-muted)]">Timeline上のノートを選択すると<br />歌詞・音高・ピッチベンドを編集できます。</p>
      </div>
    );
  }

  const sectionClass = 'rounded-lg border border-[var(--vose-border)] bg-[var(--vose-bg-panel)] p-2.5';
  const fieldLabelClass = 'text-[var(--vose-text-secondary)] font-medium block mb-1';
  const inputClass = 'w-full h-10 bg-[var(--vose-bg-base)] border border-[#3a3a40] rounded-lg px-3 text-[var(--vose-text-primary)] focus:border-[#0a84ff] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff] focus-visible:ring-offset-1 focus-visible:ring-offset-[var(--vose-bg-panel)]';
  const compactInputClass = 'w-full h-8 bg-[var(--vose-bg-base)] border border-[#3a3a40] rounded px-2 text-[var(--vose-text-primary)] font-mono text-[11px] focus:border-[#0a84ff] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff] focus-visible:ring-offset-1 focus-visible:ring-offset-[var(--vose-bg-panel)]';

  return (
    <div className={`flex flex-col text-xs ${isCompact ? 'space-y-3' : 'space-y-3.5'}`}>
      {/* Header: identity + destructive action */}
      <div className="flex items-center justify-between border-b border-[var(--vose-border)] pb-2.5">
        <div className="flex items-center space-x-2 min-w-0">
          <span className="w-6 h-6 shrink-0 rounded-md bg-[#0a84ff]/20 border border-[#0a84ff]/50 text-[#2997ff] font-bold font-mono text-xs flex items-center justify-center">
            {getNoteName(selectedNote.noteNum)}
          </span>
          <div className="min-w-0">
            <h4 className="font-bold text-[var(--vose-text-primary)] text-xs">ノート情報</h4>
            <span className="text-[10px] text-[var(--vose-text-muted)] font-mono">Tick {selectedNote.tick}</span>
          </div>
        </div>

        <button
          onClick={() => onDeleteNote(selectedNote.id)}
          className="min-w-[36px] min-h-[36px] px-2.5 py-1 text-[#ff453a] hover:text-white bg-[#ff453a]/15 hover:bg-[#ff453a]/30 border border-[#ff453a]/40 rounded-lg flex items-center space-x-1 transition cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff]"
          title="ノートを削除"
          aria-label="ノートを削除"
        >
          <Trash2 className="w-3.5 h-3.5" />
          <span className="text-[11px]">削除</span>
        </button>
      </div>

      <section className={`${sectionClass} space-y-2.5`}>
        <div className="flex items-center justify-between">
          <h5 className="text-[10px] font-semibold tracking-wide text-[var(--vose-text-muted)] uppercase">基本情報</h5>
          {onOpenBatchLyrics && (
            <button
              type="button"
              onClick={onOpenBatchLyrics}
              className="text-[10px] text-[#2997ff] hover:text-[#0a84ff] hover:underline flex items-center gap-0.5 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff] rounded"
            >
              <Type className="w-3 h-3" />
              <span>一括入力</span>
            </button>
          )}
        </div>

        <div>
          <label className={fieldLabelClass}>歌詞 / 音素 (Lyrics):</label>
          <input type="text" value={selectedNote.lyric} onChange={(e) => onUpdateNote('lyric', e.target.value)} className={`${inputClass} font-bold text-sm`} />
        </div>

        <div>
          <label className={fieldLabelClass}>音高 (Pitch / Note):</label>
          <div className="grid grid-cols-2 gap-2">
            <input type="number" min="36" max="84" value={selectedNote.noteNum} onChange={(e) => onUpdateNote('noteNum', parseInt(e.target.value) || 60)} className={`${inputClass} font-mono text-center font-bold`} />
            <div className="h-10 bg-[var(--vose-bg-elevated)] border border-[#3a3a40] rounded-lg text-[#2997ff] font-mono font-bold flex items-center justify-center text-sm shadow-inner">
              {getNoteName(selectedNote.noteNum)}
            </div>
          </div>
        </div>

        <div>
          <label className={fieldLabelClass}>長さ (Length Ticks):</label>
          <input type="number" step="60" value={selectedNote.length} onChange={(e) => onUpdateNote('length', parseInt(e.target.value) || 480)} className={`${inputClass} font-mono`} />
        </div>
      </section>

      <section className={`${sectionClass} space-y-2.5`}>
        <h5 className="text-[10px] font-semibold tracking-wide text-[var(--vose-text-muted)] uppercase">発音・強度</h5>

        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-[var(--vose-text-secondary)] font-medium">音量強度 (Intensity):</label>
            <span className="font-mono text-[#2997ff] font-bold">{selectedNote.intensity}</span>
          </div>
          <input type="range" min="0" max="150" value={selectedNote.intensity} onChange={(e) => onUpdateNote('intensity', parseFloat(e.target.value))} className="w-full h-2 accent-[#0a84ff] bg-[var(--vose-bg-elevated)] rounded-lg appearance-none cursor-pointer" aria-label="音量強度" />
        </div>

        <div>
          <label className={fieldLabelClass}>フラグ <span className="text-[10px] text-[var(--vose-text-muted)]">(例: g-5B50)</span></label>
          <input type="text" value={selectedNote.flags} onChange={(e) => onUpdateNote('flags', e.target.value)} placeholder="g-5B50" className={`${inputClass} font-mono text-xs placeholder-[#55555c]`} />
        </div>
      </section>

      <section className={sectionClass}>
        <label className="text-[#d5d5da] font-medium block mb-2 flex items-center space-x-1">
          <AudioWaveform className="w-3.5 h-3.5 text-[#0a84ff]" />
          <span>ピッチベンド曲線</span>
        </label>

        <div className="grid grid-cols-[auto_1fr] items-center gap-x-2 gap-y-1.5">
          <span className="text-[10px] text-[var(--vose-text-muted)]">PBS</span>
          <input type="text" value={selectedNote.pbs} onChange={(e) => onUpdateNote('pbs', e.target.value)} aria-label="PBS 開始点" className={compactInputClass} />
          <span className="text-[10px] text-[var(--vose-text-muted)]">PBW</span>
          <input type="text" value={selectedNote.pbw} onChange={(e) => onUpdateNote('pbw', e.target.value)} aria-label="PBW 各点幅" className={compactInputClass} />
          <span className="text-[10px] text-[var(--vose-text-muted)]">PBY</span>
          <input type="text" value={selectedNote.pby} onChange={(e) => onUpdateNote('pby', e.target.value)} aria-label="PBY 各点高さ" className={compactInputClass} />
        </div>

        <p className="mt-1.5 text-[9px] text-[#6f6f78] leading-relaxed">PBS: 開始点 / PBW: 幅 / PBY: 高さ</p>

        <div className="mt-3">
          <PitchCurveMiniEditor
            note={selectedNote}
            tempo={tempo}
            onUpdate={(pbs, pbw, pby) => {
              onUpdateNote('pbs', pbs);
              onUpdateNote('pbw', pbw);
              onUpdateNote('pby', pby);
            }}
          />
        </div>
      </section>
    </div>
  );
};

export default InspectorPanel;
