import React from 'react';
import { Sparkles, AudioWaveform, Sliders, Eye, EyeOff } from 'lucide-react';
import BottomSheet from './BottomSheet';
import PitchCurveMiniEditor from './PitchCurveMiniEditor';
import { Note } from './InspectorPanel';

interface PitchParamsSheetProps {
  isOpen?: boolean;
  onClose?: () => void;
  selectedNote: Note | null;
  onUpdateNote?: (field: keyof Note, value: any) => void;
  onUpdatePitch?: (pbs: string, pbw: string, pby: string) => void;
  tempo: number;
  showGhostNotes?: boolean;
  onToggleGhostNotes?: () => void;
  getNoteName?: (midi: number) => string;
}

export const PitchParamsSheet: React.FC<PitchParamsSheetProps> = ({
  isOpen = true,
  onClose = () => {},
  selectedNote,
  onUpdateNote,
  onUpdatePitch,
  tempo,
  showGhostNotes = false,
  onToggleGhostNotes,
  getNoteName = (midi: number) => {
    const noteNames = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
    const oct = Math.floor(midi / 12) - 1;
    return `${noteNames[midi % 12]}${oct}`;
  },
}) => {
  const handleUpdateField = (field: keyof Note, value: any) => {
    if (onUpdateNote) {
      onUpdateNote(field, value);
    } else if (onUpdatePitch && selectedNote) {
      const pbs = field === 'pbs' ? value : selectedNote.pbs;
      const pbw = field === 'pbw' ? value : selectedNote.pbw;
      const pby = field === 'pby' ? value : selectedNote.pby;
      onUpdatePitch(pbs, pbw, pby);
    }
  };

  const content = (
    <div className="space-y-4 text-xs">
      {/* Global View Options */}
      {onToggleGhostNotes && (
        <div className="p-3 bg-[#18181a] border border-[#3a3a40] rounded-xl flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Eye className="w-4 h-4 text-[#0a84ff]" />
            <div>
              <span className="font-bold text-[#f0f0f2]">他トラック透視 (Ghost Notes)</span>
              <span className="text-[10px] text-[#9a9aa2] block">別トラックの音高・ピッチカーブを背景に半透明表示</span>
            </div>
          </div>

          <button
            onClick={onToggleGhostNotes}
            className={`min-w-[44px] h-9 px-3 rounded-lg font-medium transition cursor-pointer border ${
              showGhostNotes
                ? 'bg-[#0a84ff]/20 text-[#2997ff] border-[#0a84ff]'
                : 'bg-[#2a2a2e] text-[#9a9aa2] border-[#3a3a40] hover:text-[#f0f0f2]'
            }`}
          >
            {showGhostNotes ? 'ON' : 'OFF'}
          </button>
        </div>
      )}

      {/* Selected Note Pitch Details */}
      {selectedNote ? (
        <div className="space-y-3">
          <div className="flex items-center justify-between border-b border-[#303034] pb-2">
            <h4 className="font-bold text-[#f0f0f2] flex items-center space-x-1.5">
              <AudioWaveform className="w-4 h-4 text-[#0a84ff]" />
              <span>選択中ノートのピッチベンド: {getNoteName(selectedNote.noteNum)} ({selectedNote.lyric})</span>
            </h4>
          </div>

          {/* Graphical Mini Curve Editor */}
          <div className="bg-[#18181a] p-2.5 rounded-xl border border-[#3a3a40]">
            <span className="text-[10px] text-[#9a9aa2] block mb-1">直感グラフィカル調整:</span>
            <PitchCurveMiniEditor
              note={selectedNote}
              tempo={tempo}
              onUpdate={(pbs, pbw, pby) => {
                if (onUpdatePitch) {
                  onUpdatePitch(pbs, pbw, pby);
                } else {
                  handleUpdateField('pbs', pbs);
                  handleUpdateField('pbw', pbw);
                  handleUpdateField('pby', pby);
                }
              }}
            />
          </div>

          {/* Text Parameters */}
          <div className="space-y-2">
            <div>
              <span className="text-[10px] text-[#9a9aa2]">PBS (Pitch Bend Start - ticks;semitones):</span>
              <input
                type="text"
                value={selectedNote.pbs}
                onChange={(e) => handleUpdateField('pbs', e.target.value)}
                className="w-full h-9 bg-[#18181a] border border-[#3a3a40] rounded-lg px-2.5 text-[#f0f0f2] font-mono text-xs focus:border-[#0a84ff] focus:outline-none"
              />
            </div>

            <div>
              <span className="text-[10px] text-[#9a9aa2]">PBW (Pitch Bend Width - points duration):</span>
              <input
                type="text"
                value={selectedNote.pbw}
                onChange={(e) => handleUpdateField('pbw', e.target.value)}
                className="w-full h-9 bg-[#18181a] border border-[#3a3a40] rounded-lg px-2.5 text-[#f0f0f2] font-mono text-xs focus:border-[#0a84ff] focus:outline-none"
              />
            </div>

            <div>
              <span className="text-[10px] text-[#9a9aa2]">PBY (Pitch Bend Height - semitone offsets):</span>
              <input
                type="text"
                value={selectedNote.pby}
                onChange={(e) => handleUpdateField('pby', e.target.value)}
                className="w-full h-9 bg-[#18181a] border border-[#3a3a40] rounded-lg px-2.5 text-[#f0f0f2] font-mono text-xs focus:border-[#0a84ff] focus:outline-none"
              />
            </div>
          </div>
        </div>
      ) : (
        <div className="p-6 text-center text-[#7d7d86] bg-[#18181a] border border-[#3a3a40] rounded-xl space-y-1">
          <p className="text-xs">ノートをタップして選択すると、</p>
          <p className="text-xs">ピッチベンド曲線（PBS/PBW/PBY）の詳細を調整できます</p>
        </div>
      )}
    </div>
  );

  return (
    <BottomSheet
      isOpen={isOpen}
      onClose={onClose}
      title="ピッチパラメータ (Pitch Curve)"
      subtitle={selectedNote ? `${getNoteName(selectedNote.noteNum)} (${selectedNote.lyric})` : 'ノート未選択'}
      icon={<AudioWaveform className="w-5 h-5" />}
    >
      {content}
    </BottomSheet>
  );
};

export default PitchParamsSheet;
