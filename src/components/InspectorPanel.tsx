import React from 'react';
import { Edit3, Trash2, Type, AudioWaveform, Sliders, Volume2, Music } from 'lucide-react';
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
      <div className="flex flex-col items-center justify-center p-6 text-center text-[#7d7d86] space-y-2 h-full min-h-[180px]">
        <Sliders className="w-8 h-8 opacity-40 text-[#9a9aa2]" />
        <p className="text-xs">ノートを選択すると<br />パラメータを編集できます</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col space-y-4 text-xs">
      {/* Header with Note Info & Delete Button */}
      <div className="flex items-center justify-between border-b border-[#303034] pb-2.5">
        <div className="flex items-center space-x-2">
          <span className="w-6 h-6 rounded-md bg-[#0a84ff]/20 border border-[#0a84ff]/50 text-[#2997ff] font-bold font-mono text-xs flex items-center justify-center">
            {getNoteName(selectedNote.noteNum)}
          </span>
          <div>
            <h4 className="font-bold text-[#f0f0f2] text-xs">Note Inspector</h4>
            <span className="text-[10px] text-[#7d7d86] font-mono">Tick: {selectedNote.tick}</span>
          </div>
        </div>

        <button
          onClick={() => onDeleteNote(selectedNote.id)}
          className="min-w-[36px] min-h-[36px] px-2.5 py-1 text-[#ff453a] hover:text-white bg-[#ff453a]/15 hover:bg-[#ff453a]/30 border border-[#ff453a]/40 rounded-lg flex items-center space-x-1 transition cursor-pointer"
          title="Delete Note"
        >
          <Trash2 className="w-3.5 h-3.5" />
          <span className="text-[11px]">Delete</span>
        </button>
      </div>

      {/* Lyric & Phoneme */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-[#9a9aa2] font-medium">歌詞 / 音素 (Lyrics):</label>
          {onOpenBatchLyrics && (
            <button
              type="button"
              onClick={onOpenBatchLyrics}
              className="text-[10px] text-[#2997ff] hover:text-[#0a84ff] hover:underline flex items-center gap-0.5 cursor-pointer"
            >
              <Type className="w-3 h-3" />
              <span>一括入力</span>
            </button>
          )}
        </div>
        <input
          type="text"
          value={selectedNote.lyric}
          onChange={(e) => onUpdateNote('lyric', e.target.value)}
          className="w-full h-10 bg-[#18181a] border border-[#3a3a40] rounded-lg px-3 text-[#f0f0f2] font-bold text-sm focus:border-[#0a84ff] focus:outline-none"
        />
      </div>

      {/* Pitch (MIDI) & Note Name */}
      <div>
        <label className="text-[#9a9aa2] font-medium block mb-1">音高 (Pitch / Note):</label>
        <div className="grid grid-cols-2 gap-2">
          <input
            type="number"
            min="36"
            max="84"
            value={selectedNote.noteNum}
            onChange={(e) => onUpdateNote('noteNum', parseInt(e.target.value) || 60)}
            className="h-10 bg-[#18181a] border border-[#3a3a40] rounded-lg px-3 text-[#f0f0f2] font-mono text-center font-bold focus:border-[#0a84ff] focus:outline-none"
          />
          <div className="h-10 bg-[#2a2a2e] border border-[#3a3a40] rounded-lg text-[#2997ff] font-mono font-bold flex items-center justify-center text-sm shadow-inner">
            {getNoteName(selectedNote.noteNum)}
          </div>
        </div>
      </div>

      {/* Length in Ticks */}
      <div>
        <label className="text-[#9a9aa2] font-medium block mb-1">長さ (Length Ticks):</label>
        <input
          type="number"
          step="60"
          value={selectedNote.length}
          onChange={(e) => onUpdateNote('length', parseInt(e.target.value) || 480)}
          className="w-full h-10 bg-[#18181a] border border-[#3a3a40] rounded-lg px-3 text-[#f0f0f2] font-mono focus:border-[#0a84ff] focus:outline-none"
        />
      </div>

      {/* Intensity / Volume */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-[#9a9aa2] font-medium">音量強度 (Intensity):</label>
          <span className="font-mono text-[#2997ff] font-bold">{selectedNote.intensity}</span>
        </div>
        <input
          type="range"
          min="0"
          max="150"
          value={selectedNote.intensity}
          onChange={(e) => onUpdateNote('intensity', parseFloat(e.target.value))}
          className="w-full h-2 accent-[#0a84ff] bg-[#2a2a2e] rounded-lg appearance-none cursor-pointer"
        />
      </div>

      {/* Flags */}
      <div>
        <label className="text-[#9a9aa2] font-medium block mb-1">フラグ (Flags, e.g. g-5B50):</label>
        <input
          type="text"
          value={selectedNote.flags}
          onChange={(e) => onUpdateNote('flags', e.target.value)}
          placeholder="g-5B50"
          className="w-full h-10 bg-[#18181a] border border-[#3a3a40] rounded-lg px-3 text-[#f0f0f2] font-mono text-xs focus:border-[#0a84ff] focus:outline-none placeholder-[#55555c]"
        />
      </div>

      {/* Pitch Curve Parameters (PBS, PBW, PBY) */}
      <div className="border-t border-[#303034] pt-3">
        <label className="text-[#d5d5da] font-medium block mb-2 flex items-center space-x-1">
          <AudioWaveform className="w-3.5 h-3.5 text-[#0a84ff]" />
          <span>ピッチベンド曲線 (PBS / PBW / PBY)</span>
        </label>
        <div className="space-y-2">
          <div>
            <span className="text-[10px] text-[#7d7d86]">PBS (開始点 ticks;semitones):</span>
            <input
              type="text"
              value={selectedNote.pbs}
              onChange={(e) => onUpdateNote('pbs', e.target.value)}
              className="w-full h-8 bg-[#18181a] border border-[#3a3a40] rounded px-2 text-[#f0f0f2] font-mono text-[11px] focus:border-[#0a84ff] focus:outline-none"
            />
          </div>
          <div>
            <span className="text-[10px] text-[#7d7d86]">PBW (各点幅カンマ区切り):</span>
            <input
              type="text"
              value={selectedNote.pbw}
              onChange={(e) => onUpdateNote('pbw', e.target.value)}
              className="w-full h-8 bg-[#18181a] border border-[#3a3a40] rounded px-2 text-[#f0f0f2] font-mono text-[11px] focus:border-[#0a84ff] focus:outline-none"
            />
          </div>
          <div>
            <span className="text-[10px] text-[#7d7d86]">PBY (各点高さ半音単位):</span>
            <input
              type="text"
              value={selectedNote.pby}
              onChange={(e) => onUpdateNote('pby', e.target.value)}
              className="w-full h-8 bg-[#18181a] border border-[#3a3a40] rounded px-2 text-[#f0f0f2] font-mono text-[11px] focus:border-[#0a84ff] focus:outline-none"
            />
          </div>
        </div>

        {/* Mini Pitch Bend Graphical Curve Editor */}
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
      </div>
    </div>
  );
};

export default InspectorPanel;
