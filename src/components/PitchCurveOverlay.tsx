// PitchCurveOverlay.tsx
//
// ピアノロールのノート上に直接インタラクティブなピッチカーブ・制御ノード（制御点）を描画・編集するコンポーネント。
// タップおよびドラッグ（PointerEvent/TouchEvent）に対応し、ピアノロール上で直接ピッチベンドを直感的に変化させられます。
//
// 機能:
//   ・ノード（制御点）のタップ＆ドラッグ: 時間(横)と半音(縦)をリアルタイム変更
//   ・カーブ線のタップ/ダブルタップ: タップした位置に新しいピッチノードを追加
//   ・ノードの右クリック/ロングプレス/削除ボタン: 制御点の削除(先頭ノードを除く)
//   ・ノード移動時のリアルタイム数値ポップアップ表示 (+1.20st / 45ms)

import React, { useState, useCallback, useMemo, useRef } from 'react';
import { Plus } from 'lucide-react';
import {
  parsePitchBend,
  serializePitchBend,
  msToTicks,
  ticksToMs,
  PitchPoint,
} from '../utils/pitchCurve';

interface NoteForCurve {
  id: string;
  noteNum: number;
  tick: number;
  pbs: string;
  pbw: string;
  pby: string;
}

interface PitchCurveOverlayProps {
  notes: NoteForCurve[];
  selectedNoteId: string | null;
  tempo: number;
  /** ピアノロールのDOM参照 (マウス/タッチ位置の計算用) */
  gridRef: React.RefObject<HTMLDivElement>;
  onUpdateNote?: (noteId: string, pitchData: { pbs: string; pbw: string; pby: string }) => void;
  onSelectNote?: (noteId: string) => void;
  /** グリッドの高さ(px)。 "h-[1036px]" と一致させる */
  gridHeightPx?: number;
  /** タイムラインの総tick数 */
  totalTicks?: number;
  rowHeightPx?: number;
  visibleStartTick?: number;
  visibleEndTick?: number;
}

function roundTo(v: number, decimals: number): number {
  const f = Math.pow(10, decimals);
  return Math.round(v * f) / f;
}

