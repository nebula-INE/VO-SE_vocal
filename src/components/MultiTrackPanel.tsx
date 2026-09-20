import React from 'react';
import { Layers, Plus, Volume2, VolumeX, Eye, EyeOff, Music, Trash2, Copy, Sliders, ChevronDown, ChevronRight, Disc, Upload } from 'lucide-react';

export interface Track {
  id: string;
  name: string;
  type: 'vocal' | 'wave';
  voicebank?: string;
  notes: any[];
  volume: number; // 0.0 ~ 1.2
  pan?: number; // -1.0 ~ 1.0
  isMuted: boolean;
  isSolo: boolean;
  color?: string;
  audioUrl?: string;
}

export interface MultiTrackPanelProps {
  tracks: Track[];
  currentTrackId: string;
  onSelectTrack: (trackId: string) => void;
  onAddTrack: (type: 'vocal' | 'wave') => void;
  onDuplicateTrack: (trackId: string) => void;
  onDeleteTrack: (trackId: string) => void;
  onUpdateTrack: (trackId: string, patch: Partial<Track>) => void;
  showGhostNotes: boolean;
  setShowGhostNotes: (show: boolean) => void;
  customVoicebanks: { name: string; aliasCount: number; hasVcv: boolean }[];
  onImportProject?: () => void;
  isCollapsed?: boolean;
  onToggleCollapse?: () => void;
}

const TRACK_COLORS = [
  '#0a84ff', // Electric Blue (Primary)
  '#ff9f0a', // Vivid Orange
  '#30d158', // Neon Green
  '#bf5af2', // Vivid Purple
  '#ff375f', // Hot Pink
  '#64d2ff', // Cyan Sky
];

