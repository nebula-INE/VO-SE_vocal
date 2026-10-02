import React from 'react';
import { CheckCircle2, Plus, Upload, Library, Sparkles, AlertCircle } from 'lucide-react';
import BottomSheet from './BottomSheet';

interface VoicebankSheetProps {
  isOpen?: boolean;
  onClose?: () => void;
  selectedVoicebank: string;
  onSelectVoicebank: (vbName: string) => void;
  customVoicebanks: { name: string; aliasCount: number; hasVcv: boolean }[];
  onUploadZip: (e: React.ChangeEvent<HTMLInputElement>) => void;
  isUploading: boolean;
  onOpenVoicebankTab?: () => void;
}

export const VoicebankSheet: React.FC<VoicebankSheetProps> = ({
  isOpen = true,
  onClose = () => {},
  selectedVoicebank,
  onSelectVoicebank,
  customVoicebanks,
  onUploadZip,
  isUploading,
  onOpenVoicebankTab,
}) => {
  const content = (
    <div className="space-y-4 text-xs">
      {/* Active Voicebank Banner */}
      <div className="p-3 bg-[#0a84ff]/15 border border-[#0a84ff]/50 rounded-xl flex items-center justify-between">
        <div className="flex items-center space-x-2.5">
          <div className="w-8 h-8 rounded-lg bg-[#0a84ff]/20 text-[#2997ff] flex items-center justify-center border border-[#0a84ff]/40">
            <CheckCircle2 className="w-5 h-5 text-[#30d158]" />
          </div>
          <div>
            <span className="text-[10px] text-[var(--vose-text-secondary)] block">選択中の歌声 (Active Voice)</span>
            <span className="text-sm font-bold text-[var(--vose-text-primary)]">{selectedVoicebank}</span>
          </div>
        </div>

        {onOpenVoicebankTab && (
          <button
            onClick={onOpenVoicebankTab}
            className="min-h-10 px-2.5 py-1.5 rounded-lg bg-[var(--vose-bg-elevated)] hover:bg-[var(--vose-bg-hover)] text-[var(--vose-text-primary)] border border-[var(--vose-border)] font-medium transition cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff] focus-visible:ring-offset-1 focus-visible:ring-offset-[var(--vose-bg-base)]"
          >
            ライブラリ管理
          </button>
        )}
      </div>

      {/* Voicebank List */}
      <div>
        <h4 className="text-xs font-semibold text-[var(--vose-text-secondary)] mb-2 flex items-center space-x-1.5">
          <Library className="w-4 h-4 text-[#0a84ff]" />
          <span>インストール済み音源 ({customVoicebanks.length})</span>
        </h4>

        <div className="space-y-1.5 max-h-56 overflow-y-auto pr-1">
          {customVoicebanks.length === 0 ? (
            <div className="rounded-xl border border-dashed border-[var(--vose-border-strong)] bg-[var(--vose-bg-base)] p-4 text-center">
              <div className="w-9 h-9 mx-auto rounded-lg bg-[var(--vose-bg-elevated)] border border-[var(--vose-border)] text-[var(--vose-text-secondary)] flex items-center justify-center mb-2">
                <Upload className="w-4 h-4" />
              </div>
              <p className="text-xs font-semibold text-[var(--vose-text-primary)]">音源がありません</p>
              <p className="mt-1 text-[10px] leading-4 text-[var(--vose-text-muted)]">UTAU音源(.zip)を追加すると、歌声を選択してレンダリングできます。</p>
            </div>
          ) : (
            customVoicebanks.map((vb) => {
            const isSelected = vb.name === selectedVoicebank;
            return (
              <button
                type="button"
                key={vb.name}
                onClick={() => onSelectVoicebank(vb.name)}
                className={`w-full text-left p-3 rounded-xl border flex items-center justify-between transition cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff] focus-visible:ring-offset-1 focus-visible:ring-offset-[var(--vose-bg-panel)] ${

                  isSelected
                    ? 'bg-[#0a84ff]/20 border-[#0a84ff] shadow-sm shadow-[#0a84ff]/20'
                    : 'bg-[var(--vose-bg-base)] hover:bg-[var(--vose-bg-elevated)] border-[var(--vose-border)] text-[#d5d5da]'
                }`}
              >
                <div className="flex items-center space-x-2.5">
                  <div className={`w-3 h-3 rounded-full ${isSelected ? 'bg-[#0a84ff] shadow-sm shadow-[#0a84ff]' : 'bg-[#3a3a40]'}`} />
                  <div>
                    <div className="font-bold text-[var(--vose-text-primary)] text-xs">{vb.name}</div>
                    <div className="text-[10px] text-[var(--vose-text-secondary)]">
                      {vb.aliasCount} 音素エイリアス {vb.hasVcv ? '・連続音 (VCV)' : '・単独音'}
                    </div>
                  </div>
                </div>

                {isSelected && (
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-[#0a84ff]/20 text-[#2997ff] border border-[#0a84ff]/40 font-medium">
                    選択中
                  </span>
                )}
              </button>
            );
          })
          )}
        </div>
      </div>

      {/* Zip Upload Button */}
      <div className="pt-2 border-t border-[var(--vose-border)]">
        <label className="w-full min-h-11 h-11 focus-within:ring-2 focus-within:ring-[#0a84ff] focus-within:ring-offset-1 focus-within:ring-offset-[#18181a] bg-[var(--vose-bg-elevated)] hover:bg-[var(--vose-bg-hover)] text-[var(--vose-text-primary)] border border-[var(--vose-border)] rounded-xl flex items-center justify-center space-x-2 transition cursor-pointer font-medium">
          <Upload className="w-4 h-4 text-[#0a84ff]" />
          <span>{isUploading ? '音源ZIP展開中...' : 'UTAU音源(.zip) を追加'}</span>
          <input
            type="file"
            accept=".zip,application/zip,application/x-zip,application/x-zip-compressed,multipart/x-zip,application/octet-stream"
            onChange={onUploadZip}
            disabled={isUploading}
            className="hidden"
          />
        </label>
      </div>
    </div>
  );

  return (
    <BottomSheet
      isOpen={isOpen}
      onClose={onClose}
      title="音源・歌声選択 (Voicebank)"
      subtitle={`現在: ${selectedVoicebank}`}
      icon={<Library className="w-5 h-5" />}
    >
      {content}
    </BottomSheet>
  );
};

export default VoicebankSheet;
