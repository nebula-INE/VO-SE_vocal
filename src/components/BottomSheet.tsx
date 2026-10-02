import React, { useEffect, useId, useRef } from 'react';
import { X } from 'lucide-react';

interface BottomSheetProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
  heightClass?: string; // e.g. "max-h-[85vh]" or "h-[70vh]"
}

export const BottomSheet: React.FC<BottomSheetProps> = ({
  isOpen,
  onClose,
  title,
  subtitle,
  icon,
  children,
  heightClass = "max-h-[85vh]"
}) => {
  const titleId = useId();
  const sheetRef = useRef<HTMLDivElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!isOpen) return;

    restoreFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusable = sheetRef.current?.querySelector<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
    );
    focusable?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;

      const elements = Array.from(
        sheetRef.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
        ) ?? []
      );
      if (elements.length === 0) return;

      const first = elements[0];
      const last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      restoreFocusRef.current?.focus();
      restoreFocusRef.current = null;
    };
  }, [isOpen, onClose]);

  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex flex-col justify-end bg-black/65 backdrop-blur-sm animate-in fade-in duration-200" role="dialog" aria-modal="true" aria-labelledby={titleId}>
      {/* Backdrop click to close */}
      <div className="absolute inset-0" onClick={onClose} />

      {/* Sheet Container */}
      <div
        ref={sheetRef}
        className={`relative w-full bg-[var(--vose-bg-panel)] border-t border-[var(--vose-border)] rounded-t-2xl shadow-2xl flex flex-col ${heightClass} z-10 animate-in slide-in-from-bottom duration-250 pb-safe`}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Grab Handle */}
        <div className="w-full flex justify-center pt-2.5 pb-1">
          <div className="w-12 h-1.5 rounded-full bg-[var(--vose-border)]" />
        </div>

        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--vose-border)] shrink-0">
          <div className="flex items-center space-x-2.5 min-w-0">
            {icon && <div className="text-[var(--vose-accent)] shrink-0">{icon}</div>}
            <div className="min-w-0">
              <h3 id={titleId} className="text-base font-bold text-[var(--vose-text-primary)] truncate">{title}</h3>
              {subtitle && <p className="text-xs text-[var(--vose-text-secondary)] truncate">{subtitle}</p>}
            </div>
          </div>
          <button
            onClick={onClose}
            className="min-w-10 h-10 rounded-lg bg-[var(--vose-bg-elevated)] hover:bg-[var(--vose-bg-hover)] text-[var(--vose-text-secondary)] hover:text-[var(--vose-text-primary)] border border-[var(--vose-border)] flex items-center justify-center transition shrink-0 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0a84ff] focus-visible:ring-offset-1 focus-visible:ring-offset-[var(--vose-bg-panel)]"
            aria-label="閉じる"
            title="閉じる"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto overscroll-contain p-4 space-y-4">
          {children}
        </div>
      </div>
    </div>
  );
};

export default BottomSheet;