export const MultiTrackPanel: React.FC<MultiTrackPanelProps> = ({
  tracks,
  currentTrackId,
  onSelectTrack,
  onAddTrack,
  onDuplicateTrack,
  onDeleteTrack,
  onUpdateTrack,
  showGhostNotes,
  setShowGhostNotes,
  customVoicebanks,
  onImportProject,
  isCollapsed = false,
  onToggleCollapse,
}) => {
  const currentTrack = tracks.find((t) => t.id === currentTrackId) || tracks[0];

  return (
    <div className="bg-[#1f1f22] border-b border-[#303034] flex flex-col shrink-0 select-none transition-all">
      {/* Panel Header */}
      <div className="h-10 px-3 bg-[#18181a] border-b border-[#303034] flex items-center justify-between">
        <div className="flex items-center space-x-2">
          {onToggleCollapse && (
            <button
              onClick={onToggleCollapse}
              className="p-1 rounded text-[#9a9aa2] hover:text-[#f0f0f2] hover:bg-[#2a2a2e] transition cursor-pointer"
              title={isCollapsed ? 'トラックリストを展開' : 'トラックリストを折りたたむ'}
            >
              {isCollapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            </button>
          )}
          <Layers className="w-4 h-4 text-[#0a84ff]" />
          <span className="font-semibold text-xs text-[#f0f0f2]">Track List</span>
          <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-[#0a84ff]/20 text-[#2997ff] border border-[#0a84ff]/40">
            {tracks.length}
          </span>
          {isCollapsed && currentTrack && (
            <span className="text-xs text-[#9a9aa2] truncate max-w-[120px] font-medium ml-1">
              Active: <span className="text-[#2997ff] font-bold">{currentTrack.name}</span>
            </span>
          )}
        </div>

        <div className="flex items-center space-x-1.5 sm:space-x-2">
          {/* Ghost Notes Toggle */}
          <button
            onClick={() => setShowGhostNotes(!showGhostNotes)}
            className={`text-[11px] px-2 py-1 rounded-md transition flex items-center space-x-1 border ${
              showGhostNotes
                ? 'bg-[#0a84ff]/20 text-[#2997ff] border-[#0a84ff]/60'
                : 'bg-[#2a2a2e] text-[#9a9aa2] hover:text-[#f0f0f2] border-[#3a3a40]'
            }`}
            title="ピアノロール上に別トラックのノート・ピッチを透かし(ゴースト)表示"
          >
            {showGhostNotes ? <Eye className="w-3.5 h-3.5 text-[#0a84ff]" /> : <EyeOff className="w-3.5 h-3.5 text-[#7d7d86]" />}
            <span className="hidden sm:inline">Ghost</span>
          </button>

          <div className="h-4 w-px bg-[#303034] hidden sm:block" />

          {/* Import UST/Project into Track */}
          {onImportProject && (
            <button
              onClick={onImportProject}
              className="text-[11px] bg-[#2a2a2e] hover:bg-[#34343a] text-[#2997ff] hover:text-[#5ac8fa] font-medium px-2 py-1 rounded-md transition flex items-center space-x-1 border border-[#3a3a40]"
              title="UST / VSQX / SVP / MIDI ファイルを選択して読み込み"
            >
              <Upload className="w-3.5 h-3.5 text-[#0a84ff]" />
              <span className="hidden md:inline">UST/MIDI読込</span>
            </button>
          )}

          {/* Add Vocal Track */}
          <button
            onClick={() => onAddTrack('vocal')}
            className="text-[11px] bg-[#0a84ff] hover:bg-[#2997ff] text-white font-medium px-2 py-1 rounded-md transition flex items-center space-x-1 border border-[#0a84ff] shadow-sm cursor-pointer"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>+ Vocal</span>
          </button>

          {/* Add Audio Track */}
          <button
            onClick={() => onAddTrack('wave')}
            className="text-[11px] bg-[#2a2a2e] hover:bg-[#34343a] text-[#f0f0f2] font-medium px-2 py-1 rounded-md transition flex items-center space-x-1 border border-[#3a3a40] cursor-pointer"
          >
            <Music className="w-3.5 h-3.5 text-[#bf5af2]" />
            <span className="hidden sm:inline">+ WAV</span>
          </button>
        </div>
      </div>

      {/* Track List Strip (Hidden when collapsed) */}
      {!isCollapsed && (
      <div className="p-2 flex space-x-2 overflow-x-auto">
        {tracks.map((t, idx) => {
          const isSelected = t.id === currentTrackId;
          const trackColor = t.color || TRACK_COLORS[idx % TRACK_COLORS.length];

          return (
            <div
              key={t.id}
              onClick={() => onSelectTrack(t.id)}
              className={`min-w-[210px] p-2.5 rounded-lg border transition cursor-pointer flex flex-col justify-between space-y-2 relative group ${
                isSelected
                  ? 'bg-[#2a2a2e] border-[#0a84ff] shadow-md shadow-[#0a84ff]/20 ring-1 ring-[#0a84ff]/40'
                  : 'bg-[#18181a] hover:bg-[#232327] border-[#303034] text-[#9a9aa2]'
              }`}
            >
              {/* Color Stripe Header */}
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2">
                  <div className="w-2.5 h-2.5 rounded-full shadow-sm" style={{ backgroundColor: trackColor }} />
                  <input
                    type="text"
                    value={t.name}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => onUpdateTrack(t.id, { name: e.target.value })}
                    className="bg-transparent font-bold text-xs text-[#f0f0f2] border-b border-transparent hover:border-[#3a3a40] focus:border-[#0a84ff] focus:outline-none w-24 truncate"
                  />
                  <span className="text-[9px] font-mono px-1 rounded bg-[#18181a] text-[#9a9aa2] border border-[#3a3a40]">
                    {t.type === 'vocal' ? `${t.notes.length}音` : 'WAV'}
                  </span>
                </div>

                <div className="flex items-center space-x-1">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onDuplicateTrack(t.id);
                    }}
                    className="p-1 hover:bg-[#2a2a2e] rounded text-[#9a9aa2] hover:text-[#2997ff]"
                    title="トラック複製"
                  >
                    <Copy className="w-3 h-3" />
                  </button>

                  {tracks.length > 1 && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onDeleteTrack(t.id);
                      }}
                      className="p-1 hover:bg-[#2a2a2e] rounded text-[#9a9aa2] hover:text-[#ff453a]"
                      title="トラック削除"
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  )}
                </div>
              </div>

              {/* Voicebank / Type details */}
              {t.type === 'vocal' ? (
                <div className="text-[10px] text-[#9a9aa2] flex items-center justify-between">
                  <span>音源:</span>
                  <select
                    value={t.voicebank || ''}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => onUpdateTrack(t.id, { voicebank: e.target.value })}
                    className="bg-[#18181a] text-[#f0f0f2] text-[10px] border border-[#3a3a40] rounded px-1 py-0.5 max-w-[120px] focus:border-[#0a84ff] focus:outline-none"
                  >
                    <option value="">(既定音源)</option>
                    {customVoicebanks.map((vb) => (
                      <option key={vb.name} value={vb.name}>
                        {vb.name}
                      </option>
                    ))}
                  </select>
                </div>
              ) : (
                <div className="text-[10px] text-[#bf5af2] flex items-center space-x-1">
                  <Disc className="w-3 h-3 text-[#bf5af2]" />
                  <span className="truncate">オーディオ伴奏トラック</span>
                </div>
              )}

              {/* Volume Slider & Mute / Solo Controls */}
              <div className="flex items-center justify-between pt-1 border-t border-[#303034]" onClick={(e) => e.stopPropagation()}>
                <div className="flex items-center space-x-1.5 flex-1 mr-2">
                  <button
                    onClick={() => onUpdateTrack(t.id, { isMuted: !t.isMuted })}
                    className={`px-1.5 py-0.5 text-[9px] font-mono font-bold rounded border transition ${
                      t.isMuted ? 'bg-[#ff453a]/20 text-[#ff453a] border-[#ff453a]/60' : 'bg-[#18181a] text-[#9a9aa2] border-[#3a3a40] hover:text-[#f0f0f2]'
                    }`}
                  >
                    M
                  </button>
                  <button
                    onClick={() => onUpdateTrack(t.id, { isSolo: !t.isSolo })}
                    className={`px-1.5 py-0.5 text-[9px] font-mono font-bold rounded border transition ${
                      t.isSolo ? 'bg-[#ff9f0a]/20 text-[#ff9f0a] border-[#ff9f0a]/60' : 'bg-[#18181a] text-[#9a9aa2] border-[#3a3a40] hover:text-[#f0f0f2]'
                    }`}
                  >
                    S
                  </button>
                  <input
                    type="range"
                    min="0"
                    max="1.2"
                    step="0.05"
                    value={t.volume}
                    onChange={(e) => onUpdateTrack(t.id, { volume: parseFloat(e.target.value) })}
                    className="w-full accent-[#0a84ff] h-1.5 bg-[#18181a] rounded"
                    title={`音量: ${Math.round(t.volume * 100)}%`}
                  />
                </div>
                <span className="text-[10px] font-mono text-[#9a9aa2] font-semibold w-7 text-right">
                  {Math.round(t.volume * 100)}%
                </span>
              </div>
            </div>
          );
        })}
      </div>
      )}
    </div>
  );
};

export default MultiTrackPanel;
