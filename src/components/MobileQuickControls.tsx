import React from 'react';
import { Sliders, Layers, Mic, Play, Pause, Square, Sparkles, FolderOpen, ZoomIn, ZoomOut, Maximize2 } from 'lucide-react';

interface MobileQuickControlsProps {
  isPlaying: boolean;
  onTogglePlay: () => void;
  onStop: () => void;
  zoomX: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onResetZoom: () => void;
  onAddNote: () => void;
  activeSheet: 'tracks' | 'voice' | 'inspector' | 'params' | 'project' | null;
  onOpenSheet: (sheet: 'tracks' | 'voice' | 'inspector' | 'params' | 'project') => void;
  selectedNote: unknown | null;
  tracksCount: number;
}

const baseButton = 'min-w-10 h-10 px-2 rounded-lg bg-[#2a2a2e] active:bg-[#34343a] text-[#d5d5da] border border-[#3a3a40] flex items-center justify-center transition cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff]';

export const MobileQuickControls: React.FC<MobileQuickControlsProps> = ({
  isPlaying, onTogglePlay, onStop, onZoomIn, onZoomOut, onResetZoom,
  onAddNote, activeSheet, onOpenSheet, selectedNote, tracksCount,
}) => {
  const sheetClass = (sheet: 'tracks' | 'voice' | 'inspector' | 'params' | 'project') =>
    activeSheet === sheet
      ? 'bg-[#0a84ff]/20 text-[#2997ff] border border-[#0a84ff]'
      : 'bg-[#2a2a2e] active:bg-[#34343a] text-[#f0f0f2] border border-[#3a3a40]';

  return (
    <div className="bg-[#1f1f22] border-t border-[#303034] px-2 py-1.5 flex items-center gap-2 shrink-0 shadow-lg select-none pb-safe">
      <div className="flex items-center gap-1 shrink-0">
        <button onClick={onTogglePlay} className="w-10 h-10 rounded-lg bg-[#0a84ff] active:bg-[#2997ff] text-white flex items-center justify-center transition cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff]" title={isPlaying ? '一時停止' : '再生'} aria-label={isPlaying ? '一時停止' : '再生'}>
          {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
        </button>
        <button onClick={onStop} className={baseButton} title="停止" aria-label="停止"><Square className="w-4 h-4" /></button>
      </div>
      <div className="hidden sm:flex items-center gap-1 shrink-0">
        <button onClick={onZoomOut} className={baseButton} title="Zoom Out" aria-label="Zoom Out"><ZoomOut className="w-4 h-4" /></button>
        <button onClick={onZoomIn} className={baseButton} title="Zoom In" aria-label="Zoom In"><ZoomIn className="w-4 h-4" /></button>
        <button onClick={onResetZoom} className={baseButton} title="表示をリセット" aria-label="表示をリセット"><Maximize2 className="w-4 h-4" /></button>
      </div>
      <div className="flex items-center gap-1.5 overflow-x-auto py-0.5 scrollbar-none min-w-0 flex-1">
        <button onClick={() => onOpenSheet('tracks')} className={sheetClass('tracks') + ' min-w-11 h-10 px-2 rounded-lg flex items-center justify-center gap-1 text-xs font-medium transition cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff]'} title="トラック" aria-label={'トラック ' + tracksCount + '件'}>
          <Layers className="w-4 h-4 text-[#0a84ff]" /><span className="hidden md:inline">Track</span><span className="text-[10px] bg-[#18181a] px-1 rounded-full text-[#9a9aa2] border border-[#3a3a40]">{tracksCount}</span>
        </button>
        <button onClick={() => onOpenSheet('voice')} className={sheetClass('voice') + ' min-w-11 h-10 px-2 rounded-lg flex items-center justify-center gap-1 text-xs font-medium transition cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff]'} title="音源" aria-label="音源">
          <Mic className="w-4 h-4 text-[#34c759]" /><span className="hidden md:inline">Voice</span>
        </button>
        <button onClick={() => onOpenSheet('inspector')} disabled={!selectedNote} className={sheetClass('inspector') + ' min-w-11 h-10 px-2 rounded-lg flex items-center justify-center gap-1 text-xs font-medium transition cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff] disabled:opacity-40 disabled:cursor-not-allowed'} title={selectedNote ? 'ノート設定' : 'ノートを選択してください'} aria-label="ノート設定">
          <Sliders className="w-4 h-4 text-[#ff9f0a]" /><span className="hidden md:inline">Inspector</span>
        </button>
        <button onClick={() => onOpenSheet('params')} disabled={!selectedNote} className={sheetClass('params') + ' min-w-11 h-10 px-2 rounded-lg flex items-center justify-center gap-1 text-xs font-medium transition cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff] disabled:opacity-40 disabled:cursor-not-allowed'} title={selectedNote ? 'ピッチ・パラメータ' : 'ノートを選択してください'} aria-label="ピッチ・パラメータ">
          <Sparkles className="w-4 h-4 text-[#bf5af2]" /><span className="hidden md:inline">Pitch</span>
        </button>
        <button onClick={onAddNote} className="min-w-11 h-10 px-2 rounded-lg bg-[#0a84ff]/15 text-[#2997ff] border border-[#0a84ff]/50 flex items-center justify-center gap-1 text-xs font-medium transition cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff]" title="ノートを追加" aria-label="ノートを追加">
          <span className="text-base leading-none">+</span><span className="hidden md:inline">Note</span>
        </button>
        <button onClick={() => onOpenSheet('project')} className={sheetClass('project') + ' min-w-11 h-10 px-2 rounded-lg flex items-center justify-center gap-1 text-xs font-medium transition cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff]'} title="プロジェクト" aria-label="プロジェクト">
          <FolderOpen className="w-4 h-4 text-[#d5d5da]" /><span className="hidden md:inline">Project</span>
        </button>
      </div>
    </div>
  );
};

export default MobileQuickControls;
