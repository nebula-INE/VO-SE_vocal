import React from 'react';
import { parsePitchBend } from '../utils/pitchCurve';

export interface GhostTrackOverlayProps {
  ghostTracks: {
    id: string;
    name: string;
    color?: string;
    notes: any[];
  }[];
  totalTicks: number;
  rowHeightPx: number;
  visibleStartTick?: number;
  visibleEndTick?: number;
}

const DEFAULT_GHOST_COLORS = ['#0a84ff', '#ff9f0a', '#34c759', '#af52de', '#5ac8fa', '#ff375f'];

export const GhostTrackOverlay: React.FC<GhostTrackOverlayProps> = ({
  ghostTracks,
  totalTicks,
  rowHeightPx,
  visibleStartTick,
  visibleEndTick,
}) => {
  if (ghostTracks.length === 0) return null;

  return (
    <div className="absolute inset-0 pointer-events-none z-10 overflow-hidden">
      {ghostTracks.map((gt, tIdx) => {
        const color = gt.color || DEFAULT_GHOST_COLORS[tIdx % DEFAULT_GHOST_COLORS.length];
        const notesToRender = (visibleStartTick !== undefined && visibleEndTick !== undefined)
          ? gt.notes.filter((note) => (note.tick + (note.length || 480)) >= visibleStartTick && note.tick <= visibleEndTick)
          : gt.notes;

        return (
          <React.Fragment key={gt.id}>
            {notesToRender.map((note) => {
              const rowIdx = 84 - note.noteNum;
              const topPos = rowIdx * rowHeightPx;
              const leftPct = (note.tick / totalTicks) * 100;
              const widthPct = (note.length / totalTicks) * 100;

              return (
                <div
                  key={note.id}
                  className="absolute rounded border opacity-40 flex items-center justify-between px-1.5 text-[10px] font-bold"
                  style={{
                    top: `${topPos + 2}px`,
                    height: `${Math.max(12, rowHeightPx - 4)}px`,
                    left: `${leftPct}%`,
                    width: `${Math.max(widthPct, 1)}%`,
                    backgroundColor: `${color}33`,
                    borderColor: color,
                    color: color,
                  }}
                >
                  <span className="truncate">{note.lyric}</span>
                  <span className="text-[8px] font-mono opacity-80 pl-0.5">{gt.name}</span>
                </div>
              );
            })}
          </React.Fragment>
        );
      })}
    </div>
  );
};

export default GhostTrackOverlay;
