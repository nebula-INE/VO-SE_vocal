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
    <div className="bg-[var(--vose-bg-panel)] border-b border-[var(--vose-border)] flex flex-col shrink-0 select-none transition-all">
      {/* Panel Header */}
      <div className="h-10 px-3 bg-[var(--vose-bg-base)] border-b border-[var(--vose-border)] flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center space-x-2">
          {onToggleCollapse && (
            <button
              onClick={onToggleCollapse}
              className="min-w-7 h-7 p-1 rounded text-[var(--vose-text-secondary)] hover:text-[var(--vose-text-primary)] hover:bg-[var(--vose-bg-elevated)] transition cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff] focus-visible:ring-offset-1 focus-visible:ring-offset-[#18181a]"
              title={isCollapsed ? 'トラックリストを展開' : 'トラックリストを折りたたむ'}
              aria-label={isCollapsed ? 'トラックリストを展開' : 'トラックリストを折りたたむ'}
              aria-expanded={!isCollapsed}
            >
              {isCollapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            </button>
          )}
          <Layers className="w-4 h-4 text-[#0a84ff]" />
          <span className="font-semibold text-xs text-[var(--vose-text-primary)]">Track List</span>
          <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-[#0a84ff]/20 text-[#2997ff] border border-[#0a84ff]/40">
            {tracks.length}
          </span>
          {isCollapsed && currentTrack && (
            <span className="text-xs text-[var(--vose-text-secondary)] truncate max-w-[120px] font-medium ml-1">
              Active: <span className="text-[#2997ff] font-bold">{currentTrack.name}</span>
            </span>
          )}
        </div>

        <div className="hidden sm:flex items-center space-x-1.5 sm:space-x-2">
          {/* Ghost Notes Toggle */}
          <button
            onClick={() => setShowGhostNotes(!showGhostNotes)}
            className={`text-[11px] px-2 py-1 rounded-md transition flex items-center space-x-1 border ${
              showGhostNotes
                ? 'bg-[#0a84ff]/20 text-[#2997ff] border-[#0a84ff]/60'
                : 'bg-[var(--vose-bg-elevated)] text-[var(--vose-text-secondary)] hover:text-[var(--vose-text-primary)] border-[var(--vose-border)]'
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
              className="text-[11px] bg-[var(--vose-bg-elevated)] hover:bg-[var(--vose-bg-hover)] text-[#2997ff] hover:text-[#5ac8fa] font-medium px-2 py-1 rounded-md transition flex items-center space-x-1 border border-[var(--vose-border)]"
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
            className="text-[11px] bg-[var(--vose-bg-elevated)] hover:bg-[var(--vose-bg-hover)] text-[var(--vose-text-primary)] font-medium px-2 py-1 rounded-md transition flex items-center space-x-1 border border-[var(--vose-border)] cursor-pointer"
          >
            <Music className="w-3.5 h-3.5 text-[#bf5af2]" />
            <span className="hidden sm:inline">+ WAV</span>
          </button>
        </div>
      </div>

      {/* Track List Strip (Hidden when collapsed) */}
      {!isCollapsed && (
      <div className="px-1.5 py-1.5 sm:p-2 flex gap-1.5 sm:gap-2 overflow-x-auto scrollbar-none">
        {tracks.map((t, idx) => {
          const isSelected = t.id === currentTrackId;
          const trackColor = t.color || TRACK_COLORS[idx % TRACK_COLORS.length];

          return (
            <div
              key={t.id}
              role="button"
              tabIndex={0}
              aria-pressed={isSelected}
              onClick={() => onSelectTrack(t.id)}
              onKeyDown={(e) => {
                if (e.target !== e.currentTarget) return;
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onSelectTrack(t.id);
                }
              }}
              className={`min-w-[176px] sm:min-w-[190px] md:min-w-[210px] p-2 sm:p-2.5 rounded-lg border transition-[background-color,border-color,box-shadow] duration-150 cursor-pointer flex flex-col justify-between gap-1.5 sm:gap-2 relative group ${
                isSelected
                  ? 'bg-[var(--vose-bg-elevated)] border-[#0a84ff] shadow-md shadow-[#0a84ff]/20 ring-1 ring-[#0a84ff]/40'
                  : 'bg-[var(--vose-bg-base)] hover:bg-[#232327] border-[var(--vose-border)] text-[var(--vose-text-secondary)]'
              }`}
            >
              {/* Color Stripe Header */}
              <div className="flex items-start justify-between gap-2 min-w-0">
                <div className="flex min-w-0 items-center gap-2">
                  <div
                    className="w-2.5 h-2.5 shrink-0 rounded-full shadow-sm ring-1 ring-white/10"
                    style={{ backgroundColor: trackColor }}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 items-center gap-1.5">
                  <input
                    type="text"
                    value={t.name}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => onUpdateTrack(t.id, { name: e.target.value })}
                    className="bg-transparent font-bold text-xs text-[var(--vose-text-primary)] border-b border-transparent hover:border-[var(--vose-border)] focus:border-[#0a84ff] focus:outline-none w-full min-w-0 max-w-[8rem] truncate"
                  />
                  <span className={`shrink-0 text-[9px] font-mono px-1.5 py-0.5 rounded border ${
                    t.type === 'vocal'
                      ? 'bg-[#0a84ff]/10 text-[#7fc8ff] border-[#0a84ff]/30'
                      : 'bg-[#bf5af2]/10 text-[#d6a8ff] border-[#bf5af2]/30'
                  }`}>
                    {t.type === 'vocal' ? 'VOCAL' : 'WAV'}
                  </span>
                    </div>
                    <div className="mt-1 text-[9px] text-[#7d7d86] truncate">
                      {t.type === 'vocal' ? `${t.notes.length}音` : 'WAVトラック'}
                    </div>
                  </div>
                </div>

                <div className="flex shrink-0 items-center gap-0.5">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onDuplicateTrack(t.id);
                    }}
                    className="min-w-7 h-7 p-1 hover:bg-[var(--vose-bg-elevated)] rounded-md text-[var(--vose-text-secondary)] hover:text-[#2997ff] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff] focus-visible:ring-offset-1 focus-visible:ring-offset-[#18181a] transition"
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
                      className="min-w-7 h-7 p-1 hover:bg-[var(--vose-bg-elevated)] rounded-md text-[var(--vose-text-secondary)] hover:text-[#ff453a] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff] focus-visible:ring-offset-1 focus-visible:ring-[#18181a] transition"
                      title="トラック削除"
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  )}
                </div>
              </div>

              {/* Voicebank / Type details */}
              {t.type === 'vocal' ? (
                <div className="rounded-md bg-[var(--vose-bg-base)]/80 border border-[var(--vose-border)] px-2 py-1.5">
                  <div className="text-[8px] sm:text-[9px] uppercase tracking-wide text-[#6f6f78] mb-1">音源</div>
                  <div className="text-[10px] text-[var(--vose-text-secondary)] flex items-center justify-between gap-2">
                  <select
                    value={t.voicebank || ''}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => onUpdateTrack(t.id, { voicebank: e.target.value })}
                    className="min-w-0 w-full bg-[var(--vose-bg-base)] text-[var(--vose-text-primary)] text-[10px] border border-[var(--vose-border)] rounded px-1.5 py-1 max-w-[150px] focus:border-[#0a84ff] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff] focus-visible:ring-offset-1 focus-visible:ring-offset-[#18181a]"
                  >
                    <option value="">(既定音源)</option>
                    {customVoicebanks.map((vb) => (
                      <option key={vb.name} value={vb.name}>
                        {vb.name}
                      </option>
                    ))}
                  </select>
                </div>
                </div>
              ) : (
                <div className="rounded-md bg-[var(--vose-bg-base)]/80 border border-[var(--vose-border)] px-2 py-2 text-[10px] text-[#bf5af2] flex items-center gap-1.5">
                  <Disc className="w-3 h-3 text-[#bf5af2]" />
                  <span className="truncate">オーディオ伴奏トラック</span>
                </div>
              )}

              {/* Volume Slider & Mute / Solo Controls */}
              <div className="flex items-center justify-between pt-1.5 sm:pt-2 border-t border-[var(--vose-border)]" onClick={(e) => e.stopPropagation()}>
                <div className="flex items-center gap-1.5 min-w-0 flex-1 mr-2">
                  <button
                    onClick={() => onUpdateTrack(t.id, { isMuted: !t.isMuted })}
                    className={`min-w-7 h-7 px-1.5 text-[9px] font-mono font-bold rounded border transition cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff] focus-visible:ring-offset-1 focus-visible:ring-offset-[#1f1f22] ${
                      t.isMuted ? 'bg-[#ff453a]/20 text-[#ff453a] border-[#ff453a]/60' : 'bg-[var(--vose-bg-base)] text-[var(--vose-text-secondary)] border-[var(--vose-border)] hover:text-[var(--vose-text-primary)]'
                    }`}
                  >
                    M
                  </button>
                  <button
                    onClick={() => onUpdateTrack(t.id, { isSolo: !t.isSolo })}
                    className={`min-w-7 h-7 px-1.5 text-[9px] font-mono font-bold rounded border transition cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff] focus-visible:ring-offset-1 focus-visible:ring-offset-[#1f1f22] ${
                      t.isSolo ? 'bg-[#ff9f0a]/20 text-[#ff9f0a] border-[#ff9f0a]/60' : 'bg-[var(--vose-bg-base)] text-[var(--vose-text-secondary)] border-[var(--vose-border)] hover:text-[var(--vose-text-primary)]'
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
                    className="w-full accent-[#0a84ff] h-1.5 bg-[var(--vose-bg-base)] rounded"
                    title={`音量: ${Math.round(t.volume * 100)}%`}
                  />
                </div>
                <span className="text-[10px] font-mono text-[var(--vose-text-secondary)] font-semibold w-7 text-right">
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
