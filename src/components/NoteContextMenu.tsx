import React, { useEffect, useRef } from 'react';
import { Trash2, Type, AudioWaveform, Copy, X } from 'lucide-react';

interface NoteContextMenuProps {
  x: number;
  y: number;
  noteId: string;
  noteLyric: string;
  noteName: string;
  onEditLyric: () => void;
  onDelete: () => void;
  onDuplicate: () => void;
  onOpenPitch: () => void;
  onClose: () => void;
}

export const NoteContextMenu: React.FC<NoteContextMenuProps> = ({
  x,
  y,
  noteLyric,
  noteName,
  onEditLyric,
  onDelete,
  onDuplicate,
  onOpenPitch,
  onClose,
}) => {
  const menuRef = useRef<HTMLDivElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    restoreFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const selector = 'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])';
    const first = menuRef.current?.querySelector<HTMLElement>(selector);
    first?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); return; }
      if (event.key !== 'Tab') return;
      const elements = Array.from(menuRef.current?.querySelectorAll<HTMLElement>(selector) ?? []);
      if (!elements.length) return;
      const firstElement = elements[0], lastElement = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === firstElement) { event.preventDefault(); lastElement.focus(); }
      else if (!event.shiftKey && document.activeElement === lastElement) { event.preventDefault(); firstElement.focus(); }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => { document.removeEventListener('keydown', handleKeyDown); restoreFocusRef.current?.focus(); restoreFocusRef.current = null; };
  }, [onClose]);

  // Clamp within window bounds
  const clampedX = Math.max(10, Math.min(window.innerWidth - 180, x));
  const clampedY = Math.max(10, Math.min(window.innerHeight - 200, y));

  return (
    <div className="fixed inset-0 z-50 select-none" role="presentation" onClick={onClose}>
      <div
        className="absolute bg-[#1f1f22] border border-[#3a3a40] rounded-xl shadow-2xl p-1.5 min-w-[170px] z-50 text-xs text-[#f0f0f2] animate-in fade-in zoom-in-95 duration-100 space-y-1"
        style={{ left: `${clampedX}px`, top: `${clampedY}px` }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-2.5 py-1.5 border-b border-[#303034] flex items-center justify-between text-[#9a9aa2]">
          <span className="font-bold text-[#2997ff]">{noteName} ({noteLyric})</span>
          <button onClick={onClose} className="p-0.5 rounded hover:bg-[#2a2a2e] text-[#9a9aa2] hover:text-[#f0f0f2]">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>

        <button
          onClick={() => {
            onEditLyric();
            onClose();
          }}
          className="w-full px-2.5 py-2 rounded-lg hover:bg-[#2a2a2e] active:bg-[#34343a] flex items-center space-x-2 text-left cursor-pointer transition text-[#f0f0f2]"
        >
          <Type className="w-4 h-4 text-[#0a84ff]" />
          <span>歌詞の編集 (Lyric)</span>
        </button>

        <button
          onClick={() => {
            onOpenPitch();
            onClose();
          }}
          className="w-full px-2.5 py-2 rounded-lg hover:bg-[#2a2a2e] active:bg-[#34343a] flex items-center space-x-2 text-left cursor-pointer transition text-[#f0f0f2]"
        >
          <AudioWaveform className="w-4 h-4 text-[#bf5af2]" />
          <span>ピッチ設定 (Pitch)</span>
        </button>

        <button
          onClick={() => {
            onDuplicate();
            onClose();
          }}
          className="w-full px-2.5 py-2 rounded-lg hover:bg-[#2a2a2e] active:bg-[#34343a] flex items-center space-x-2 text-left cursor-pointer transition text-[#f0f0f2]"
        >
          <Copy className="w-4 h-4 text-[#30d158]" />
          <span>ノート複製</span>
        </button>

        <div className="border-t border-[#303034] my-1" />

        <button
          onClick={() => {
            onDelete();
            onClose();
          }}
          className="w-full px-2.5 py-2 rounded-lg hover:bg-[#ff453a]/15 active:bg-[#ff453a]/25 text-[#ff453a] flex items-center space-x-2 text-left cursor-pointer transition"
        >
          <Trash2 className="w-4 h-4 text-[#ff453a]" />
          <span>ノート削除 (Delete)</span>
        </button>
      </div>
    </div>
  );
};

export default NoteContextMenu;
