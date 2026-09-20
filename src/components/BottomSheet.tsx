import React, { useEffect } from 'react';
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
    <div className="fixed inset-0 z-50 flex flex-col justify-end bg-black/60 backdrop-blur-xs animate-in fade-in duration-200">
      {/* Backdrop click to close */}
      <div className="absolute inset-0" onClick={onClose} />

      {/* Sheet Container */}
      <div
        className={`relative w-full bg-[#1f1f22] border-t border-[#3a3a40] rounded-t-2xl shadow-2xl flex flex-col ${heightClass} z-10 animate-in slide-in-from-bottom duration-250 pb-safe`}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Grab Handle */}
        <div className="w-full flex justify-center pt-2.5 pb-1">
          <div className="w-12 h-1.5 rounded-full bg-[#3a3a40]" />
        </div>

        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-[#303034] shrink-0">
          <div className="flex items-center space-x-2.5 min-w-0">
            {icon && <div className="text-[#0a84ff] shrink-0">{icon}</div>}
            <div className="min-w-0">
              <h3 className="text-base font-bold text-[#f0f0f2] truncate">{title}</h3>
              {subtitle && <p className="text-xs text-[#9a9aa2] truncate">{subtitle}</p>}
            </div>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 rounded-lg bg-[#2a2a2e] hover:bg-[#34343a] text-[#9a9aa2] hover:text-[#f0f0f2] border border-[#3a3a40] flex items-center justify-center transition shrink-0 cursor-pointer"
            aria-label="Close"
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
