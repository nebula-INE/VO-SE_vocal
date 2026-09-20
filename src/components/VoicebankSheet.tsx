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
            <span className="text-[10px] text-[#9a9aa2] block">選択中の歌声 (Active Voice)</span>
            <span className="text-sm font-bold text-[#f0f0f2]">{selectedVoicebank}</span>
          </div>
        </div>

        {onOpenVoicebankTab && (
          <button
            onClick={onOpenVoicebankTab}
            className="px-2.5 py-1.5 rounded-lg bg-[#2a2a2e] hover:bg-[#34343a] text-[#f0f0f2] border border-[#3a3a40] font-medium transition cursor-pointer"
          >
            ライブラリ管理
          </button>
        )}
      </div>

      {/* Voicebank List */}
      <div>
        <h4 className="text-xs font-semibold text-[#9a9aa2] mb-2 flex items-center space-x-1.5">
          <Library className="w-4 h-4 text-[#0a84ff]" />
          <span>インストール済み音源 ({customVoicebanks.length})</span>
        </h4>

        <div className="space-y-1.5 max-h-56 overflow-y-auto pr-1">
          {customVoicebanks.map((vb) => {
            const isSelected = vb.name === selectedVoicebank;
            return (
              <div
                key={vb.name}
                onClick={() => onSelectVoicebank(vb.name)}
                className={`p-3 rounded-xl border flex items-center justify-between transition cursor-pointer ${
                  isSelected
                    ? 'bg-[#0a84ff]/20 border-[#0a84ff] shadow-sm shadow-[#0a84ff]/20'
                    : 'bg-[#18181a] hover:bg-[#2a2a2e] border-[#303034] text-[#d5d5da]'
                }`}
              >
                <div className="flex items-center space-x-2.5">
                  <div className={`w-3 h-3 rounded-full ${isSelected ? 'bg-[#0a84ff] shadow-sm shadow-[#0a84ff]' : 'bg-[#3a3a40]'}`} />
                  <div>
                    <div className="font-bold text-[#f0f0f2] text-xs">{vb.name}</div>
                    <div className="text-[10px] text-[#9a9aa2]">
                      {vb.aliasCount} 音素エイリアス {vb.hasVcv ? '・連続音 (VCV)' : '・単独音'}
                    </div>
                  </div>
                </div>

                {isSelected && (
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-[#0a84ff]/20 text-[#2997ff] border border-[#0a84ff]/40 font-medium">
                    選択中
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Zip Upload Button */}
      <div className="pt-2 border-t border-[#303034]">
        <label className="w-full h-11 bg-[#2a2a2e] hover:bg-[#34343a] text-[#f0f0f2] border border-[#3a3a40] rounded-xl flex items-center justify-center space-x-2 transition cursor-pointer font-medium">
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
