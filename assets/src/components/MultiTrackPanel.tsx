import React from 'react';
import { Layers, Plus, Volume2, VolumeX, Eye, EyeOff, Music, Trash2, Copy, Sliders, ChevronDown, ChevronRight, Disc, Upload } from 'lucide-react';

interface TrackEffect {
  id: string;
  type: 'compressor' | 'eq' | 'reverb';
  enabled: boolean;
  params: Record<string, number>;
}

export interface Track {
  id: string;
  name: string;
  type: 'vocal' | 'wave';
  voicebank?: string;
  notes: any[];
  volume: number; // 0.0 ~ 1.0
  pan?: number; // -1.0 ~ 1.0
  effects?: TrackEffect[];
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

const TRACK_EFFECT_PRESETS: Record<string, TrackEffect[]> = {
  vocal_clean: [
    {
      id: 'vocal_clean_1',
      type: 'eq',
      enabled: true,
      params: { frequency_hz: 350, gain_db: -1.5, q: 1.0 },
    },
    {
      id: 'vocal_clean_2',
      type: 'compressor',
      enabled: true,
      params: {
        threshold_db: -18,
        ratio: 3,
        attack_ms: 12,
        release_ms: 120,
        makeup_gain_db: 1,
      },
    },
  ],
  warm_room: [
    {
      id: 'warm_room_1',
      type: 'eq',
      enabled: true,
      params: { frequency_hz: 240, gain_db: 1.5, q: 0.8 },
    },
    {
      id: 'warm_room_2',
      type: 'reverb',
      enabled: true,
      params: { room_size: 0.42, damping: 0.55, wet: 0.16, dry: 0.9, width: 0.8 },
    },
  ],
  vocal_presence: [
    {
      id: 'vocal_presence_1',
      type: 'eq',
      enabled: true,
      params: { frequency_hz: 3200, gain_db: 2, q: 1.2 },
    },
    {
      id: 'vocal_presence_2',
      type: 'compressor',
      enabled: true,
      params: {
        threshold_db: -22,
        ratio: 4,
        attack_ms: 8,
        release_ms: 90,
        makeup_gain_db: 1.5,
      },
    },
  ],
};

const TRACK_EFFECT_PARAM_RANGES: Record<string, {
  min: number;
  max: number;
  step: number;
  label: string;
}> = {
  threshold_db: { min: -60, max: 0, step: 0.5, label: 'Threshold (dB)' },
  ratio: { min: 1, max: 20, step: 0.1, label: 'Ratio' },
  attack_ms: { min: 1, max: 200, step: 0.1, label: 'Attack (ms)' },
  release_ms: { min: 10, max: 1000, step: 1, label: 'Release (ms)' },
  makeup_gain_db: { min: -12, max: 12, step: 0.1, label: 'Makeup (dB)' },
  frequency_hz: { min: 20, max: 20000, step: 1, label: 'Frequency (Hz)' },
  gain_db: { min: -18, max: 18, step: 0.1, label: 'Gain (dB)' },
  q: { min: 0.2, max: 10, step: 0.1, label: 'Q' },
  room_size: { min: 0, max: 0.9, step: 0.01, label: 'Room size' },
  damping: { min: 0, max: 0.95, step: 0.01, label: 'Damping' },
  wet: { min: 0, max: 1, step: 0.01, label: 'Wet' },
  dry: { min: 0, max: 1, step: 0.01, label: 'Dry' },
  width: { min: 0, max: 1, step: 0.01, label: 'Width' },
};

const getTrackEffectPreset = (effects?: TrackEffect[]): string => {
  const firstId = effects?.[0]?.id || '';
  if (firstId.startsWith('vocal_clean_')) return 'vocal_clean';
  if (firstId.startsWith('warm_room_')) return 'warm_room';
  if (firstId.startsWith('vocal_presence_')) return 'vocal_presence';
  return effects?.length ? 'custom' : 'none';
};

const createTrackEffectPreset = (preset: string): TrackEffect[] =>
  (TRACK_EFFECT_PRESETS[preset] || []).map((effect) => ({
    ...effect,
    params: { ...effect.params },
  }));

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
  const [expandedFxTrackId, setExpandedFxTrackId] = React.useState<string | null>(null);

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
      <div className="vose-track-strip px-1.5 py-1.5 sm:p-2 flex gap-1.5 sm:gap-2 overflow-x-auto scrollbar-none">
        {tracks.map((t, idx) => {
          const isSelected = t.id === currentTrackId;
          const trackColor = t.color || TRACK_COLORS[idx % TRACK_COLORS.length];
          const updateEffect = (effectId: string, changes: Partial<TrackEffect>) => {
            const updatedEffects = (t.effects || []).map((effect) =>
              effect.id === effectId
                ? {
                    ...effect,
                    ...changes,
                    params: { ...effect.params, ...(changes.params || {}) },
                  }
                : effect
            );
            onUpdateTrack(t.id, { effects: updatedEffects });
          };

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

              {/* Track effect preset uses the same serialized chain as Desktop. */}
              <div
                className="rounded-md bg-[var(--vose-bg-base)]/80 border border-[var(--vose-border)] px-2 py-1.5 space-y-2"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="flex items-center gap-2">
                  <span className="text-[9px] font-semibold tracking-wide text-[var(--vose-text-secondary)] shrink-0">TRACK FX</span>
                  <select
                    value={getTrackEffectPreset(t.effects)}
                    onChange={(e) => {
                      const preset = e.target.value;
                      if (preset !== 'custom') {
                        onUpdateTrack(t.id, { effects: createTrackEffectPreset(preset) });
                      }
                    }}
                    className="min-w-0 flex-1 bg-[var(--vose-bg-base)] text-[var(--vose-text-primary)] text-[10px] border border-[var(--vose-border)] rounded px-1.5 py-1 focus:border-[#0a84ff] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff]"
                    title="選択トラックに適用するEQ / Compressor / Reverb"
                  >
                    <option value="none">Off</option>
                    <option value="vocal_clean">Vocal Clean</option>
                    <option value="warm_room">Warm Room</option>
                    <option value="vocal_presence">Vocal Presence</option>
                    {getTrackEffectPreset(t.effects) === 'custom' && (
                      <option value="custom" disabled>{'Custom (' + (t.effects?.length ?? 0) + ')'}</option>
                    )}
                  </select>
                  <button
                    type="button"
                    disabled={!t.effects?.length}
                    onClick={() => setExpandedFxTrackId(
                      expandedFxTrackId === t.id ? null : t.id
                    )}
                    className="text-[9px] px-1.5 py-1 rounded border border-[var(--vose-border)] text-[var(--vose-text-secondary)] disabled:opacity-40 hover:text-[var(--vose-text-primary)]"
                    aria-expanded={expandedFxTrackId === t.id}
                    title="各エフェクトのパラメータを編集"
                  >
                    {expandedFxTrackId === t.id ? '閉じる' : '調整'}
                  </button>
                </div>
                {expandedFxTrackId === t.id && (t.effects?.length ?? 0) > 0 && (
                  <div className="space-y-2 border-t border-[var(--vose-border)] pt-2">
                    {t.effects?.map((effect) => (
                      <div key={effect.id} className="space-y-1.5">
                        <label className="flex items-center gap-1.5 text-[9px] font-semibold text-[var(--vose-text-primary)]">
                          <input
                            type="checkbox"
                            checked={effect.enabled}
                            onChange={(e) => updateEffect(effect.id, { enabled: e.target.checked })}
                            className="accent-[#0a84ff]"
                          />
                          <span>{effect.type.toUpperCase()}</span>
                        </label>
                        {Object.entries(effect.params).map(([parameter, value]) => {
                          const range = TRACK_EFFECT_PARAM_RANGES[parameter] || {
                            min: 0, max: 1, step: 0.01, label: parameter,
                          };
                          const safeValue = Math.min(range.max, Math.max(range.min, value));
                          return (
                            <label key={parameter} className="block space-y-0.5">
                              <span className="flex items-center justify-between gap-2 text-[8px] text-[var(--vose-text-secondary)]">
                                <span>{range.label}</span>
                                <span className="font-mono">{Number(value).toFixed(2)}</span>
                              </span>
                              <input
                                type="range"
                                min={range.min}
                                max={range.max}
                                step={range.step}
                                value={safeValue}
                                onChange={(e) => updateEffect(effect.id, {
                                  params: { [parameter]: parseFloat(e.target.value) },
                                })}
                                className="w-full h-1 accent-[#0a84ff]"
                              />
                            </label>
                          );
                        })}
                      </div>
                    ))}
                  </div>
                )}
              </div>

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
                    min="-1"
                    max="1"
                    step="0.01"
                    value={Math.max(-1, Math.min(1, t.pan ?? 0))}
                    onChange={(e) => onUpdateTrack(t.id, { pan: parseFloat(e.target.value) })}
                    className="w-14 accent-[#0a84ff] h-1.5 bg-[var(--vose-bg-base)] rounded"
                    title={`Pan: ${Math.round((t.pan ?? 0) * 100)}`}
                  />
                  <input
                    type="range"
                    min="0"
                    max="1"
                    step="0.01"
                    value={Math.max(0, Math.min(1, t.volume))}
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
