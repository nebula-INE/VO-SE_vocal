import React from 'react';
import {
  Layers, Plus, Music, Volume2, Trash2, Copy, Eye, EyeOff, Upload, Check
} from 'lucide-react';
import { Track } from './MultiTrackPanel';
import BottomSheet from './BottomSheet';

interface TracksSheetProps {
  isOpen?: boolean;
  onClose?: () => void;
  tracks: Track[];
  currentTrackId: string;
  onSelectTrack: (id: string) => void;
  onAddTrack: (type: 'vocal' | 'wave') => void;
  onDuplicateTrack: (id: string) => void;
  onDeleteTrack: (id: string) => void;
  onUpdateTrack: (id: string, patch: Partial<Track>) => void;
  showGhostNotes: boolean;
  onToggleGhostNotes?: () => void;
  setShowGhostNotes?: (show: boolean) => void;
  onImportProject?: () => void;
}

export const TracksSheet: React.FC<TracksSheetProps> = ({
  isOpen = true,
  onClose = () => {},
  tracks,
  currentTrackId,
  onSelectTrack,
  onAddTrack,
  onDuplicateTrack,
  onDeleteTrack,
  onUpdateTrack,
  showGhostNotes,
  onToggleGhostNotes,
  setShowGhostNotes,
  onImportProject,
}) => {
  const handleToggleGhost = () => {
    if (onToggleGhostNotes) {
      onToggleGhostNotes();
    } else if (setShowGhostNotes) {
      setShowGhostNotes(!showGhostNotes);
    }
  };

  const content = (
    <div className="space-y-4 text-xs">
      {/* Top Toolbar in Sheet */}
      <div className="flex items-center justify-between gap-2 pb-2 border-b border-[#303034]">
        <div className="flex items-center space-x-2">
          <button
            onClick={() => onAddTrack('vocal')}
            className="h-9 px-3 bg-[#0a84ff] hover:bg-[#2997ff] active:bg-[#0071e3] text-white font-medium rounded-lg flex items-center space-x-1.5 transition cursor-pointer"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>+ ボーカルトラック</span>
          </button>
          <button
            onClick={() => onAddTrack('wave')}
            className="h-9 px-3 bg-[#2a2a2e] hover:bg-[#34343a] active:bg-[#2a2a2e]/80 text-[#f0f0f2] font-medium rounded-lg flex items-center space-x-1.5 transition cursor-pointer border border-[#3a3a40]"
          >
            <Music className="w-3.5 h-3.5 text-[#bf5af2]" />
            <span>+ WAV伴奏</span>
          </button>
        </div>

        <button
          onClick={handleToggleGhost}
          className={`h-9 px-2.5 rounded-lg border flex items-center space-x-1.5 transition cursor-pointer ${
            showGhostNotes
              ? 'bg-[#0a84ff]/20 text-[#2997ff] border-[#0a84ff]'
              : 'bg-[#18181a] text-[#9a9aa2] border-[#3a3a40]'
          }`}
          title="他トラックの透かし表示"
        >
          {showGhostNotes ? <Eye className="w-3.5 h-3.5 text-[#0a84ff]" /> : <EyeOff className="w-3.5 h-3.5 text-[#7d7d86]" />}
          <span>他トラック透視</span>
        </button>
      </div>

      {/* Track List */}
      <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
        {tracks.map((t, idx) => {
          const isSelected = t.id === currentTrackId;
          return (
            <div
              key={t.id}
              onClick={() => onSelectTrack(t.id)}
              className={`p-3 rounded-xl border transition cursor-pointer flex flex-col space-y-2.5 ${
                isSelected
                  ? 'bg-[#2a2a2e] border-[#0a84ff] shadow-md ring-1 ring-[#0a84ff]/40'
                  : 'bg-[#18181a] hover:bg-[#232327] border-[#303034] text-[#d5d5da]'
              }`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2">
                  <div className={`w-3 h-3 rounded-full ${isSelected ? 'bg-[#0a84ff] shadow-sm shadow-[#0a84ff]' : 'bg-[#3a3a40]'}`} />
                  <input
                    type="text"
                    value={t.name}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => onUpdateTrack(t.id, { name: e.target.value })}
                    className="font-bold text-[#f0f0f2] bg-transparent border-b border-transparent focus:border-[#0a84ff] focus:outline-none max-w-[150px]"
                  />
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#18181a] text-[#9a9aa2] border border-[#3a3a40] font-mono">
                    {t.type}
                  </span>
                </div>

                <div className="flex items-center space-x-1" onClick={(e) => e.stopPropagation()}>
                  <button
                    onClick={() => onDuplicateTrack(t.id)}
                    className="p-1.5 rounded-lg hover:bg-[#34343a] text-[#9a9aa2] hover:text-[#f0f0f2] transition cursor-pointer"
                    title="複製"
                  >
                    <Copy className="w-3.5 h-3.5" />
                  </button>
                  {tracks.length > 1 && (
                    <button
                      onClick={() => onDeleteTrack(t.id)}
                      className="p-1.5 rounded-lg hover:bg-[#ff453a]/20 text-[#ff453a] hover:text-[#ff453a] transition cursor-pointer"
                      title="削除"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
              </div>

              {/* Mute, Solo & Volume */}
              <div className="flex items-center space-x-3 pt-1 border-t border-[#303034]" onClick={(e) => e.stopPropagation()}>
                <div className="flex space-x-1">
                  <button
                    onClick={() => onUpdateTrack(t.id, { isMuted: !t.isMuted })}
                    className={`min-w-[28px] h-6 px-1.5 text-[10px] font-mono font-bold rounded border transition cursor-pointer ${
                      t.isMuted
                        ? 'bg-[#ff453a]/20 text-[#ff453a] border-[#ff453a]/60'
                        : 'bg-[#18181a] text-[#9a9aa2] border-[#3a3a40] hover:text-[#f0f0f2]'
                    }`}
                  >
                    M
                  </button>
                  <button
                    onClick={() => onUpdateTrack(t.id, { isSolo: !t.isSolo })}
                    className={`min-w-[28px] h-6 px-1.5 text-[10px] font-mono font-bold rounded border transition cursor-pointer ${
                      t.isSolo
                        ? 'bg-[#ff9f0a]/20 text-[#ff9f0a] border-[#ff9f0a]/60'
                        : 'bg-[#18181a] text-[#9a9aa2] border-[#3a3a40] hover:text-[#f0f0f2]'
                    }`}
                  >
                    S
                  </button>
                </div>

                <div className="flex-1 flex items-center space-x-2">
                  <Volume2 className="w-3.5 h-3.5 text-[#9a9aa2]" />
                  <input
                    type="range"
                    min="0"
                    max="1.2"
                    step="0.05"
                    value={t.volume}
                    onChange={(e) => onUpdateTrack(t.id, { volume: parseFloat(e.target.value) })}
                    className="flex-1 accent-[#0a84ff] h-1.5 bg-[#18181a] rounded appearance-none cursor-pointer"
                  />
                  <span className="text-[10px] font-mono text-[#9a9aa2] w-8 text-right font-medium">
                    {Math.round(t.volume * 100)}%
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {onImportProject && (
        <div className="pt-2 border-t border-[#303034]">
          <button
            onClick={onImportProject}
            className="w-full h-10 bg-[#2a2a2e] hover:bg-[#34343a] text-[#2997ff] border border-[#3a3a40] rounded-xl flex items-center justify-center space-x-2 transition font-medium cursor-pointer"
          >
            <Upload className="w-4 h-4 text-[#0a84ff]" />
            <span>UST / MIDI プロジェクトをトラックへ読み込む</span>
          </button>
        </div>
      )}
    </div>
  );

  return (
    <BottomSheet
      isOpen={isOpen}
      onClose={onClose}
      title="トラック管理 (Tracks)"
      subtitle={`${tracks.length} トラック`}
      icon={<Layers className="w-5 h-5" />}
    >
      {content}
    </BottomSheet>
  );
};

export default TracksSheet;
