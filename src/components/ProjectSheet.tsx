import React from 'react';
import {
  Upload, Download, Sparkles, FolderOpen, Save, FileText, CheckCircle2,
  RefreshCw, Music, HardDrive
} from 'lucide-react';
import BottomSheet from './BottomSheet';

interface ProjectSheetProps {
  isOpen?: boolean;
  onClose?: () => void;
  onImportClick?: () => void;
  onImport?: () => void;
  onExportClick?: (fmt: 'ust' | 'vsqx' | 'svp' | 'midi') => void;
  onExportProject?: (fmt: 'ust' | 'vsqx' | 'svp' | 'midi') => void;
  onRenderWasm?: () => void;
  onExportWav?: () => void;
  isRendering?: boolean;
  isRenderingWav?: boolean;
  renderProgress?: number;
  formatEta?: (ms: number) => string;
  notesCount?: number;
  tempo: number;
  onUpdateTempo?: (tempo: number) => void;
  projectName?: string;
  onUpdateProjectName?: (name: string) => void;
}

export const ProjectSheet: React.FC<ProjectSheetProps> = ({
  isOpen = true,
  onClose = () => {},
  onImportClick,
  onImport,
  onExportClick,
  onExportProject,
  onRenderWasm,
  onExportWav,
  isRendering = false,
  isRenderingWav = false,
  renderProgress,
  formatEta,
  notesCount = 0,
  tempo,
  onUpdateTempo,
  projectName = 'VO-SE Project',
  onUpdateProjectName = () => {},
}) => {
  const handleImport = onImport || onImportClick || (() => {});
  const handleExport = onExportProject || onExportClick || (() => {});
  const handleRender = onExportWav || onRenderWasm || (() => {});
  const renderingActive = isRendering || isRenderingWav;

  const content = (
    <div className="space-y-4 text-xs">
      {/* Project Meta Info */}
      <div className="p-3 bg-[#18181a] border border-[#3a3a40] rounded-xl space-y-2">
        <label className="text-[10px] text-[#9a9aa2] font-medium">プロジェクト名 (Project Name):</label>
        <input
          type="text"
          value={projectName}
          onChange={(e) => onUpdateProjectName(e.target.value)}
          className="w-full h-9 bg-[#2a2a2e] border border-[#3a3a40] rounded-lg px-2.5 text-[#f0f0f2] font-bold focus:border-[#0a84ff] focus:outline-none"
        />
        <div className="flex items-center justify-between text-[11px] text-[#9a9aa2] pt-1">
          <div className="flex items-center space-x-1.5">
            <span>テンポ:</span>
            {onUpdateTempo ? (
              <input
                type="number"
                value={tempo}
                onChange={(e) => onUpdateTempo(Number(e.target.value) || 120)}
                className="w-16 h-7 bg-[#2a2a2e] border border-[#3a3a40] rounded px-1.5 text-[#2997ff] font-mono text-center font-bold focus:border-[#0a84ff] focus:outline-none"
              />
            ) : (
              <strong className="text-[#2997ff] font-mono">{tempo} BPM</strong>
            )}
          </div>
          {notesCount > 0 && (
            <span>総ノート数: <strong className="text-[#2997ff] font-mono">{notesCount}</strong></span>
          )}
        </div>
      </div>

      {/* Render Voice Section */}
      <div className="p-3.5 bg-[#0a84ff]/10 border border-[#0a84ff]/40 rounded-xl space-y-2">
        <div className="flex items-center space-x-2">
          <Sparkles className="w-4 h-4 text-[#0a84ff]" />
          <h4 className="font-bold text-[#f0f0f2]">WASM 音声レンダリング (Render)</h4>
        </div>
        <p className="text-[11px] text-[#9a9aa2]">
          C++ネイティブ合成エンジン（vose_core / WORLD）で全ノートを高音質WAVへ一括書き出しします。
        </p>
        <button
          onClick={handleRender}
          disabled={renderingActive}
          className="w-full h-11 bg-[#0a84ff] hover:bg-[#2997ff] active:bg-[#0071e3] text-white font-bold rounded-xl flex items-center justify-center space-x-2 transition shadow-lg shadow-[#0a84ff]/30 cursor-pointer disabled:opacity-50"
        >
          {renderingActive ? (
            <>
              <RefreshCw className="w-4 h-4 animate-spin" />
              <span>
                {renderProgress !== undefined ? `レンダリング中... ${Math.round(renderProgress * 100)}%` : 'レンダリング実行中...'}
              </span>
            </>
          ) : (
            <>
              <Sparkles className="w-4 h-4 text-[#ffd60a]" />
              <span>歌声をWAVレンダリング</span>
            </>
          )}
        </button>
      </div>

      {/* Import Formats */}
      <div>
        <h4 className="text-xs font-semibold text-[#9a9aa2] mb-2 flex items-center space-x-1.5">
          <Upload className="w-4 h-4 text-[#0a84ff]" />
          <span>プロジェクト読み込み (Import)</span>
        </h4>
        <button
          onClick={handleImport}
          className="w-full h-11 bg-[#2a2a2e] hover:bg-[#34343a] active:bg-[#2a2a2e]/80 text-[#2997ff] border border-[#3a3a40] rounded-xl flex items-center justify-center space-x-2 transition cursor-pointer font-medium"
        >
          <Upload className="w-4 h-4 text-[#0a84ff]" />
          <span>UST / VSQX / SVP / MIDI ファイルを開く</span>
        </button>
      </div>

      {/* Export Formats */}
      <div>
        <h4 className="text-xs font-semibold text-[#9a9aa2] mb-2 flex items-center space-x-1.5">
          <Download className="w-4 h-4 text-[#0a84ff]" />
          <span>形式を指定して書き出し (Export)</span>
        </h4>
        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={() => handleExport('ust')}
            className="h-10 bg-[#18181a] hover:bg-[#2a2a2e] border border-[#3a3a40] rounded-lg flex items-center justify-center space-x-1.5 text-[#f0f0f2] transition cursor-pointer font-medium"
          >
            <FileText className="w-3.5 h-3.5 text-[#0a84ff]" />
            <span>.UST (UTAU)</span>
          </button>
          <button
            onClick={() => handleExport('vsqx')}
            className="h-10 bg-[#18181a] hover:bg-[#2a2a2e] border border-[#3a3a40] rounded-lg flex items-center justify-center space-x-1.5 text-[#f0f0f2] transition cursor-pointer font-medium"
          >
            <FileText className="w-3.5 h-3.5 text-[#bf5af2]" />
            <span>.VSQX (VOCALOID)</span>
          </button>
          <button
            onClick={() => handleExport('svp')}
            className="h-10 bg-[#18181a] hover:bg-[#2a2a2e] border border-[#3a3a40] rounded-lg flex items-center justify-center space-x-1.5 text-[#f0f0f2] transition cursor-pointer font-medium"
          >
            <FileText className="w-3.5 h-3.5 text-[#30d158]" />
            <span>.SVP (Synthesizer V)</span>
          </button>
          <button
            onClick={() => handleExport('midi')}
            className="h-10 bg-[#18181a] hover:bg-[#2a2a2e] border border-[#3a3a40] rounded-lg flex items-center justify-center space-x-1.5 text-[#f0f0f2] transition cursor-pointer font-medium"
          >
            <Music className="w-3.5 h-3.5 text-[#ff9f0a]" />
            <span>Standard MIDI (.mid)</span>
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <BottomSheet
      isOpen={isOpen}
      onClose={onClose}
      title="プロジェクト・ファイル (Project)"
      subtitle={projectName}
      icon={<FolderOpen className="w-5 h-5" />}
    >
      {content}
    </BottomSheet>
  );
};

export default ProjectSheet;