export default function PitchCurveOverlay({
  notes,
  selectedNoteId,
  tempo,
  gridRef,
  onUpdateNote,
  onSelectNote,
  gridHeightPx = 1036,
  totalTicks = 3840,
  rowHeightPx = 28,
  visibleStartTick,
  visibleEndTick,
}: PitchCurveOverlayProps) {
  // Viewport culling: only render curves/nodes that are within the current scroll view (or selected)
  const notesToRender = React.useMemo(() => {
    if (visibleStartTick === undefined || visibleEndTick === undefined || notes.length <= 40) {
      return notes;
    }
    const BUFFER_TICKS = 26880; // 14 measures (14 * 1920 ticks in 4/4)
    return notes.filter((n) => {
      if (n.id === selectedNoteId) return true;
      // Allow 14 measures margin on either side for pitch bends extending beyond note borders
      return (n.tick + 1920 + BUFFER_TICKS) >= visibleStartTick && (n.tick - BUFFER_TICKS) <= visibleEndTick;
    });
  }, [notes, selectedNoteId, visibleStartTick, visibleEndTick]);

  // 現在ドラッグ/タップ操作中のノード情報
  const [activeDrag, setActiveDrag] = useState<{
    noteId: string;
    pointIndex: number;
    startX: number;
    startY: number;
  } | null>(null);

  const [activeTooltip, setActiveTooltip] = useState<{
    noteId: string;
    pointIndex: number;
    xPct: number;
    yPx: number;
    semitone: number;
    offsetMs: number;
  } | null>(null);

  // 長押し(Long Press)による「ノードを追加」ポップアップ表示状態
  const [addNodePopup, setAddNodePopup] = useState<{
    noteId: string;
    xPct: number;
    yPx: number;
    offsetMs: number;
    semitone: number;
  } | null>(null);

  const longPressOverlayTimerRef = useRef<NodeJS.Timeout | null>(null);
  const startOverlayPointerPosRef = useRef<{ x: number; y: number } | null>(null);

  const handleOverlayPointerDown = useCallback(
    (note: NoteForCurve, points: PitchPoint[]) => (e: React.PointerEvent) => {
      setAddNodePopup(null);
      if (longPressOverlayTimerRef.current) clearTimeout(longPressOverlayTimerRef.current);
      if (e.button === 2) return;

      if (!gridRef.current) return;
      const rect = gridRef.current.getBoundingClientRect();
      const pointerX = e.clientX - rect.left;
      const pointerY = e.clientY - rect.top;

      const rowIdx = 84 - note.noteNum;
      const centerY = rowIdx * rowHeightPx + rowHeightPx / 2;

      const pointerTick = (pointerX / rect.width) * totalTicks;
      const tickOffset = pointerTick - note.tick;
      const offsetMs = Math.round(ticksToMs(tickOffset, tempo || 120));
      const semitone = roundTo(Math.max(-12, Math.min(12, (centerY - pointerY) / rowHeightPx)), 2);

      const xPct = (pointerX / rect.width) * 100;
      startOverlayPointerPosRef.current = { x: e.clientX, y: e.clientY };

      longPressOverlayTimerRef.current = setTimeout(() => {
        setAddNodePopup({
          noteId: note.id,
          xPct,
          yPx: pointerY,
          offsetMs,
          semitone,
        });
        if (typeof navigator !== 'undefined' && navigator.vibrate) {
          try { navigator.vibrate(40); } catch (_) {}
        }
      }, 380);
    },
    [gridRef, rowHeightPx, tempo, totalTicks]
  );

  const handleOverlayPointerMove = useCallback((e: React.PointerEvent) => {
    if (startOverlayPointerPosRef.current) {
      const dx = Math.abs(e.clientX - startOverlayPointerPosRef.current.x);
      const dy = Math.abs(e.clientY - startOverlayPointerPosRef.current.y);
      if (dx > 8 || dy > 8) {
        if (longPressOverlayTimerRef.current) {
          clearTimeout(longPressOverlayTimerRef.current);
          longPressOverlayTimerRef.current = null;
        }
      }
    }
  }, []);

  const handleOverlayPointerUp = useCallback(() => {
    if (longPressOverlayTimerRef.current) {
      clearTimeout(longPressOverlayTimerRef.current);
      longPressOverlayTimerRef.current = null;
    }
  }, []);

  // ノードのドラッグ操作開始 (PointerDown)
  const handleNodePointerDown = useCallback(
    (note: NoteForCurve, pointIdx: number, points: PitchPoint[]) =>
      (e: React.PointerEvent) => {
        e.stopPropagation();
        e.preventDefault();

        if (onSelectNote && note.id !== selectedNoteId) {
          onSelectNote(note.id);
        }

        const target = e.currentTarget as HTMLElement;
        try {
          target.setPointerCapture(e.pointerId);
        } catch (err) {}

        setActiveDrag({
          noteId: note.id,
          pointIndex: pointIdx,
          startX: e.clientX,
          startY: e.clientY,
        });

        const p = points[pointIdx];
        if (p) {
          const rowIdx = 84 - note.noteNum;
          const centerY = rowIdx * rowHeightPx + rowHeightPx / 2;
          const tickOffset = msToTicks(p.offsetMs, tempo || 120);
          const absTick = note.tick + tickOffset;
          const xPct = (absTick / totalTicks) * 100;
          const yPx = centerY - p.semitone * rowHeightPx;

          setActiveTooltip({
            noteId: note.id,
            pointIndex: pointIdx,
            xPct,
            yPx,
            semitone: p.semitone,
            offsetMs: p.offsetMs,
          });
        }
      },
    [onSelectNote, selectedNoteId, tempo, totalTicks, rowHeightPx]
  );

  // ノードのドラッグ移動 (PointerMove)
  const handleNodePointerMove = useCallback(
    (note: NoteForCurve, pointIdx: number, points: PitchPoint[]) =>
      (e: React.PointerEvent) => {
        if (!activeDrag || activeDrag.noteId !== note.id || activeDrag.pointIndex !== pointIdx) return;
        if (!gridRef.current || !onUpdateNote) return;

        const rect = gridRef.current.getBoundingClientRect();
        if (rect.width <= 0) return;

        const rowIdx = 84 - note.noteNum;
        const centerY = rowIdx * rowHeightPx + rowHeightPx / 2;

        const pointerX = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
        const pointerY = Math.max(0, Math.min(gridHeightPx, e.clientY - rect.top));

        // 横軸: 時間 (ms) の計算
        const pointerTick = (pointerX / rect.width) * totalTicks;
        const tickOffset = pointerTick - note.tick;
        let newOffsetMs = Math.round(ticksToMs(tickOffset, tempo || 120));

        // 縦軸: 半音 (semitone) オフセットの計算
        const pointerSemitone = (centerY - pointerY) / rowHeightPx;
        const newSemitone = roundTo(Math.max(-12, Math.min(12, pointerSemitone)), 2);

        // 先頭ノード (PBS基準点) と 後続ノード の順序制約
        const nextPoints = points.map((pt, idx) => {
          if (idx !== pointIdx) return pt;
          let clampedMs = newOffsetMs;
          if (pointIdx > 0) {
            const prevPt = points[pointIdx - 1];
            clampedMs = Math.max(prevPt.offsetMs + 2, clampedMs);
          }
          if (pointIdx < points.length - 1) {
            const nextPt = points[pointIdx + 1];
            clampedMs = Math.min(nextPt.offsetMs - 2, clampedMs);
          }
          return { offsetMs: clampedMs, semitone: newSemitone };
        });

        const newPitchData = serializePitchBend(nextPoints);
        onUpdateNote(note.id, newPitchData);

        const updatedPt = nextPoints[pointIdx];
        const tickOffsetUpdated = msToTicks(updatedPt.offsetMs, tempo || 120);
        const absTickUpdated = note.tick + tickOffsetUpdated;
        const xPct = (absTickUpdated / totalTicks) * 100;
        const yPx = centerY - updatedPt.semitone * rowHeightPx;

        setActiveTooltip({
          noteId: note.id,
          pointIndex: pointIdx,
          xPct,
          yPx,
          semitone: updatedPt.semitone,
          offsetMs: updatedPt.offsetMs,
        });
      },
    [activeDrag, gridRef, onUpdateNote, tempo, totalTicks, rowHeightPx, gridHeightPx]
  );

  // ドラッグ完了 (PointerUp / PointerCancel)
  const handleNodePointerUp = useCallback(
    (e: React.PointerEvent) => {
      try {
        (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
      } catch (err) {}
      setActiveDrag(null);
    },
    []
  );

  // ピッチ線タップ/ダブルクリックで新しい制御点を追加
  const handleAddPointAt = useCallback(
    (note: NoteForCurve, points: PitchPoint[], e: React.PointerEvent | React.MouseEvent) => {
      e.stopPropagation();
      e.preventDefault();
      if (!gridRef.current || !onUpdateNote) return;

      if (onSelectNote && note.id !== selectedNoteId) {
        onSelectNote(note.id);
      }

      const rect = gridRef.current.getBoundingClientRect();
      const pointerX = e.clientX - rect.left;
      const pointerY = e.clientY - rect.top;

      const rowIdx = 84 - note.noteNum;
      const centerY = rowIdx * rowHeightPx + rowHeightPx / 2;

      // 既存の制御点と極めて近い(24px以内)タップの場合は重複ノード作成を防ぐ
      const isNearNode = points.some((pt) => {
        const ptTickOffset = msToTicks(pt.offsetMs, tempo || 120);
        const ptX = ((note.tick + ptTickOffset) / totalTicks) * rect.width;
        const ptY = centerY - pt.semitone * rowHeightPx;
        return Math.hypot(pointerX - ptX, pointerY - ptY) < 24;
      });

      if (isNearNode) return;

      const pointerTick = (pointerX / rect.width) * totalTicks;
      const tickOffset = pointerTick - note.tick;
      const offsetMs = Math.round(ticksToMs(tickOffset, tempo || 120));
      const semitone = roundTo(Math.max(-12, Math.min(12, (centerY - pointerY) / rowHeightPx)), 2);

      const newPoint: PitchPoint = { offsetMs, semitone };
      const nextPoints = [...points, newPoint].sort((a, b) => a.offsetMs - b.offsetMs);

      onUpdateNote(note.id, serializePitchBend(nextPoints));
    },
    [gridRef, onUpdateNote, onSelectNote, selectedNoteId, tempo, totalTicks, rowHeightPx]
  );

  // ノード削除 (右クリックまたはタップ削除ボタン)
  const handleDeletePoint = useCallback(
    (noteId: string, points: PitchPoint[], index: number) => (e: React.SyntheticEvent) => {
      e.stopPropagation();
      e.preventDefault();
      if (index === 0 || points.length <= 1) return; // 先頭ノードは基準点のため残す
      if (!onUpdateNote) return;

      const nextPoints = points.filter((_, i) => i !== index);
      onUpdateNote(noteId, serializePitchBend(nextPoints));
      setActiveTooltip(null);
    },
    [onUpdateNote]
  );

  return (
    <div className="absolute inset-0 z-20 pointer-events-none overflow-visible">
      {/* 1. SVG レイヤー (ピッチカーブライン描画) */}
      <svg
        className="w-full h-full"
        height={gridHeightPx}
        viewBox={`0 0 100 ${gridHeightPx}`}
        preserveAspectRatio="none"
      >
        {notesToRender.map((note) => {
          const points = parsePitchBend(note.pbs, note.pbw, note.pby);
          if (points.length === 0) return null;

          const isSelected = note.id === selectedNoteId;
          const rowIdx = 84 - note.noteNum;
          const centerY = rowIdx * rowHeightPx + rowHeightPx / 2;

          const coords = points.map((p) => {
            const tickOffset = msToTicks(p.offsetMs, tempo || 120);
            const xPct = ((note.tick + tickOffset) / totalTicks) * 100;
            const y = centerY - p.semitone * rowHeightPx;
            return { xPct, y };
          });

          const pathD = coords
            .map((c, i) => `${i === 0 ? 'M' : 'L'} ${c.xPct.toFixed(3)} ${c.y.toFixed(2)}`)
            .join(' ');

          return (
            <g key={note.id} className="pointer-events-auto">
              {/* 発光グローレイヤー */}
              <path
                d={pathD}
                fill="none"
                stroke={isSelected ? '#06b6d4' : '#38bdf8'}
                strokeOpacity={isSelected ? 0.35 : 0.15}
                strokeWidth={6}
                vectorEffect="non-scaling-stroke"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="pointer-events-none"
              />

              {/* タップ判定拡大用の広め当たり判定ライン (タップまたは長押し対応) */}
              <path
                d={pathD}
                fill="none"
                stroke="transparent"
                strokeWidth={18}
                vectorEffect="non-scaling-stroke"
                className="cursor-pointer"
                onPointerDown={(e) => {
                  handleOverlayPointerDown(note, points)(e);
                  handleAddPointAt(note, points, e);
                }}
                onPointerMove={handleOverlayPointerMove}
                onPointerUp={handleOverlayPointerUp}
                onPointerCancel={handleOverlayPointerUp}
              />

              {/* メインのピッチカーブライン */}
              <path
                d={pathD}
                fill="none"
                stroke={isSelected ? '#0a84ff' : '#0a84ff'}
                strokeOpacity={isSelected ? 0.35 : 0.15}
                strokeWidth={isSelected ? 5 : 3}
                vectorEffect="non-scaling-stroke"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="pointer-events-none"
              />
              <path
                d={pathD}
                fill="none"
                stroke={isSelected ? '#2997ff' : '#5ac8fa'}
                strokeOpacity={isSelected ? 1 : 0.75}
                strokeWidth={isSelected ? 2 : 1.2}
                vectorEffect="non-scaling-stroke"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="pointer-events-none"
              />
            </g>
          );
        })}
      </svg>

      {/* 2. HTML オーバーレイレイヤー (完全な正円のコントロールノード) */}
      {notesToRender.map((note) => {
        const points = parsePitchBend(note.pbs, note.pbw, note.pby);
        if (points.length === 0) return null;

        const isSelected = note.id === selectedNoteId;
        const rowIdx = 84 - note.noteNum;
        const centerY = rowIdx * rowHeightPx + rowHeightPx / 2;

        return points.map((p, i) => {
          const tickOffset = msToTicks(p.offsetMs, tempo || 120);
          const xPct = ((note.tick + tickOffset) / totalTicks) * 100;
          const y = centerY - p.semitone * rowHeightPx;

          const isDraggingThis =
            activeDrag?.noteId === note.id && activeDrag?.pointIndex === i;

          return (
            <div
              key={`${note.id}-node-${i}`}
              className="absolute transform -translate-x-1/2 -translate-y-1/2 cursor-grab active:cursor-grabbing touch-none pointer-events-auto flex items-center justify-center z-25"
              style={{
                left: `${xPct.toFixed(3)}%`,
                top: `${y.toFixed(2)}px`,
                width: '36px',
                height: '36px',
              }}
              onPointerDown={handleNodePointerDown(note, i, points)}
              onPointerMove={handleNodePointerMove(note, i, points)}
              onPointerUp={handleNodePointerUp}
              onPointerCancel={handleNodePointerUp}
              onContextMenu={handleDeletePoint(note.id, points, i)}
            >
              {/* 完全な真円ノード (小型サイズ: 直径 8px / 選択時 10px / ドラッグ時 12px) */}
              <div
                className={`rounded-full transition-transform duration-75 shadow-md flex items-center justify-center border ${
                  isDraggingThis
                    ? 'w-3 h-3 bg-white border-[#0a84ff] ring-2 ring-[#0a84ff]/60 scale-110'
                    : isSelected
                    ? 'w-2.5 h-2.5 bg-[#2997ff] border-white ring-1 ring-[#0a84ff]/50'
                    : 'w-2 h-2 bg-[#f0f0f2] border-[#0a84ff]'
                }`}
              >
                <div
                  className={`rounded-full ${
                    i === 0 ? 'w-1 h-1 bg-[#0051a8]' : 'w-0.5 h-0.5 bg-[#3a3a40]'
                  }`}
                />
              </div>
            </div>
          );
        });
      })}

      {/* 長押し(Long Press)で表示される「ノードを追加」ボタンポップアップ */}
      {addNodePopup && (
        <div
          className="absolute transform -translate-x-1/2 -translate-y-full mb-3 pointer-events-auto z-40 animate-in fade-in zoom-in-95 duration-150"
          style={{
            left: `${addNodePopup.xPct}%`,
            top: `${addNodePopup.yPx}px`,
          }}
        >
          <button
            onClick={(e) => {
              e.stopPropagation();
              e.preventDefault();
              const note = notes.find((n) => n.id === addNodePopup.noteId);
              if (note && onUpdateNote) {
                const pts = parsePitchBend(note.pbs, note.pbw, note.pby);
                const newPt: PitchPoint = { offsetMs: addNodePopup.offsetMs, semitone: addNodePopup.semitone };
                const nextPts = [...pts, newPt].sort((a, b) => a.offsetMs - b.offsetMs);
                onUpdateNote(note.id, serializePitchBend(nextPts));
              }
              setAddNodePopup(null);
            }}
            className="bg-[#0a84ff] hover:bg-[#2997ff] active:scale-95 text-white font-bold px-3 py-1.5 rounded-full shadow-2xl border border-white text-[11px] flex items-center space-x-1.5 whitespace-nowrap cursor-pointer transition ring-2 ring-[#0a84ff]/50"
          >
            <Plus className="w-3.5 h-3.5 stroke-[3]" />
            <span>ノードを追加 ({addNodePopup.semitone >= 0 ? `+${addNodePopup.semitone.toFixed(2)}` : addNodePopup.semitone.toFixed(2)}st)</span>
          </button>
        </div>
      )}

      {/* ノード移動中・タップ中のリアルタイム情報ポップアップTooltip */}
      {activeTooltip && (
        <div
          className="absolute transform -translate-x-1/2 -translate-y-full mb-3 pointer-events-auto z-30"
          style={{
            left: `${activeTooltip.xPct}%`,
            top: `${activeTooltip.yPx}px`,
          }}
        >
          <div className="bg-[#1f1f22]/95 border border-[#0a84ff]/60 text-[#2997ff] text-[11px] font-mono px-2 py-1 rounded-md shadow-xl flex items-center space-x-1.5 backdrop-blur-sm">
            <span className="font-bold text-[#f0f0f2]">
              {activeTooltip.semitone >= 0 ? `+${activeTooltip.semitone.toFixed(2)}` : activeTooltip.semitone.toFixed(2)} st
            </span>
            <span className="text-[#7d7d86]">|</span>
            <span className="text-[#9a9aa2]">{activeTooltip.offsetMs}ms</span>

            {/* 先頭以外のノードにはクイック削除ボタンを表示 */}
            {activeTooltip.pointIndex > 0 && (
              <button
                onClick={(e) => {
                  const targetNote = notes.find((n) => n.id === activeTooltip.noteId);
                  if (targetNote) {
                    const pts = parsePitchBend(targetNote.pbs, targetNote.pbw, targetNote.pby);
                    handleDeletePoint(activeTooltip.noteId, pts, activeTooltip.pointIndex)(e);
                  }
                }}
                className="ml-1 text-[#9a9aa2] hover:text-[#ff453a] hover:bg-[#2a2a2e] rounded p-0.5 transition"
                title="このノードを削除"
              >
                ✕
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
