import React from 'react';
import {
  Sliders, Layers, Volume2, Mic, Settings, Play, Pause, Square,
  RotateCcw, Sparkles, FolderOpen, ZoomIn, ZoomOut, Maximize2
} from 'lucide-react';

interface MobileQuickControlsProps {
  onOpenTracks: () => void;
  onOpenVoice: () => void;
  onOpenInspector: () => void;
  onOpenPitchParams: () => void;
  onOpenProject: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onResetZoom: () => void;
  activeSheet: string | null;
  trackCount: number;
  selectedVoicebank: string;
  hasSelectedNote: boolean;
}

export const MobileQuickControls: React.FC<MobileQuickControlsProps> = ({
  onOpenTracks,
  onOpenVoice,
  onOpenInspector,
  onOpenPitchParams,
  onOpenProject,
  onZoomIn,
  onZoomOut,
  onResetZoom,
  activeSheet,
  trackCount,
  selectedVoicebank,
  hasSelectedNote,
}) => {
  return (
    <div className="bg-[#1f1f22] border-t border-[#303034] px-2 py-1.5 flex items-center justify-between shrink-0 shadow-lg select-none pb-safe">
      {/* Zoom / View Tools */}
      <div className="flex items-center space-x-1">
        <button
          onClick={onZoomOut}
          className="min-w-[40px] h-10 px-2 rounded-lg bg-[#2a2a2e] active:bg-[#34343a] text-[#d5d5da] border border-[#3a3a40] flex items-center justify-center transition cursor-pointer"
          title="Zoom Out"
        >
          <ZoomOut className="w-4 h-4" />
        </button>
        <button
          onClick={onZoomIn}
          className="min-w-[40px] h-10 px-2 rounded-lg bg-[#2a2a2e] active:bg-[#34343a] text-[#d5d5da] border border-[#3a3a40] flex items-center justify-center transition cursor-pointer"
          title="Zoom In"
        >
          <ZoomIn className="w-4 h-4" />
        </button>
        <button
          onClick={onResetZoom}
          className="min-w-[40px] h-10 px-2 rounded-lg bg-[#2a2a2e] active:bg-[#34343a] text-[#d5d5da] border border-[#3a3a40] flex items-center justify-center transition cursor-pointer"
          title="Fit to Content"
        >
          <Maximize2 className="w-4 h-4" />
        </button>
      </div>

      {/* Sheet Buttons (Track, Voice, Inspector, Pitch, Project) */}
      <div className="flex items-center space-x-1.5 overflow-x-auto py-0.5 scrollbar-none">
        {/* Tracks */}
        <button
          onClick={onOpenTracks}
          className={`min-w-[44px] h-10 px-2.5 rounded-lg flex items-center justify-center space-x-1 text-xs font-medium transition cursor-pointer ${
            activeSheet === 'tracks'
              ? 'bg-[#0a84ff]/20 text-[#2997ff] border border-[#0a84ff]'
              : 'bg-[#2a2a2e] active:bg-[#34343a] text-[#f0f0f2] border border-[#3a3a40]'
          }`}
        >
          <Layers className="w-4 h-4 text-[#0a84ff] shrink-0" />
          <span className="hidden xs:inline">Track</span>
          <span className="text-[10px] bg-[#18181a] px-1 rounded-full text-[#9a9aa2] border border-[#3a3a40]">{trackCount}</span>
        </button>

        {/* Voice */}
        <button
          onClick={onOpenVoice}
          className={`min-w-[44px] h-10 px-2.5 rounded-lg flex items-center justify-center space-x-1 text-xs font-medium transition cursor-pointer ${
            activeSheet === 'voice'
              ? 'bg-[#0a84ff]/20 text-[#2997ff] border border-[#0a84ff]'
              : 'bg-[#2a2a2e] active:bg-[#34343a] text-[#f0f0f2] border border-[#3a3a40]'
          }`}
        >
          <Mic className="w-4 h-4 text-[#34c759] shrink-0" />
          <span className="hidden xs:inline">Voice</span>
        </button>

        {/* Inspector (Note / Track Property) */}
        <button
          onClick={onOpenInspector}
          className={`min-w-[44px] h-10 px-2.5 rounded-lg flex items-center justify-center space-x-1 text-xs font-medium transition cursor-pointer relative ${
            activeSheet === 'inspector'
              ? 'bg-[#0a84ff]/20 text-[#2997ff] border border-[#0a84ff]'
              : 'bg-[#2a2a2e] active:bg-[#34343a] text-[#f0f0f2] border border-[#3a3a40]'
          }`}
        >
          <Sliders className="w-4 h-4 text-[#ff9f0a] shrink-0" />
          <span className="hidden xs:inline">Inspector</span>
          {hasSelectedNote && (
            <span className="w-2 h-2 rounded-full bg-[#ff9f0a] absolute top-1 right-1 shadow-sm" />
          )}
        </button>

        {/* Pitch / Parameters */}
        <button
          onClick={onOpenPitchParams}
          className={`min-w-[44px] h-10 px-2.5 rounded-lg flex items-center justify-center space-x-1 text-xs font-medium transition cursor-pointer ${
            activeSheet === 'params'
              ? 'bg-[#0a84ff]/20 text-[#2997ff] border border-[#0a84ff]'
              : 'bg-[#2a2a2e] active:bg-[#34343a] text-[#f0f0f2] border border-[#3a3a40]'
          }`}
        >
          <Sparkles className="w-4 h-4 text-[#bf5af2] shrink-0" />
          <span className="hidden xs:inline">Pitch</span>
        </button>

        {/* Project / Menu */}
        <button
          onClick={onOpenProject}
          className={`min-w-[44px] h-10 px-2.5 rounded-lg flex items-center justify-center space-x-1 text-xs font-medium transition cursor-pointer ${
            activeSheet === 'project'
              ? 'bg-[#0a84ff]/20 text-[#2997ff] border border-[#0a84ff]'
              : 'bg-[#2a2a2e] active:bg-[#34343a] text-[#f0f0f2] border border-[#3a3a40]'
          }`}
        >
          <FolderOpen className="w-4 h-4 text-[#d5d5da] shrink-0" />
          <span className="hidden xs:inline">Project</span>
        </button>
      </div>
    </div>
  );
};

export default MobileQuickControls;
