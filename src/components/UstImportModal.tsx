import React, { useState, useRef } from 'react';
import { Upload, FileText, Clipboard, Check, X, AlertCircle, Music, Sparkles, FolderOpen } from 'lucide-react';
import { parseUstText, parseVsqxXml, parseSvpJson, parseMidiBuffer, decodeTextBuffer, ProjectData } from '../utils/formatConverter';

interface UstImportModalProps {
  isOpen: boolean;
  onClose: () => void;
  onImportData: (projectData: ProjectData, sourceFileName?: string, mode?: 'replace' | 'new_track') => void;
}

export const UstImportModal: React.FC<UstImportModalProps> = ({
  isOpen,
  onClose,
  onImportData,
}) => {
  const [activeTab, setActiveTab] = useState<'file' | 'paste'>('file');
  const [pastedText, setPastedText] = useState<string>('');
  const [importMode, setImportMode] = useState<'replace' | 'new_track'>('replace');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [previewData, setPreviewData] = useState<ProjectData | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  if (!isOpen) return null;

  const handleTriggerFileInput = () => {
    setErrorMsg(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
      fileInputRef.current.click();
    }
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    try {
      setErrorMsg(null);
      const fileName = file.name.toLowerCase();
      let pData: ProjectData | null = null;

      if (fileName.endsWith('.mid') || fileName.endsWith('.midi')) {
        const buffer = await file.arrayBuffer();
        pData = parseMidiBuffer(buffer);
      } else {
        const buffer = await file.arrayBuffer();
        const text = decodeTextBuffer(buffer);

        if (fileName.endsWith('.svp') || (text.trim().startsWith('{') && text.includes('tracks'))) {
          pData = parseSvpJson(text);
        } else if (fileName.endsWith('.vsqx') || text.includes('<vsq3>') || text.includes('<vsq4>') || text.includes('vocaloid')) {
          pData = parseVsqxXml(text);
        } else {
          pData = parseUstText(text);
        }
      }

      if (pData && pData.notes && pData.notes.length > 0) {
        onImportData(pData, file.name, importMode);
        onClose();
      } else {
        setErrorMsg('ファイル内に有効な音符データが見つかりませんでした。USTファイルの内容・形式をご確認ください。');
      }
    } catch (err: any) {
      setErrorMsg(`ファイル解析エラー: ${err.message}`);
    }
  };

  const handlePasteChange = (text: string) => {
    setPastedText(text);
    setErrorMsg(null);
    if (!text.trim()) {
      setPreviewData(null);
      return;
    }

    try {
      let pData: ProjectData | null = null;
      if (text.trim().startsWith('{') && text.includes('tracks')) {
        pData = parseSvpJson(text);
      } else if (text.includes('<vsq3>') || text.includes('<vsq4>') || text.includes('vocaloid')) {
        pData = parseVsqxXml(text);
      } else {
        pData = parseUstText(text);
      }

      if (pData && pData.notes && pData.notes.length > 0) {
        setPreviewData(pData);
      } else {
        setPreviewData(null);
      }
    } catch (e) {
      setPreviewData(null);
    }
  };

  const handleApplyPastedText = () => {
    if (!pastedText.trim()) {
      setErrorMsg('USTテキストを貼り付けてください。');
      return;
    }

    try {
      let pData: ProjectData | null = null;
      if (pastedText.trim().startsWith('{') && pastedText.includes('tracks')) {
        pData = parseSvpJson(pastedText);
      } else if (pastedText.includes('<vsq3>') || pastedText.includes('<vsq4>') || pastedText.includes('vocaloid')) {
        pData = parseVsqxXml(pastedText);
      } else {
        pData = parseUstText(pastedText);
      }

      if (pData && pData.notes && pData.notes.length > 0) {
        onImportData(pData, pData.projectName || 'Pasted UST', importMode);
        onClose();
      } else {
        setErrorMsg('有効な音符セクション ([#0000] 等) が見つかりませんでした。');
      }
    } catch (err: any) {
      setErrorMsg(`解析エラー: ${err.message}`);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4 animate-in fade-in duration-200">
      <div className="bg-[#1f1f22] border border-[#3a3a40] rounded-xl shadow-2xl w-full max-w-xl flex flex-col overflow-hidden">
        {/* Modal Header */}
        <div className="px-5 py-4 border-b border-[#303034] flex items-center justify-between bg-[#18181a]">
          <div className="flex items-center space-x-2.5">
            <div className="w-8 h-8 rounded-lg bg-[#0a84ff]/20 border border-[#0a84ff]/40 flex items-center justify-center text-[#0a84ff]">
              <Upload className="w-4 h-4" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-[#f0f0f2]">プロジェクト / USTファイルの読み込み</h2>
              <p className="text-xs text-[#9a9aa2]">UST, VSQX, SVP, MIDI ファイルまたはテキストをインポート</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-[#9a9aa2] hover:text-[#f0f0f2] rounded-lg hover:bg-[#2a2a2e] transition"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Tab Navigation */}
        <div className="flex border-b border-[#303034] bg-[#18181a] px-5 pt-3 space-x-4 text-xs font-semibold">
          <button
            onClick={() => {
              setActiveTab('file');
              setErrorMsg(null);
            }}
            className={`pb-2.5 flex items-center space-x-1.5 border-b-2 transition cursor-pointer ${
              activeTab === 'file'
                ? 'border-[#0a84ff] text-[#2997ff]'
                : 'border-transparent text-[#9a9aa2] hover:text-[#f0f0f2]'
            }`}
          >
            <FolderOpen className="w-3.5 h-3.5" />
            <span>ファイルを選択して読込 (.ust / .mid / etc.)</span>
          </button>

          <button
            onClick={() => {
              setActiveTab('paste');
              setErrorMsg(null);
            }}
            className={`pb-2.5 flex items-center space-x-1.5 border-b-2 transition cursor-pointer ${
              activeTab === 'paste'
                ? 'border-[#0a84ff] text-[#2997ff]'
                : 'border-transparent text-[#9a9aa2] hover:text-[#f0f0f2]'
            }`}
          >
            <Clipboard className="w-3.5 h-3.5" />
            <span>USTテキスト直接貼り付け</span>
          </button>
        </div>

        {/* Modal Body */}
        <div className="p-5 space-y-4">
          {/* Target Track Mode Option */}
          <div className="flex items-center justify-between bg-[#18181a] p-3 rounded-lg border border-[#303034] text-xs">
            <span className="text-[#f0f0f2] font-medium">読み込み先:</span>
            <div className="flex items-center space-x-3">
              <label className="flex items-center space-x-1.5 cursor-pointer text-[#d5d5da] hover:text-[#f0f0f2]">
                <input
                  type="radio"
                  name="importMode"
                  checked={importMode === 'replace'}
                  onChange={() => setImportMode('replace')}
                  className="accent-[#0a84ff]"
                />
                <span>現在の選択トラックに上書き</span>
              </label>
              <label className="flex items-center space-x-1.5 cursor-pointer text-[#d5d5da] hover:text-[#f0f0f2]">
                <input
                  type="radio"
                  name="importMode"
                  checked={importMode === 'new_track'}
                  onChange={() => setImportMode('new_track')}
                  className="accent-[#0a84ff]"
                />
                <span>新規ボーカルトラックとして追加</span>
              </label>
            </div>
          </div>

          {/* Hidden universal file input */}
          <input
            ref={fileInputRef}
            type="file"
            accept="*/*,.ust,.UST,.vsqx,.VSQX,.svp,.SVP,.mid,.MID,.midi,.MIDI,.txt,.TXT,text/plain,application/octet-stream"
            onChange={handleFileChange}
            className="hidden"
          />

          {/* File Picker Tab */}
          {activeTab === 'file' && (
            <div className="space-y-3">
              <div
                onClick={handleTriggerFileInput}
                className="border-2 border-dashed border-[#3a3a40] hover:border-[#0a84ff]/70 bg-[#18181a]/80 hover:bg-[#18181a] rounded-xl p-8 flex flex-col items-center justify-center cursor-pointer transition group"
              >
                <div className="w-12 h-12 rounded-full bg-[#0a84ff]/10 border border-[#0a84ff]/30 flex items-center justify-center text-[#0a84ff] group-hover:scale-110 transition mb-3">
                  <Upload className="w-6 h-6" />
                </div>
                <p className="text-sm font-bold text-[#f0f0f2] group-hover:text-[#2997ff] transition mb-1">
                  クリックしてファイルを選択
                </p>
                <p className="text-xs text-[#9a9aa2] text-center max-w-sm">
                  UTAU (*.ust), VOCALOID (*.vsqx), Synthesizer V (*.svp), MIDI (*.mid)
                </p>
                <span className="mt-3 text-[11px] bg-[#2a2a2e] text-[#d5d5da] px-3 py-1 rounded-full border border-[#3a3a40]">
                  またはファイルを画面上に直接ドラッグ＆ドロップ
                </span>
              </div>

              <div className="text-[11px] text-[#7d7d86] leading-relaxed space-y-1">
                <p>💡 <span className="text-[#9a9aa2] font-semibold">ヒント:</span> 日本語Windowsで保存されたShift_JIS / CP932形式のUSTファイルも自動で文字コード判別して読み込みます。</p>
              </div>
            </div>
          )}

          {/* Paste UST Text Tab */}
          {activeTab === 'paste' && (
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-semibold text-[#f0f0f2] mb-1.5">
                  UST または VSQX / SVP のテキストを貼り付け:
                </label>
                <textarea
                  value={pastedText}
                  onChange={(e) => handlePasteChange(e.target.value)}
                  placeholder="[#SETTING]&#10;Tempo=120.00&#10;ProjectName=MySong&#10;&#10;[#0000]&#10;Length=480&#10;Lyric=あ&#10;NoteNum=60&#10;&#10;[#0001]&#10;Length=480&#10;Lyric=い&#10;NoteNum=62"
                  rows={8}
                  className="w-full bg-[#18181a] border border-[#3a3a40] rounded-lg p-3 text-xs font-mono text-[#f0f0f2] placeholder-[#7d7d86] focus:outline-none focus:border-[#0a84ff] resize-none"
                />
              </div>

              {previewData && (
                <div className="bg-[#0a84ff]/15 border border-[#0a84ff]/50 rounded-lg p-3 text-xs text-[#2997ff] flex items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <Check className="w-4 h-4 text-[#30d158] shrink-0" />
                    <span>検出: <strong>{previewData.notes.length} 音符</strong> (BPM: {previewData.tempo || 120} / 曲名: {previewData.projectName || 'なし'})</span>
                  </div>
                  <span className="text-[10px] bg-[#0a84ff]/30 px-2 py-0.5 rounded font-mono text-[#2997ff]">OK</span>
                </div>
              )}
            </div>
          )}

          {/* Error Message */}
          {errorMsg && (
            <div className="bg-[#ff453a]/15 border border-[#ff453a]/50 rounded-lg p-3 text-xs text-[#ff453a] flex items-start space-x-2 animate-in fade-in">
              <AlertCircle className="w-4 h-4 text-[#ff453a] shrink-0 mt-0.5" />
              <span>{errorMsg}</span>
            </div>
          )}
        </div>

        {/* Modal Footer */}
        <div className="px-5 py-3 border-t border-[#303034] bg-[#18181a] flex items-center justify-between">
          <button
            onClick={onClose}
            className="px-4 py-1.5 text-xs text-[#9a9aa2] hover:text-[#f0f0f2] hover:bg-[#2a2a2e] rounded-lg transition cursor-pointer"
          >
            キャンセル
          </button>

          {activeTab === 'paste' ? (
            <button
              onClick={handleApplyPastedText}
              disabled={!previewData}
              className="px-4 py-1.5 text-xs bg-[#0a84ff] hover:bg-[#2997ff] disabled:opacity-40 disabled:hover:bg-[#0a84ff] text-white font-medium rounded-lg transition flex items-center space-x-1.5 shadow-md shadow-[#0a84ff]/30 cursor-pointer"
            >
              <Check className="w-3.5 h-3.5" />
              <span>トラックに反映して読み込み</span>
            </button>
          ) : (
            <button
              onClick={handleTriggerFileInput}
              className="px-4 py-1.5 text-xs bg-[#0a84ff] hover:bg-[#2997ff] text-white font-medium rounded-lg transition flex items-center space-x-1.5 shadow-md shadow-[#0a84ff]/30 cursor-pointer"
            >
              <FolderOpen className="w-3.5 h-3.5" />
              <span>ファイルを選択...</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default UstImportModal;
