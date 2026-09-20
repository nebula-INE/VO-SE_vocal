import React, { useState, useEffect, useRef } from 'react';
import { Check, X, ChevronLeft, ChevronRight } from 'lucide-react';

interface FloatingLyricInputProps {
  isOpen: boolean;
  initialLyric: string;
  noteNum: number;
  noteName: string;
  onConfirm: (newLyric: string) => void;
  onCancel: () => void;
  onNextNote?: () => void;
  onPrevNote?: () => void;
  hasNext?: boolean;
  hasPrev?: boolean;
}

export const FloatingLyricInput: React.FC<FloatingLyricInputProps> = ({
  isOpen,
  initialLyric,
  noteNum,
  noteName,
  onConfirm,
  onCancel,
  onNextNote,
  onPrevNote,
  hasNext = false,
  hasPrev = false,
}) => {
  const [lyric, setLyric] = useState(initialLyric);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setLyric(initialLyric);
      setTimeout(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      }, 50);
    }
  }, [isOpen, initialLyric]);

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onConfirm(lyric.trim() || 'あ');
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onCancel();
    } else if (e.key === 'Tab') {
      e.preventDefault();
      onConfirm(lyric.trim() || 'あ');
      if (e.shiftKey) {
        onPrevNote?.();
      } else {
        onNextNote?.();
      }
    }
  };

  return (
    <div className="fixed top-16 left-1/2 -translate-x-1/2 z-50 w-11/12 max-w-sm bg-[#1f1f22]/95 backdrop-blur-md border border-[#3a3a40] rounded-2xl shadow-2xl p-3 animate-in fade-in zoom-in-95 duration-150">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center space-x-2">
          <span className="w-6 h-6 rounded-md bg-[#0a84ff]/20 text-[#2997ff] font-mono font-bold text-xs flex items-center justify-center border border-[#0a84ff]/40">
            {noteName}
          </span>
          <span className="text-xs font-semibold text-[#f0f0f2]">歌詞 / 音素入力</span>
        </div>
        <button
          onClick={onCancel}
          className="w-7 h-7 rounded-lg text-[#9a9aa2] hover:text-[#f0f0f2] flex items-center justify-center hover:bg-[#2a2a2e] transition cursor-pointer"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <form onSubmit={handleSubmit} className="flex items-center space-x-2">
        {hasPrev && (
          <button
            type="button"
            onClick={() => {
              onConfirm(lyric.trim() || 'あ');
              onPrevNote?.();
            }}
            className="w-10 h-10 rounded-xl bg-[#2a2a2e] hover:bg-[#34343a] text-[#f0f0f2] flex items-center justify-center shrink-0 cursor-pointer border border-[#3a3a40]"
            title="前のノートへ"
          >
            <ChevronLeft className="w-5 h-5" />
          </button>
        )}

        <input
          ref={inputRef}
          type="text"
          value={lyric}
          onChange={(e) => setLyric(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="あ"
          className="flex-1 h-11 bg-[#18181a] border border-[#3a3a40] focus:border-[#0a84ff] rounded-xl px-3 text-[#f0f0f2] font-bold text-base text-center focus:outline-none"
        />

        {hasNext && (
          <button
            type="button"
            onClick={() => {
              onConfirm(lyric.trim() || 'あ');
              onNextNote?.();
            }}
            className="w-10 h-10 rounded-xl bg-[#2a2a2e] hover:bg-[#34343a] text-[#f0f0f2] flex items-center justify-center shrink-0 cursor-pointer border border-[#3a3a40]"
            title="次のノートへ"
          >
            <ChevronRight className="w-5 h-5" />
          </button>
        )}

        <button
          type="submit"
          className="w-11 h-11 rounded-xl bg-[#0a84ff] hover:bg-[#2997ff] active:bg-[#0071e3] text-white flex items-center justify-center shrink-0 shadow-md font-bold transition cursor-pointer"
          title="確定"
        >
          <Check className="w-5 h-5" />
        </button>
      </form>
      <div className="flex items-center justify-between mt-2 px-1 text-[10px] text-[#9a9aa2]">
        <span>Enterで確定 / Escで閉じる</span>
        <span>Tabで次のノートへ</span>
      </div>
    </div>
  );
};

export default FloatingLyricInput;
