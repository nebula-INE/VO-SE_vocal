import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import {
  Play, Pause, Square, Music, Upload, Download, Settings, RefreshCw,
  Monitor, Cpu, Volume2, Sliders, Layers, Sparkles, FileText, CheckCircle2,
  AlertCircle, ChevronRight, AudioWaveform, Plus, Trash2, Edit3, HelpCircle, Loader2,
  Activity, Zap, X, Library, DownloadCloud, HardDrive, Check, Search, FolderPlus, Star, ShieldAlert, ZoomIn, ZoomOut, RotateCcw, Maximize2,
  Type
} from 'lucide-react';
import { bufferToWav } from './utils/audioEncoder';
import {
  parsePitchBend,
  msToTicks,
  smoothPitchBendPoints,
  calculateFormantCutoff,
  softClampSemitone,
  scheduleSafePitchRamp
} from './utils/pitchCurve';
import {
  parseUstText, exportUstText,
  parseVsqxXml, exportVsqxXml,
  parseSvpJson, exportSvpJson,
  parseMidiBuffer, exportMidiBuffer,
  decodeTextBuffer,
  ProjectData
} from './utils/formatConverter';
// vose_core WASM(本物のC++コア)経由でレンダリング（例外発生時もスキップし、JSへのフォールバックは絶対に行わない）
import { renderStudioCore as renderWasm } from './voseCoreClient';
import PitchCurveOverlay from './components/PitchCurveOverlay';
import PitchCurveMiniEditor from './components/PitchCurveMiniEditor';
import MultiTrackPanel from './components/MultiTrackPanel';
import GhostTrackOverlay from './components/GhostTrackOverlay';
import realtimeEngine from './utils/realtimeAudioEngine';
import BatchLyricModal from './components/BatchLyricModal';
import UstImportModal from './components/UstImportModal';

interface Note {
  id: string;
  lyric: string;
  noteNum: number; // MIDI pitch 36-96
  tick: number; // 0 to 3840... (480 ticks = 1 beat)
  length: number; // in ticks (e.g. 480 = quarter note)
  intensity: number; // 0-150
  flags: string; // e.g. "g-5B50"
  pbs: string; // Pitch bend start e.g. "0;0"
  pbw: string; // Pitch bend width e.g. "50,100"
  pby: string; // Pitch bend height e.g. "0,5"
}

interface Track {
  id: string;
  name: string;
  type: 'vocal' | 'wave';
  voicebank?: string;
  notes: Note[];
  volume: number;
  isMuted: boolean;
  isSolo: boolean;
  audioUrl?: string;
}

interface UstProjectData {
  tempo: number;
  projectName: string;
  voicebank: string;
  flags: string;
  notes: Note[];
}

interface PyStatus {
  pythonVersion: string;
  pysideInstalled: boolean;
  engineLibExists: boolean;
  desktopEntryPoint: string;
  mode: string;
}

const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

const REST_LYRICS_SET = new Set(['r', 'r_', '息', 'br', 'pau', 'sil', '吸', '', ' ', '　', '休', '・', '-', 'ー', '~']);
export const isRestLyric = (lyric?: string): boolean => {
  if (!lyric) return true;
  const l = lyric.trim().toLowerCase();
  return REST_LYRICS_SET.has(l);
};

export const getSampleCacheKey = (vb: string, lyric: string, prevLyric?: string, noteNum?: number) => {
  return `${vb}:${lyric}:${prevLyric || ''}:${noteNum || 60}`;
};

const getNoteName = (midiNum: number) => {
  const octave = Math.floor(midiNum / 12) - 1;
  const noteName = NOTE_NAMES[midiNum % 12];
  return `${noteName}${octave}`;
};

const isBlackKey = (midiNum: number) => {
  const noteInOctave = midiNum % 12;
  return [1, 3, 6, 8, 10].includes(noteInOctave);
};

// Default sample notes ("か", "え", "る", "の", "う", "た", "が")
const INITIAL_NOTES: Note[] = [
  { id: '1', lyric: 'か', noteNum: 60, tick: 0, length: 480, intensity: 120, flags: '', pbs: '-20;0', pbw: '50,100', pby: '0,5' },
  { id: '2', lyric: 'え', noteNum: 62, tick: 480, length: 480, intensity: 120, flags: '', pbs: '0;0', pbw: '50', pby: '0' },
  { id: '3', lyric: 'る', noteNum: 64, tick: 960, length: 480, intensity: 120, flags: '', pbs: '0;0', pbw: '50', pby: '0' },
  { id: '4', lyric: 'の', noteNum: 65, tick: 1440, length: 480, intensity: 120, flags: '', pbs: '0;0', pbw: '50', pby: '0' },
  { id: '5', lyric: 'う', noteNum: 64, tick: 1920, length: 480, intensity: 120, flags: '', pbs: '0;0', pbw: '50', pby: '0' },
  { id: '6', lyric: 'た', noteNum: 62, tick: 2400, length: 480, intensity: 120, flags: '', pbs: '0;0', pbw: '50', pby: '0' },
  { id: '7', lyric: 'が', noteNum: 60, tick: 2880, length: 960, intensity: 120, flags: 'g-5', pbs: '0;0', pbw: '50', pby: '0' },
];

export default function App() {
  const [tempo, setTempo] = useState<number>(120);
  const [projectName, setProjectName] = useState<string>('VO-SE Song 1');
  const [tracks, setTracks] = useState<Track[]>([
    {
      id: 'track_1',
      name: 'Vocal 1',
      type: 'vocal',
      voicebank: '',
      notes: INITIAL_NOTES,
      volume: 0.8,
      isMuted: false,
      isSolo: false
    }
  ]);
  const [currentTrackId, setCurrentTrackId] = useState<string>('track_1');
  const [selectedNoteId, setSelectedNoteId] = useState<string | null>('1');
  
  const currentTrack = tracks.find(t => t.id === currentTrackId) || tracks[0];
  const notes = currentTrack?.type === 'vocal' ? currentTrack.notes : [];
  
  const setNotes = (updater: any) => {
    setTracks(prev => prev.map(t => {
      if (t.id === currentTrackId && t.type === 'vocal') {
        const newNotes = typeof updater === 'function' ? updater(t.notes) : updater;
        return { ...t, notes: newNotes };
      }
      return t;
    }));
  };

  const [showGhostNotes, setShowGhostNotes] = useState<boolean>(true);
  const [isBatchLyricModalOpen, setIsBatchLyricModalOpen] = useState<boolean>(false);
  const [isUstImportModalOpen, setIsUstImportModalOpen] = useState<boolean>(false);
  const projectFileInputRef = useRef<HTMLInputElement>(null);

  // Batch lyric apply handler
  const handleApplyBatchLyrics = (newLyrics: string[]) => {
    setTracks(prev => prev.map(t => {
      if (t.id === currentTrackId && t.type === 'vocal') {
        // Sort notes by tick to apply in order
        const sortedNotes = [...t.notes].sort((a, b) => a.tick - b.tick);
        const updatedSorted = sortedNotes.map((note, idx) => {
          if (idx < newLyrics.length) {
            return { ...note, lyric: newLyrics[idx] };
          }
          return note;
        });

        // Map back to maintain original order/structure with updated lyrics
        const updatedMap = new Map(updatedSorted.map(n => [n.id, n]));
        const newNotes = t.notes.map(n => updatedMap.get(n.id) || n);
        return { ...t, notes: newNotes };
      }
      return t;
    }));
  };

  // Track operations
  const handleAddTrack = (type: 'vocal' | 'wave') => {
    const newId = `track_${Date.now()}`;
    const newTrack: Track = {
      id: newId,
      name: type === 'vocal' ? `Vocal ${tracks.length + 1}` : `Wave ${tracks.length + 1}`,
      type,
      voicebank: '',
      notes: type === 'vocal' ? [{ id: '1', lyric: 'あ', noteNum: 60, tick: 0, length: 480, intensity: 120, flags: '', pbs: '0;0', pbw: '50', pby: '0' }] : [],
      volume: 0.8,
      isMuted: false,
      isSolo: false
    };
    setTracks(prev => [...prev, newTrack]);
    setCurrentTrackId(newId);
  };

  const handleDuplicateTrack = (trackId: string) => {
    const target = tracks.find(t => t.id === trackId);
    if (!target) return;
    const newId = `track_${Date.now()}`;
    const dup: Track = {
      ...target,
      id: newId,
      name: `${target.name} (コピー)`,
      notes: target.notes.map(n => ({ ...n, id: `${n.id}_dup_${Date.now()}` }))
    };
    setTracks(prev => [...prev, dup]);
    setCurrentTrackId(newId);
  };

  const handleDeleteTrack = (trackId: string) => {
    if (tracks.length <= 1) return;
    const nextTracks = tracks.filter(t => t.id !== trackId);
    setTracks(nextTracks);
    if (currentTrackId === trackId) {
      setCurrentTrackId(nextTracks[0].id);
    }
  };

  const handleUpdateTrack = (trackId: string, patch: Partial<Track>) => {
    setTracks(prev => prev.map(t => t.id === trackId ? { ...t, ...patch } : t));
  };
  const gridRef = useRef<HTMLDivElement>(null);
  const [clipboardNote, setClipboardNote] = useState<Note | null>(null);

  // ピアノロール グリッドの拡大縮小 (Zoom) 状態
  const [pianoRollZoomX, setPianoRollZoomX] = useState<number>(1.0); // 1.0 (100%) ~ 4.0 (400%)
  const [pianoRollRowHeight, setPianoRollRowHeight] = useState<number>(28); // 20px ~ 64px
  const keybedScrollRef = useRef<HTMLDivElement>(null);
  const gridScrollRef = useRef<HTMLDivElement>(null);
  const rulerScrollRef = useRef<HTMLDivElement>(null);
  const pianoRollTouchRef = useRef<{ dist: number; initialZoomX: number; initialRowHeight: number } | null>(null);

  // High performance DOM Playhead refs
  const gridPlayheadRef = useRef<HTMLDivElement>(null);
  const rulerPlayheadRef = useRef<HTMLDivElement>(null);
  const tickDisplayRef = useRef<HTMLSpanElement>(null);
  const lastTickStateUpdateRef = useRef<number>(0);
  const currentTickRef = useRef<number>(0);

  // Viewport Culling State for large songs
  const [visibleTickRange, setVisibleTickRange] = useState<{ startTick: number; endTick: number }>({
    startTick: 0,
    endTick: 3840,
  });
  const scrollUpdateRafRef = useRef<number | null>(null);

  // Playback state
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const [currentTick, setCurrentTick] = useState<number>(0);
  const playbackRef = useRef<number | null>(null);

  // ノート・ノードの位置に合わせてタイムラインの最大小節数を高速計算
  const totalMeasures = React.useMemo(() => {
    const DEFAULT_MEASURES = 8;
    const TICKS_PER_MEASURE = 480;
    let maxTick = DEFAULT_MEASURES * TICKS_PER_MEASURE;

    for (let i = 0; i < notes.length; i++) {
      const note = notes[i];
      const noteEnd = note.tick + (note.length || 480);
      if (noteEnd > maxTick) maxTick = noteEnd;
    }

    for (let t = 0; t < tracks.length; t++) {
      const tr = tracks[t];
      if (tr.notes) {
        for (let i = 0; i < tr.notes.length; i++) {
          const note = tr.notes[i];
          const noteEnd = note.tick + (note.length || 480);
          if (noteEnd > maxTick) maxTick = noteEnd;
        }
      }
    }

    const calculatedMeasures = Math.ceil((maxTick + TICKS_PER_MEASURE * 2) / TICKS_PER_MEASURE);
    return Math.max(DEFAULT_MEASURES, calculatedMeasures);
  }, [notes, tracks]);

  const totalTicks = totalMeasures * 480;

  const updateVisibleRangeFromScroll = useCallback(() => {
    const el = gridScrollRef.current;
    if (!el || totalTicks <= 0) return;
    const scrollLeft = el.scrollLeft;
    const clientWidth = el.clientWidth || 800;
    const scrollWidth = el.scrollWidth || clientWidth || 1;
    const ticksPerPx = totalTicks / scrollWidth;

    // Buffer 14 measures (14 * 1920 = 26880 ticks in 4/4 time) before and after visible window to prevent any pop-in or cutting off
    const BUFFER_TICKS = 26880;
    const startTick = Math.max(0, Math.round((scrollLeft * ticksPerPx) - BUFFER_TICKS));
    const endTick = Math.min(totalTicks, Math.round(((scrollLeft + clientWidth) * ticksPerPx) + BUFFER_TICKS));

    setVisibleTickRange((prev) => {
      if (Math.abs(prev.startTick - startTick) < 960 && Math.abs(prev.endTick - endTick) < 960) {
        return prev;
      }
      return { startTick, endTick };
    });
  }, [totalTicks]);

  useEffect(() => {
    updateVisibleRangeFromScroll();
  }, [totalTicks, pianoRollZoomX, updateVisibleRangeFromScroll]);

  const handlePianoRollScroll = (e: React.UIEvent<HTMLDivElement>) => {
    if (keybedScrollRef.current) {
      keybedScrollRef.current.scrollTop = e.currentTarget.scrollTop;
    }
    if (rulerScrollRef.current) {
      rulerScrollRef.current.scrollLeft = e.currentTarget.scrollLeft;
    }

    if (scrollUpdateRafRef.current === null) {
      scrollUpdateRafRef.current = requestAnimationFrame(() => {
        updateVisibleRangeFromScroll();
        scrollUpdateRafRef.current = null;
      });
    }
  };

  const scrollPianoRollHorizontal = (amount: number) => {
    if (gridScrollRef.current) {
      gridScrollRef.current.scrollBy({ left: amount, behavior: 'smooth' });
    }
  };

  const scrollPianoRollToStart = () => {
    if (gridScrollRef.current) {
      gridScrollRef.current.scrollTo({ left: 0, behavior: 'smooth' });
    }
  };

  const scrollPianoRollToPlayhead = () => {
    if (gridScrollRef.current) {
      const scrollWidth = gridScrollRef.current.scrollWidth;
      const targetLeft = (currentTick / totalTicks) * scrollWidth - gridScrollRef.current.clientWidth / 2;
      gridScrollRef.current.scrollTo({ left: Math.max(0, targetLeft), behavior: 'smooth' });
    }
  };

  const handlePianoRollTouchStart = (e: React.TouchEvent) => {
    if (e.touches.length === 2) {
      const t1 = e.touches[0];
      const t2 = e.touches[1];
      const dist = Math.hypot(t1.clientX - t2.clientX, t1.clientY - t2.clientY);
      pianoRollTouchRef.current = {
        dist,
        initialZoomX: pianoRollZoomX,
        initialRowHeight: pianoRollRowHeight,
      };
    }
  };

  const handlePianoRollTouchMove = (e: React.TouchEvent) => {
    if (e.touches.length === 2 && pianoRollTouchRef.current) {
      const t1 = e.touches[0];
      const t2 = e.touches[1];
      const currentDist = Math.hypot(t1.clientX - t2.clientX, t1.clientY - t2.clientY);
      const ratio = currentDist / pianoRollTouchRef.current.dist;

      const nextZoomX = Math.max(1.0, Math.min(4.0, Math.round(pianoRollTouchRef.current.initialZoomX * ratio * 10) / 10));
      const nextRowHeight = Math.max(20, Math.min(64, Math.round(pianoRollTouchRef.current.initialRowHeight * ratio)));

      setPianoRollZoomX(nextZoomX);
      setPianoRollRowHeight(nextRowHeight);
    }
  };

  const handlePianoRollTouchEnd = (e: React.TouchEvent) => {
    if (e.touches.length < 2) {
      pianoRollTouchRef.current = null;
    }
  };

  // Keyboard Shortcuts for Editor
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const activeTag = document.activeElement?.tagName;
      if (activeTag === 'INPUT' || activeTag === 'TEXTAREA' || activeTag === 'SELECT') {
        return;
      }
      
      if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selectedNoteId) {
          setNotes(prev => prev.filter(n => n.id !== selectedNoteId));
          setSelectedNoteId(null);
        }
      }

      if ((e.ctrlKey || e.metaKey) && e.key === 'c') {
        const noteToCopy = notes.find(n => n.id === selectedNoteId);
        if (noteToCopy) {
          setClipboardNote({ ...noteToCopy });
        }
      }

      if ((e.ctrlKey || e.metaKey) && e.key === 'v') {
        if (clipboardNote) {
          setNotes(prev => {
            const maxTick = prev.reduce((max, n) => Math.max(max, n.tick + n.length), 0);
            const newNote = {
              ...clipboardNote,
              id: String(Date.now()),
              tick: maxTick
            };
            setSelectedNoteId(newNote.id);
            return [...prev, newNote];
          });
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedNoteId, clipboardNote, notes]);

  // Voicebank State
  const selectedVoicebank = currentTrack?.voicebank || '';
  const setSelectedVoicebank = (vb: string) => {
    setTracks(prev => prev.map(t => t.id === currentTrackId ? { ...t, voicebank: vb } : t));
  };
  const [customVoicebanks, setCustomVoicebanks] = useState<
    { name: string; aliasCount: number; hasVcv: boolean; aliases: string[]; entries: any[] }[]
  >([]);
  const [selectedVbDetails, setSelectedVbDetails] = useState<{
    name: string;
    aliasCount: number;
    hasVcv: boolean;
    entries: {
      alias: string;
      filename: string;
      wav_path?: string;
      wav_exists?: boolean;
      left_blank: number;
      fixed_range: number;
      right_blank: number;
      preutterance: number;
      overlap: number;
    }[];
  } | null>(null);
  const [isLoadingDetails, setIsLoadingDetails] = useState<boolean>(false);
  const [playingAlias, setPlayingAlias] = useState<string | null>(null);

  const [isUploadingVb, setIsUploadingVb] = useState<boolean>(false);
  const [uploadProgress, setUploadProgress] = useState<number>(0);
  const [selectedAliasSearch, setSelectedAliasSearch] = useState<string>('');
  const [selectedOtoEntry, setSelectedOtoEntry] = useState<any | null>(null);

  // Upload Cancellation & Input Refs
  const uploadAbortControllerRef = useRef<AbortController | null>(null);
  const uploadXhrRef = useRef<XMLHttpRequest | null>(null);
  const currentUploadIdRef = useRef<string | null>(null);
  const isUploadCancelledRef = useRef<boolean>(false);
  const fileInputRef1 = useRef<HTMLInputElement | null>(null);
  const fileInputRef2 = useRef<HTMLInputElement | null>(null);

  // Toast Notification State
  const [toast, setToast] = useState<{ type: 'success' | 'error' | 'info'; title: string; desc: string } | null>(null);

  // System Diagnostic Status
  const [pyStatus, setPyStatus] = useState<PyStatus | null>(null);
  const [testResult, setTestResult] = useState<{ stdout: string; stderr: string; success: boolean } | null>(null);
  const [isRunningTests, setIsRunningTests] = useState<boolean>(false);
  const [isRenderingWav, setIsRenderingWav] = useState<boolean>(false);
  const [renderProgress, setRenderProgress] = useState<{
    pct: number;
    remainingSec: number | null;
    elapsedSec: number;
  } | null>(null);

  // 残り時間のフォーマット表示ヘルパー
  const formatEta = (seconds: number | null | undefined): string => {
    if (seconds === null || seconds === undefined || isNaN(seconds)) {
      return '計算中...';
    }
    if (seconds <= 0) {
      return 'まもなく完了';
    }
    if (seconds < 60) {
      return `約${seconds}秒`;
    }
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `約${mins}分${secs > 0 ? `${secs}秒` : ''}`;
  };

  // Oto Inspector State
  const [otoOffset, setOtoOffset] = useState<number>(15);
  const [otoOverlap, setOtoOverlap] = useState<number>(8);
  const [otoPreutterance, setOtoPreutterance] = useState<number>(25);
  const [otoBlank, setOtoBlank] = useState<number>(40);
  const [otoConsonant, setOtoConsonant] = useState<number>(100);

  // Active Tab
  const [activeTab, setActiveTab] = useState<'editor' | 'voicebanks' | 'oto' | 'tests' | 'desktop'>('editor');

  // Preset Voicebank Download & Delete State
  const [isDownloadingPreset, setIsDownloadingPreset] = useState<string | null>(null);
  const [vbSearchQuery, setVbSearchQuery] = useState<string>('');
  const [vbCategoryFilter, setVbCategoryFilter] = useState<'all' | 'official' | 'custom'>('all');

  // Drag and Drop State
  const [isDraggingFile, setIsDraggingFile] = useState<boolean>(false);

  
  const handleEngineRender = async () => {
    if (tracks.length === 0) return;
    
    const targetVb = currentTrack.voicebank || selectedVoicebank || (customVoicebanks.length > 0 ? customVoicebanks[0].name : '');
    if (!targetVb) {
      setToast({
        type: 'error',
        title: '音源が未設定です',
        desc: 'UTAU音源が登録されていません。右上の「UTAU音源(.zip) 追加」から音源ZIPをアップロードしてください。'
      });
      return;
    }

    if (!currentTrack.voicebank && targetVb) {
      setSelectedVoicebank(targetVb);
    }

    setIsRenderingWav(true);
    const startTime = performance.now();
    setRenderProgress({ pct: 0, remainingSec: null, elapsedSec: 0 });
    setToast({
      type: 'info',
      title: 'WASM合成中... 0% (計算中...)',
      desc: 'VO-SE Core WebAssemblyエンジンでWAVを合成しています...'
    });
    
    try {
      const audioUrl = await renderWasm(currentTrack.notes, tempo, targetVb, (pct: number) => {
        const elapsedSec = (performance.now() - startTime) / 1000;
        let remainingSec: number | null = null;
        if (pct > 2 && pct < 100) {
          remainingSec = Math.max(0, Math.round((elapsedSec / pct) * (100 - pct)));
        } else if (pct >= 100) {
          remainingSec = 0;
        }

        setRenderProgress({
          pct,
          remainingSec,
          elapsedSec: Math.round(elapsedSec)
        });

        const etaText = pct >= 100 ? 'まもなく完了' : `残り ${formatEta(remainingSec)}`;
        setToast({
          type: 'info',
          title: `WASM合成中... ${pct}% (${etaText})`,
          desc: 'VO-SE Core WebAssemblyエンジンでWAVを合成しています...'
        });
      });
      
      if (audioUrl) {
        const totalDurationSec = Math.round((performance.now() - startTime) / 1000);
        setToast({
          type: 'success',
          title: 'レンダリング完了',
          desc: `ブラウザ内のWASMエンジンで高品質合成が完了しました。(処理時間: ${totalDurationSec}秒)`
        });
        
        const audio = new Audio(audioUrl);
        audio.play();
      } else {
        throw new Error('合成エラー: 出力ファイルが生成されませんでした');
      }
    } catch (e: any) {
      setToast({
        type: 'error',
        title: 'レンダリング失敗',
        desc: e.message
      });
    } finally {
      setIsRenderingWav(false);
      setRenderProgress(null);
    }
  };

  const downloadPresetVoicebank = async (presetId: string, name: string, type: string) => {
    setIsDownloadingPreset(presetId);
    setToast({
      type: 'info',
      title: 'UTAU音源ダウンロード・構築中...',
      desc: `「${name}」の音源データおよび原音設定(oto.ini)をインストール中...`
    });
    try {
      const res = await fetch('/api/py/download-preset-voicebank', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ presetId, name, type })
      });
      const data = await res.json();
      if (data.success) {
        setToast({
          type: 'success',
          title: '音源インストールの完了',
          desc: `「${name}」をライブラリに追加しました！アクティブ音源として選択されました。`
        });
        await fetchVoicebanks();
        setSelectedVoicebank(name);
      } else {
        throw new Error(data.error || 'ダウンロードに失敗しました');
      }
    } catch (e: any) {
      setToast({
        type: 'error',
        title: 'ダウンロードエラー',
        desc: e.message || '音源のインストール中にエラーが発生しました。'
      });
    } finally {
      setIsDownloadingPreset(null);
    }
  };

  const deleteVoicebank = async (vbName: string) => {
    try {
      setToast({
        type: 'info',
        title: '音源削除中...',
        desc: `「${vbName}」をライブラリから削除しています...`
      });
      const res = await fetch(`/api/py/voicebanks?name=${encodeURIComponent(vbName)}`, {
        method: 'DELETE'
      });
      const data = await res.json();
      if (data.success) {
        setToast({
          type: 'success',
          title: '音源削除完了',
          desc: `「${vbName}」を削除しました。`
        });
        await fetchVoicebanks();
        if (selectedVoicebank === vbName) {
          const remaining = customVoicebanks.filter(v => v.name !== vbName);
          setSelectedVoicebank(remaining.length > 0 ? remaining[0].name : '');
        }
      } else {
        throw new Error(data.error || '削除失敗');
      }
    } catch (e: any) {
      setToast({
        type: 'error',
        title: '削除エラー',
        desc: e.message
      });
    }
  };

  // Web Audio Context & High-Precision Audio Lookahead Scheduler
  const audioCtxRef = useRef<AudioContext | null>(null);
  const masterGainRef = useRef<GainNode | null>(null);
  const masterLimiterRef = useRef<DynamicsCompressorNode | null>(null);

  const getOrCreateAudioContext = (): AudioContext => {
    if (!audioCtxRef.current) {
      audioCtxRef.current = new (window.AudioContext || (window as any).webkitAudioContext)();
    }
    const ctx = audioCtxRef.current;
    if (!masterGainRef.current || !masterLimiterRef.current) {
      const masterGain = ctx.createGain();
      masterGain.gain.setValueAtTime(0.85, ctx.currentTime);

      const limiter = ctx.createDynamicsCompressor();
      limiter.threshold.setValueAtTime(-1.0, ctx.currentTime); // -1.0 dBFS ceiling
      limiter.knee.setValueAtTime(3.0, ctx.currentTime);
      limiter.ratio.setValueAtTime(16.0, ctx.currentTime);
      limiter.attack.setValueAtTime(0.003, ctx.currentTime);
      limiter.release.setValueAtTime(0.050, ctx.currentTime);

      masterGain.connect(limiter);
      limiter.connect(ctx.destination);

      masterGainRef.current = masterGain;
      masterLimiterRef.current = limiter;
    }
    return ctx;
  };

  const sampleCacheRef = useRef<Map<string, {
    buffer: AudioBuffer;
    left_blank: number;
    fixed_range: number;
    right_blank: number;
    preutterance: number;
    overlap: number;
    baseMidi: number;
  } | null>>(new Map());
  const sampleInFlightRef = useRef<Map<string, Promise<any>>>(new Map());
  // 診断用: 今回の再生セッションでサンプル解決に失敗したエイリアスを記録する
  // (alias -> 発生回数)。devtoolsが使いにくい環境でも画面上のトーストで確認できるようにする。
  const unresolvedAliasesRef = useRef<Map<string, number>>(new Map());

  interface ActiveAudioNode {
    stop: () => void;
    disconnect: () => void;
  }
  const activeAudioNodesRef = useRef<Set<ActiveAudioNode>>(new Set());
  const playbackStartRef = useRef<{ audioStartCtxTime: number; audioStartTick: number } | null>(null);
  const trackSchedulePointersRef = useRef<Map<string, number>>(new Map());
  const sortedTrackNotesRef = useRef<Map<string, Note[]>>(new Map());
  const schedulerTimerRef = useRef<number | null>(null);
  const animFrameRef = useRef<number | null>(null);

  // Seamless seek / scrub handler without audio stutter
  const seekToTick = (targetTick: number) => {
    const newTick = Math.max(0, Math.min(totalTicks, targetTick));
    currentTickRef.current = newTick;

    if (isPlaying && audioCtxRef.current) {
      activeAudioNodesRef.current.forEach((node) => {
        try {
          node.stop();
          node.disconnect();
        } catch (e) {}
      });
      activeAudioNodesRef.current.clear();

      playbackStartRef.current = {
        audioStartCtxTime: audioCtxRef.current.currentTime,
        audioStartTick: newTick
      };

      sortedTrackNotesRef.current.forEach((sortedNotes, trackId) => {
        const idx = sortedNotes.findIndex(n => n.tick + (n.length || 480) >= newTick);
        trackSchedulePointersRef.current.set(trackId, idx >= 0 ? idx : sortedNotes.length);
      });
    }

    if (gridPlayheadRef.current) {
      gridPlayheadRef.current.style.left = `${(newTick / totalTicks) * 100}%`;
    }
    if (rulerPlayheadRef.current) {
      rulerPlayheadRef.current.style.left = `${(newTick / totalTicks) * 100 * pianoRollZoomX}%`;
    }
    if (tickDisplayRef.current) {
      tickDisplayRef.current.textContent = String(Math.round(newTick));
    }

    setCurrentTick(newTick);
  };

  // Fetch status and voicebanks on mount and when opening voicebanks tab
  useEffect(() => {
    fetchPyStatus();
    fetchVoicebanks();
  }, []);

  useEffect(() => {
    if (activeTab === 'voicebanks') {
      fetchVoicebanks();
    }
  }, [activeTab]);

  // Preload unique samples in bounded batches when voicebank changes or notes count changes
  useEffect(() => {
    sampleCacheRef.current.clear();
  }, [selectedVoicebank]);

  useEffect(() => {
    if (!selectedVoicebank) return;
    const uniqueKeys = new Map<string, { lyric: string; prevLyric?: string; noteNum?: number }>();
    notes.forEach((n, idx) => {
      if (isRestLyric(n.lyric)) return;
      const prevNote = idx > 0 ? notes[idx - 1] : null;
      const isContinuous = prevNote && (n.tick - (prevNote.tick + prevNote.length) <= 240);
      const prevLyric = isContinuous ? prevNote.lyric : undefined;
      const k = getSampleCacheKey(selectedVoicebank, n.lyric, prevLyric, n.noteNum);
      if (!uniqueKeys.has(k)) {
        uniqueKeys.set(k, { lyric: n.lyric, prevLyric, noteNum: n.noteNum });
      }
    });

    const items = Array.from(uniqueKeys.values()).slice(0, 30); // Preload initial set
    const BATCH_SIZE = 6;
    (async () => {
      for (let i = 0; i < items.length; i += BATCH_SIZE) {
        const batch = items.slice(i, i + BATCH_SIZE);
        await Promise.allSettled(
          batch.map(item => fetchAndCacheSample(selectedVoicebank, item.lyric, item.prevLyric, item.noteNum))
        );
      }
    })();
  }, [selectedVoicebank, notes.length]);

  // Stop active audio nodes cleanly when isPlaying turns false
  useEffect(() => {
    if (!isPlaying) {
      activeAudioNodesRef.current.forEach((node) => {
        try {
          node.stop();
          node.disconnect();
        } catch (e) {}
      });
      activeAudioNodesRef.current.clear();
    }
  }, [isPlaying]);

  const togglePlay = async () => {
    if (!isPlaying) {
      const ctx = getOrCreateAudioContext();
      if (ctx.state === 'suspended') {
        await ctx.resume();
      }

      // 診断用: 今回の再生セッション分をクリア
      unresolvedAliasesRef.current.clear();

      // Collect active tracks to play
      const hasSolo = tracks.some(t => t.isSolo);
      const activeTracks = hasSolo
        ? tracks.filter(t => t.isSolo && t.type === 'vocal' && t.notes.length > 0)
        : tracks.filter(t => !t.isMuted && t.type === 'vocal' && t.notes.length > 0);

      // Pre-sort notes for fast O(1) sequential lookahead scheduling
      sortedTrackNotesRef.current.clear();
      trackSchedulePointersRef.current.clear();

      const uniqueKeys = new Map<string, { vb: string; lyric: string; prevLyric?: string; noteNum?: number }>();

      // ★修正: 以前はここで曲全体(tick=0から末尾まで)を一括スキャンしてユニーク音節を
      // 集め、再生開始直後にバックグラウンドで「曲全体ぶん」を延々フェッチし続けていた。
      // これは scheduleAhead 側に既にある「再生位置から4秒先まで」の先読みプリフェッチと
      // 完全に重複しており、ブラウザの同時接続数を奪い合って本当に直近で必要な
      // フェッチが後回しになる（＝プレビューだけ詰まる）原因になっていた。
      // ここでは「再生開始位置(currentTick)から直後の数十ノート分」だけを対象にし、
      // それより先は scheduleAhead の継続的な先読みに任せる。
      const PRESTART_LOOKAHEAD_NOTES = 30;
      activeTracks.forEach(t => {
        const sorted = [...t.notes].sort((a, b) => a.tick - b.tick);
        sortedTrackNotesRef.current.set(t.id, sorted);
        const idx = sorted.findIndex(n => n.tick + (n.length || 480) >= currentTick);
        const startIdx = idx >= 0 ? idx : sorted.length;
        trackSchedulePointersRef.current.set(t.id, startIdx);

        const vb = t.voicebank || selectedVoicebank || (customVoicebanks.length > 0 ? customVoicebanks[0].name : '');
        const endIdx = Math.min(sorted.length, startIdx + PRESTART_LOOKAHEAD_NOTES);
        for (let i = startIdx; i < endIdx; i++) {
          const n = sorted[i];
          if (isRestLyric(n.lyric)) continue;
          const prevNote = i > 0 ? sorted[i - 1] : null;
          const isContinuous = prevNote && (n.tick - (prevNote.tick + prevNote.length) <= 240);
          const prevLyric = isContinuous ? prevNote.lyric : undefined;
          const k = getSampleCacheKey(vb, n.lyric, prevLyric, n.noteNum);
          if (!uniqueKeys.has(k)) {
            uniqueKeys.set(k, { vb, lyric: n.lyric, prevLyric, noteNum: n.noteNum });
          }
        }
      });

      // 再生開始位置直後の音だけ、確実に用意してから再生を始める
      const items = Array.from(uniqueKeys.values());
      const BATCH_SIZE = 8;
      const initialBatch = items.slice(0, BATCH_SIZE);
      await Promise.allSettled(
        initialBatch.map(item => fetchAndCacheSample(item.vb, item.lyric, item.prevLyric, item.noteNum))
      );

      // 初回バッチに入りきらなかった直近分は、再生を止めずにバックグラウンドで続きを取得
      // (曲全体ではなく、あくまで PRESTART_LOOKAHEAD_NOTES 分のみ。それ以降は
      //  scheduleAhead の4秒先読みプリフェッチが継続的に担当する)
      if (items.length > BATCH_SIZE) {
        (async () => {
          for (let i = BATCH_SIZE; i < items.length; i += BATCH_SIZE) {
            const batch = items.slice(i, i + BATCH_SIZE);
            await Promise.allSettled(
              batch.map(item => fetchAndCacheSample(item.vb, item.lyric, item.prevLyric, item.noteNum))
            );
          }
        })();
      }

      currentTickRef.current = currentTick;

      // Record audio start reference
      playbackStartRef.current = {
        audioStartCtxTime: audioCtxRef.current.currentTime,
        audioStartTick: currentTick
      };

      setIsPlaying(true);
    } else {
      setIsPlaying(false);
      setCurrentTick(currentTickRef.current);
      showUnresolvedAliasToastIfAny();
    }
  };

  // 診断用: 再生セッション中にサンプル解決へ失敗したエイリアスがあれば
  // 画面上のトーストで一覧表示する（devtoolsが使いにくい環境向け）
  const showUnresolvedAliasToastIfAny = () => {
    const failed = unresolvedAliasesRef.current;
    if (failed.size === 0) return;
    const entries: [string, number][] = Array.from(failed.entries()).sort((a: [string, number], b: [string, number]) => b[1] - a[1]);
    const totalHits = entries.reduce((sum, [, count]) => sum + count, 0);
    const preview = entries
      .slice(0, 8)
      .map(([alias, count]) => `${alias}${count > 1 ? `×${count}` : ''}`)
      .join(', ');
    const more = entries.length > 8 ? ` 他${entries.length - 8}種` : '';
    setToast({
      type: 'error',
      title: `未解決エイリアス: ${entries.length}種 / 計${totalHits}件`,
      desc: `解決できなかった歌詞: ${preview}${more}`
    });
  };

  const fetchVoicebanks = async () => {
    try {
      const res = await fetch('/api/py/voicebanks');
      const data = await res.json();
      if (data.success && Array.isArray(data.voicebanks)) {
        setCustomVoicebanks(data.voicebanks);
        if (data.voicebanks.length > 0) {
          // If current track has no voicebank or invalid one, auto-select the first available one
          setTracks(prev => prev.map(t => {
            if (!t.voicebank || !data.voicebanks.some((v: any) => v.name === t.voicebank)) {
              return { ...t, voicebank: data.voicebanks[0].name };
            }
            return t;
          }));
        } else {
          setTracks(prev => prev.map(t => ({ ...t, voicebank: '' })));
        }
      }
    } catch (e) {
      console.warn('Failed to load voicebanks:', e);
    }
  };

  const fetchVoicebankDetails = async (vbName: string, query: string = '') => {
    if (!vbName) {
      setSelectedVbDetails(null);
      return;
    }
    setIsLoadingDetails(true);
    try {
      const res = await fetch(`/api/py/voicebank-details?name=${encodeURIComponent(vbName)}&q=${encodeURIComponent(query)}&limit=300`);
      const data = await res.json();
      if (data.success) {
        setSelectedVbDetails(data);
        if (data.entries && data.entries.length > 0) {
          setSelectedOtoEntry(data.entries[0]);
          setOtoOffset(data.entries[0].left_blank || 15);
          setOtoOverlap(data.entries[0].overlap || 8);
          setOtoPreutterance(data.entries[0].preutterance || 25);
          setOtoBlank(data.entries[0].right_blank || 40);
          setOtoConsonant(data.entries[0].fixed_range || 100);
        }
      }
    } catch (e) {
      console.warn('Failed to load voicebank details:', e);
    } finally {
      setIsLoadingDetails(false);
    }
  };

  useEffect(() => {
    const timer = setTimeout(() => {
      fetchVoicebankDetails(selectedVoicebank, selectedAliasSearch);
    }, 200);
    return () => clearTimeout(timer);
  }, [selectedVoicebank, selectedAliasSearch]);

  const fetchAndCacheSample = async (vbName: string, alias: string, prevLyric?: string, noteNum?: number) => {
    if (!vbName || isRestLyric(alias)) return null;
    const cacheKey = getSampleCacheKey(vbName, alias, prevLyric, noteNum);
    if (sampleCacheRef.current.has(cacheKey)) {
      return sampleCacheRef.current.get(cacheKey) || null;
    }
    if (sampleInFlightRef.current.has(cacheKey)) {
      return await sampleInFlightRef.current.get(cacheKey);
    }

    if (!audioCtxRef.current) {
      audioCtxRef.current = new (window.AudioContext || (window as any).webkitAudioContext)();
    }
    const ctx = audioCtxRef.current;

    const fetchPromise = (async () => {
      try {
        let url = `/api/py/voicebank-sample?name=${encodeURIComponent(vbName)}&alias=${encodeURIComponent(alias)}`;
        if (prevLyric) {
          url += `&prevLyric=${encodeURIComponent(prevLyric)}`;
        }
        if (noteNum) {
          url += `&noteNum=${encodeURIComponent(String(noteNum))}`;
        }
        const res = await fetch(url);
        if (!res.ok) {
          sampleCacheRef.current.set(cacheKey, null);
          // 診断用: 解決失敗を記録する。ただし「っ」「ッ」（促音）は単独サンプルを
          // 持たない音源が多く、無音として扱われるのが仕様上正常なケースなので、
          // 実際に問題がある未解決エイリアスと区別するため診断対象から除外する。
          const isExpectedGlottalStop = alias === 'っ' || alias === 'ッ';
          if (!isExpectedGlottalStop) {
            unresolvedAliasesRef.current.set(
              alias,
              (unresolvedAliasesRef.current.get(alias) || 0) + 1
            );
          }
          return null;
        }

        const left_blank = parseFloat(res.headers.get('X-Oto-Left-Blank') || '0');
        const fixed_range = parseFloat(res.headers.get('X-Oto-Fixed-Range') || '0');
        const right_blank = parseFloat(res.headers.get('X-Oto-Right-Blank') || '0');
        const preutterance = parseFloat(res.headers.get('X-Oto-Preutterance') || '0');
        const overlap = parseFloat(res.headers.get('X-Oto-Overlap') || '0');
        const baseMidi = parseFloat(res.headers.get('X-Sample-Base-Midi') || '60');

        const arrayBuf = await res.arrayBuffer();
        const audioBuf = await ctx.decodeAudioData(arrayBuf);

        const item = {
          buffer: audioBuf,
          left_blank,
          fixed_range,
          right_blank,
          preutterance,
          overlap,
          baseMidi
        };

        sampleCacheRef.current.set(cacheKey, item);
        return item;
      } catch (e) {
        sampleCacheRef.current.set(cacheKey, null);
        return null;
      }
    })();

    sampleInFlightRef.current.set(cacheKey, fetchPromise);
    try {
      const res = await fetchPromise;
      return res;
    } finally {
      sampleInFlightRef.current.delete(cacheKey);
    }
  };

  const playSampleAudio = async (
    vbName: string,
    alias: string,
    pitchMidi = 60,
    durationSec = 1.0,
    isDirectPreview = false,
    intensity = 120,
    pbs?: string,
    pbw?: string,
    pby?: string,
    prevLyric?: string,
    startTimeCtx?: number
  ): Promise<boolean> => {
    if (isRestLyric(alias)) {
      if (isDirectPreview) setPlayingAlias(null);
      return false;
    }
    const ctx = getOrCreateAudioContext();
    if (ctx.state === 'suspended') await ctx.resume();

    if (isDirectPreview) {
      setPlayingAlias(alias);
    }

    try {
      const item = await fetchAndCacheSample(vbName, alias, prevLyric, pitchMidi);
      if (!item) {
        if (isDirectPreview) setPlayingAlias(null);
        return false;
      }

      const source = ctx.createBufferSource();
      source.buffer = item.buffer;

      // Pitch shift based on pitchMidi and sample base pitch
      const sampleBase = item.baseMidi || 60;
      const semitoneShift = softClampSemitone(pitchMidi - sampleBase);
      const baseRate = Math.min(4.0, Math.max(0.18, Math.pow(2, semitoneShift / 12)));
      const now = ctx.currentTime;
      const targetNoteTime = startTimeCtx !== undefined ? startTimeCtx : now;
      source.playbackRate.setValueAtTime(baseRate, Math.max(0, targetNoteTime));

      // Pitch bend curve (PBS / PBW / PBY) integration with soft limiting, slew-rate smoothing & formant anti-aliasing filter
      let formantFilter: BiquadFilterNode | null = null;
      if (pbs && pbw && pby) {
        try {
          const rawPoints = parsePitchBend(pbs, pbw, pby);
          const points = smoothPitchBendPoints(rawPoints);

          formantFilter = ctx.createBiquadFilter();
          formantFilter.type = 'lowpass';
          formantFilter.Q.setValueAtTime(0.707, Math.max(0, targetNoteTime));

          scheduleSafePitchRamp(
            source.playbackRate,
            baseRate,
            points,
            targetNoteTime,
            (st) => Math.max(0.18, Math.min(4.0, baseRate * Math.pow(2, st / 12))),
            ctx.currentTime,
            targetNoteTime + durationSec
          );

          scheduleSafePitchRamp(
            formantFilter.frequency,
            calculateFormantCutoff(sampleBase, 0),
            points,
            targetNoteTime,
            (st) => calculateFormantCutoff(sampleBase, st),
            ctx.currentTime,
            targetNoteTime + durationSec
          );
        } catch (e) {
          source.playbackRate.setValueAtTime(baseRate, Math.max(ctx.currentTime, targetNoteTime));
        }
      } else {
        source.playbackRate.setValueAtTime(baseRate, Math.max(ctx.currentTime, targetNoteTime));
      }

      // OTO parameters alignment
      const offsetSec = Math.max(0, (item.left_blank || 0) / 1000);
      const preuttSec = Math.max(0, (item.preutterance || 0) / 1000);
      const fixedSec = Math.max(0, (item.fixed_range || 0) / 1000);
      const effectivePreuttSec = preuttSec / baseRate;
      const wavDuration = item.buffer.duration;

      // UTAU standard Cutoff (right_blank):
      // - rb > 0: Cutoff distance from the end of the WAV file (in ms)
      // - rb < 0: Distance from offset (left_blank) with opposite sign (in ms)
      // - rb === 0: Whole remaining wav
      const rb = item.right_blank || 0;
      let cutoffEndSec = wavDuration;
      if (rb > 0) {
        cutoffEndSec = Math.max(offsetSec + 0.05, wavDuration - (rb / 1000));
      } else if (rb < 0) {
        cutoffEndSec = Math.max(offsetSec + 0.05, Math.min(wavDuration, offsetSec + Math.abs(rb) / 1000));
      }
      const maxSampleDur = Math.max(0.04, cutoffEndSec - offsetSec);

      let actualStartTime = now;
      let startOffsetInWav = offsetSec;
      let playLen = durationSec;

      if (isDirectPreview) {
        // Direct audition start immediately from vowel transition
        startOffsetInWav = Math.min(offsetSec + preuttSec, cutoffEndSec - 0.05);
        actualStartTime = now;
        playLen = durationSec;
      } else {
        const targetNoteTime = startTimeCtx !== undefined ? startTimeCtx : now;
        actualStartTime = Math.max(now, targetNoteTime - effectivePreuttSec);
        const timeDiff = actualStartTime - (targetNoteTime - effectivePreuttSec);
        startOffsetInWav = Math.min(offsetSec + timeDiff * baseRate, cutoffEndSec - 0.02);
        playLen = effectivePreuttSec + durationSec;
      }

      // Seamless looping over vowel body ONLY if requested duration exceeds max sample length
      const requiredSampleSec = (startOffsetInWav - offsetSec) + playLen * baseRate;
      if (requiredSampleSec > maxSampleDur + 0.02) {
        const loopStartSec = Math.min(cutoffEndSec - 0.06, offsetSec + Math.max(0.02, fixedSec || preuttSec || 0.05));
        const loopEndSec = Math.min(wavDuration - 0.01, Math.max(loopStartSec + 0.04, cutoffEndSec - 0.01));
        if (loopEndSec > loopStartSec + 0.03) {
          source.loop = true;
          source.loopStart = loopStartSec;
          source.loopEnd = loopEndSec;
        }
      }

      const gain = ctx.createGain();
      const volGain = Math.max(0.05, Math.min(1.5, (intensity || 120) / 120)) * 0.92;

      const tStart = Math.max(ctx.currentTime, actualStartTime);
      const tAttack = Math.max(tStart + 0.008, actualStartTime + (isDirectPreview ? 0.01 : 0.006));
      const tDecay = Math.max(tAttack + 0.005, actualStartTime + playLen - 0.015);
      const tEnd = tDecay + 0.02;

      gain.gain.setValueAtTime(0.0001, tStart);
      gain.gain.linearRampToValueAtTime(volGain, tAttack);
      gain.gain.setValueAtTime(volGain, tDecay);
      gain.gain.linearRampToValueAtTime(0.0001, tEnd);

      // Studio High-pass filter (80Hz) to prevent sub-rumble and preserve vocal clarity
      const hpf = ctx.createBiquadFilter();
      hpf.type = 'highpass';
      hpf.frequency.setValueAtTime(80, tStart);
      hpf.Q.setValueAtTime(0.707, tStart);

      source.connect(hpf);
      if (formantFilter) {
        hpf.connect(formantFilter);
        formantFilter.connect(gain);
      } else {
        hpf.connect(gain);
      }

      if (masterGainRef.current) {
        gain.connect(masterGainRef.current);
      } else {
        gain.connect(ctx.destination);
      }

      const activeNodeWrapper: ActiveAudioNode = {
        stop: () => {
          try { source.stop(); } catch (e) {}
        },
        disconnect: () => {
          try {
            source.disconnect();
            hpf.disconnect();
            if (formantFilter) formantFilter.disconnect();
            gain.disconnect();
          } catch (e) {}
        }
      };

      activeAudioNodesRef.current.add(activeNodeWrapper);
      source.onended = () => {
        activeAudioNodesRef.current.delete(activeNodeWrapper);
        if (isDirectPreview) setPlayingAlias(null);
        activeNodeWrapper.disconnect();
      };

      source.start(actualStartTime, startOffsetInWav);
      source.stop(tEnd + 0.01);
      return true;
    } catch (err) {
      if (isDirectPreview) setPlayingAlias(null);
      return false;
    }
  };

  // ★修正: バイト単位のstring連結(btoa+reduce)を廃止し、
  //         ブラウザネイティブのFileReader.readAsDataURLを使う。
  //         ネイティブ実装のためJS側でのループが発生せず、
  //         iPad Safari等のメモリ制約が厳しい環境でも
  //         「処理落ち」や応答なしを起こしにくい。
  const fileToBase64 = (file: File): Promise<string> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const result = reader.result as string; // "data:application/zip;base64,XXXX...."
        const commaIdx = result.indexOf(',');
        resolve(commaIdx >= 0 ? result.slice(commaIdx + 1) : result);
      };
      reader.onerror = () => reject(reader.error || new Error('FileReader failed'));
      reader.readAsDataURL(file);
    });
  };

  const handleCancelVoicebankUpload = () => {
    if (!isUploadingVb && !uploadAbortControllerRef.current && !uploadXhrRef.current) return;
    isUploadCancelledRef.current = true;

    // Abort Fetch / Chunks
    if (uploadAbortControllerRef.current) {
      try {
        uploadAbortControllerRef.current.abort();
      } catch (e) {}
      uploadAbortControllerRef.current = null;
    }

    // Abort XHR
    if (uploadXhrRef.current) {
      try {
        uploadXhrRef.current.abort();
      } catch (e) {}
      uploadXhrRef.current = null;
    }

    // Notify server to clean up partial chunks
    const uploadId = currentUploadIdRef.current;
    if (uploadId) {
      fetch(`/api/py/upload-voicebank-chunk?uploadId=${encodeURIComponent(uploadId)}`, {
        method: 'DELETE'
      }).catch(() => {});
      currentUploadIdRef.current = null;
    }

    setIsUploadingVb(false);
    setUploadProgress(0);

    if (fileInputRef1.current) fileInputRef1.current.value = '';
    if (fileInputRef2.current) fileInputRef2.current.value = '';

    setToast({
      type: 'info',
      title: 'アップロードをキャンセルしました',
      desc: '音源の送信・解析処理を中断しました。'
    });
  };

  const uploadVoicebankZipFile = async (file: File) => {
    const lowerName = file.name.toLowerCase();
    if (lowerName.endsWith('.html') || lowerName.endsWith('.htm')) {
      setToast({
        type: 'error',
        title: 'HTMLファイルです',
        desc: '選択されたファイルはWebページ（HTML）です。音源配布サイトから直接ZIP圧縮ファイル（.zip）をダウンロードして指定してください。'
      });
      return;
    }

    if (lowerName.endsWith('.rar') || lowerName.endsWith('.7z')) {
      setToast({
        type: 'error',
        title: '非対応の圧縮形式',
        desc: '.rar や .7z は非対応です。ZIP形式（.zip）の音源ファイルを指定してください。'
      });
      return;
    }

    isUploadCancelledRef.current = false;
    const abortController = new AbortController();
    uploadAbortControllerRef.current = abortController;

    setIsUploadingVb(true);
    setUploadProgress(5);
    setToast({
      type: 'info',
      title: '音源アップロード開始 (5%)',
      desc: `「${file.name}」(${Math.round(file.size / 1024 / 1024)}MB) を送信しています...`
    });

    try {
      // 1. Primary: Native multipart/form-data with XMLHttpRequest
      const uploadWithFormData = (): Promise<any> => {
        return new Promise((resolve, reject) => {
          const xhr = new XMLHttpRequest();
          uploadXhrRef.current = xhr;

          const formData = new FormData();
          formData.append('file', file, file.name);
          formData.append('filename', encodeURIComponent(file.name));

          xhr.open('POST', '/api/py/upload-voicebank-form');

          xhr.upload.onprogress = (e) => {
            if (e.lengthComputable && !isUploadCancelledRef.current) {
              const pct = Math.min(85, Math.round((e.loaded / e.total) * 85));
              setUploadProgress(pct);
              const loadedMb = (e.loaded / (1024 * 1024)).toFixed(1);
              const totalMb = (e.total / (1024 * 1024)).toFixed(1);
              if (e.loaded >= e.total) {
                setUploadProgress(88);
                setToast({
                  type: 'info',
                  title: '音源解凍・原音設定解析中 (88%)',
                  desc: 'ファイル送信完了。サーバーでZIPの展開およびoto.iniの解析を行っています...'
                });
              } else {
                setToast({
                  type: 'info',
                  title: `音源送信中 (${pct}%)`,
                  desc: `[${loadedMb}MB / ${totalMb}MB] データを転送しています...`
                });
              }
            }
          };

          xhr.onload = () => {
            uploadXhrRef.current = null;
            if (xhr.status >= 200 && xhr.status < 300) {
              try {
                const resJson = JSON.parse(xhr.responseText);
                resolve(resJson);
              } catch (err) {
                reject(new Error('サーバーの応答解析に失敗しました。'));
              }
            } else {
              try {
                const errJson = JSON.parse(xhr.responseText);
                reject(new Error(errJson.error || `アップロードエラー (${xhr.status})`));
              } catch (e) {
                reject(new Error(`アップロード通信エラー (${xhr.status})`));
              }
            }
          };

          xhr.onabort = () => {
            uploadXhrRef.current = null;
            reject(new Error('UPLOAD_CANCELLED'));
          };

          xhr.onerror = () => {
            uploadXhrRef.current = null;
            reject(new Error('ネットワーク通信が遮断されました。サーバー接続を確認してください。'));
          };

          xhr.send(formData);
        });
      };

      let json = await uploadWithFormData().catch((e) => {
        if (e.message === 'UPLOAD_CANCELLED' || isUploadCancelledRef.current || abortController.signal.aborted) {
          throw e;
        }
        console.warn('[VO-SE] FormData upload failed, attempting chunked fallback:', e?.message || e);
        return null;
      });

      // 2. Fallback: Chunked Upload for strict proxy environments
      if (!json || !json.success) {
        if (isUploadCancelledRef.current || abortController.signal.aborted) {
          throw new Error('UPLOAD_CANCELLED');
        }

        const uploadInChunks = async (): Promise<any> => {
          const CHUNK_SIZE = 2 * 1024 * 1024; // 2MB
          const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
          const uploadId = `up_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
          currentUploadIdRef.current = uploadId;

          let lastServerResult: any = null;

          for (let i = 0; i < totalChunks; i++) {
            if (isUploadCancelledRef.current || abortController.signal.aborted) {
              throw new Error('UPLOAD_CANCELLED');
            }

            const start = i * CHUNK_SIZE;
            const end = Math.min(file.size, start + CHUNK_SIZE);
            const chunkBlob = file.slice(start, end);

            const pct = Math.round(((i + 1) / totalChunks) * 80);
            setUploadProgress(pct);
            setToast({
              type: 'info',
              title: `音源ブロック送信中 (${pct}%)`,
              desc: `ブロック [${i + 1}/${totalChunks}]: ${Math.round(end / 1024 / 1024)}MB / ${Math.round(file.size / 1024 / 1024)}MB`
            });

            const res = await fetch(
              `/api/py/upload-voicebank-chunk?uploadId=${uploadId}&chunkIndex=${i}&totalChunks=${totalChunks}&filename=${encodeURIComponent(file.name)}`,
              {
                method: 'POST',
                headers: {
                  'Content-Type': 'application/octet-stream',
                  'X-Upload-Id': uploadId,
                  'X-Chunk-Index': String(i),
                  'X-Total-Chunks': String(totalChunks),
                  'X-Filename': encodeURIComponent(file.name)
                },
                body: chunkBlob,
                signal: abortController.signal
              }
            );

            if (isUploadCancelledRef.current || abortController.signal.aborted) {
              throw new Error('UPLOAD_CANCELLED');
            }

            if (!res.ok) {
              const errJson = await res.json().catch(() => ({}));
              throw new Error(errJson.error || `ブロック ${i + 1}/${totalChunks} の送信に失敗しました (${res.status})`);
            }

            lastServerResult = await res.json();
          }

          if (isUploadCancelledRef.current || abortController.signal.aborted) {
            throw new Error('UPLOAD_CANCELLED');
          }

          setUploadProgress(88);
          setToast({
            type: 'info',
            title: `音源解析・解凍中 (88%)`,
            desc: `全ブロック受信完了。サーバーで解凍および音源エイリアスをパース中...`
          });

          currentUploadIdRef.current = null;
          return lastServerResult;
        };

        json = await uploadInChunks().catch((e) => {
          if (e.message === 'UPLOAD_CANCELLED' || isUploadCancelledRef.current || abortController.signal.aborted) {
            throw e;
          }
          console.warn('[VO-SE] Chunked upload also failed:', e?.message || e);
          return null;
        });
      }

      if (json && json.success && json.data) {
        setUploadProgress(100);
        setToast({
          type: 'success',
          title: 'UTAU音源の読み込み完了 (100%)！',
          desc: `「${json.data.name}」を正常ロードしました (登録原音数: ${json.data.aliasCount}件)`
        });
        await fetchVoicebanks();
        setSelectedVoicebank(json.data.name);
        setTracks(prev => prev.map(t => t.id === currentTrackId ? { ...t, voicebank: json.data.name } : t));
      } else {
        setUploadProgress(0);
        setToast({
          type: 'error',
          title: '音源の読み込み失敗',
          desc: (json && json.error) || 'ZIP内に有効な oto.ini または WAV 音声が見つかりませんでした。'
        });
      }
    } catch (err: any) {
      if (err.name === 'AbortError' || err.message === 'UPLOAD_CANCELLED' || isUploadCancelledRef.current) {
        setUploadProgress(0);
      } else {
        setUploadProgress(0);
        setToast({
          type: 'error',
          title: '通信エラー',
          desc: err.message || '音源ZIPの送信・処理中に通信エラーが発生しました。'
        });
      }
    } finally {
      setIsUploadingVb(false);
      uploadAbortControllerRef.current = null;
      uploadXhrRef.current = null;
      currentUploadIdRef.current = null;
      setTimeout(() => {
        if (!isUploadingVb) setUploadProgress(0);
      }, 3000);
    }
  };

  const handleVoicebankZipUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      await uploadVoicebankZipFile(file);
    } finally {
      if (event.target) event.target.value = '';
    }
  };

  const fetchPyStatus = async () => {
    try {
      const res = await fetch('/api/py/status');
      const data = await res.json();
      if (data.success) {
        setPyStatus(data);
      }
    } catch (e) {
      console.warn('Backend Py API not responding:', e);
    }
  };

  const handleRunTests = async () => {
    setIsRunningTests(true);
    setTestResult(null);
    try {
      const res = await fetch('/api/py/run-tests');
      const data = await res.json();
      setTestResult(data);
    } catch (e: any) {
      setTestResult({
        success: false,
        stdout: '',
        stderr: `Failed to execute test runner: ${e.message}`
      });
    } finally {
      setIsRunningTests(false);
    }
  };

  // Apply parsed ProjectData (UST, VSQX, SVP, MIDI)
  const handleApplyProjectData = (pData: ProjectData, sourceName?: string, mode: 'replace' | 'new_track' = 'replace') => {
    if (!pData || !pData.notes || pData.notes.length === 0) {
      setToast({
        type: 'error',
        title: '読み込みエラー',
        desc: '有効な音符データが見つかりませんでした。USTファイルの内容・形式をご確認ください。'
      });
      return;
    }

    if (pData.tempo) setTempo(pData.tempo);
    if (pData.projectName) setProjectName(pData.projectName);

    const formattedNotes: Note[] = pData.notes.map((n, idx) => ({
      ...n,
      id: `note_${Date.now()}_${idx + 1}`
    }));

    // Resolve matching voicebank from installed customVoicebanks or fallback to active/default
    let resolvedVoicebank = selectedVoicebank;
    if (pData.voicebank) {
      const match = customVoicebanks.find(v => 
        v.name.toLowerCase() === pData.voicebank!.toLowerCase() ||
        pData.voicebank!.toLowerCase().includes(v.name.toLowerCase()) ||
        v.name.toLowerCase().includes(pData.voicebank!.toLowerCase())
      );
      if (match) {
        resolvedVoicebank = match.name;
      }
    }
    if (!resolvedVoicebank && customVoicebanks.length > 0) {
      resolvedVoicebank = customVoicebanks[0].name;
    }

    if (mode === 'new_track') {
      const newTrackId = `track_${Date.now()}`;
      const newTrack: Track = {
        id: newTrackId,
        name: (pData.projectName ? pData.projectName.slice(0, 20) : sourceName ? sourceName.replace(/\.[^/.]+$/, '').slice(0, 20) : `Vocal ${tracks.length + 1}`),
        type: 'vocal',
        voicebank: resolvedVoicebank,
        notes: formattedNotes,
        volume: 0.8,
        isMuted: false,
        isSolo: false
      };
      setTracks(prev => [...prev, newTrack]);
      setCurrentTrackId(newTrackId);
    } else {
      // Replace in current active vocal track or create first vocal track
      setTracks(prev => {
        let updated = false;
        const mapped = prev.map(t => {
          if (t.id === currentTrackId && t.type === 'vocal') {
            updated = true;
            return {
              ...t,
              name: pData.projectName ? pData.projectName.slice(0, 20) : t.name,
              voicebank: resolvedVoicebank || t.voicebank,
              notes: formattedNotes
            };
          }
          return t;
        });

        if (!updated) {
          const vocalIdx = mapped.findIndex(t => t.type === 'vocal');
          if (vocalIdx !== -1) {
            mapped[vocalIdx] = {
              ...mapped[vocalIdx],
              name: pData.projectName ? pData.projectName.slice(0, 20) : mapped[vocalIdx].name,
              voicebank: resolvedVoicebank || mapped[vocalIdx].voicebank,
              notes: formattedNotes
            };
            setCurrentTrackId(mapped[vocalIdx].id);
          } else {
            const newTrackId = `track_${Date.now()}`;
            mapped.push({
              id: newTrackId,
              name: pData.projectName ? pData.projectName.slice(0, 20) : 'Vocal 1',
              type: 'vocal',
              voicebank: resolvedVoicebank,
              notes: formattedNotes,
              volume: 0.8,
              isMuted: false,
              isSolo: false
            });
            setCurrentTrackId(newTrackId);
          }
        }
        return mapped;
      });
    }

    if (formattedNotes.length > 0) {
      setSelectedNoteId(formattedNotes[0].id);
    }

    setToast({
      type: 'success',
      title: 'UST / プロジェクト読み込み完了',
      desc: `「${sourceName || pData.projectName || 'USTファイル'}」を正常ロードしました (音符数: ${formattedNotes.length}音 / BPM: ${pData.tempo || tempo})`
    });
  };

  // Universal Project File Loader (.ust, .vsqx, .svp, .mid/.midi, etc.)
  const loadProjectFile = async (file: File) => {
    const fileName = file.name.toLowerCase();

    try {
      let pData: ProjectData | null = null;

      if (fileName.endsWith('.mid') || fileName.endsWith('.midi')) {
        const buffer = await file.arrayBuffer();
        pData = parseMidiBuffer(buffer);
      } else {
        // Use decodeTextBuffer to correctly handle UTF-8, Shift_JIS/CP932, and UTF-16
        const buffer = await file.arrayBuffer();
        const text = decodeTextBuffer(buffer);

        if (fileName.endsWith('.svp') || (text.trim().startsWith('{') && text.includes('tracks'))) {
          pData = parseSvpJson(text);
        } else if (fileName.endsWith('.vsqx') || text.includes('<vsq3>') || text.includes('<vsq4>') || text.includes('vocaloid')) {
          pData = parseVsqxXml(text);
        } else {
          // UST (or any text-based UST project)
          pData = parseUstText(text);
        }
      }

      if (pData && pData.notes && pData.notes.length > 0) {
        handleApplyProjectData(pData, file.name, 'replace');
      } else {
        setToast({
          type: 'error',
          title: '読み込みエラー',
          desc: 'ファイル内に有効な音符データが見つかりませんでした。USTファイルの内容・形式をご確認ください。'
        });
      }
    } catch (err: any) {
      setToast({
        type: 'error',
        title: '解析エラー',
        desc: `ファイルの読み込みに失敗しました: ${err.message}`
      });
    }
  };

  // Trigger Native File Picker with non-restrictive accept
  const handleTriggerProjectFileInput = () => {
    if (projectFileInputRef.current) {
      projectFileInputRef.current.value = '';
      projectFileInputRef.current.click();
    }
  };

  // Universal Project File Import Handler (.ust, .vsqx, .svp, .mid/.midi)
  const handleProjectFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      await loadProjectFile(file);
    } finally {
      if (event.target) event.target.value = '';
    }
  };

  // Universal Project Export Handler (.ust, .vsqx, .svp, .mid)
  const handleExportProject = (format: 'ust' | 'vsqx' | 'svp' | 'midi') => {
    if (notes.length === 0) {
      setToast({
        type: 'error',
        title: '書き出しエラー',
        desc: '書き出すノートが存在しません。'
      });
      return;
    }

    const pData: ProjectData = {
      projectName: projectName || 'VO-SE_Song',
      tempo,
      voicebank: selectedVoicebank,
      notes
    };

    const safeName = (projectName || 'VO-SE_Song').replace(/\s+/g, '_');

    try {
      if (format === 'ust') {
        const text = exportUstText(pData);
        const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
        downloadBlob(blob, `${safeName}.ust`);
      } else if (format === 'vsqx') {
        const xml = exportVsqxXml(pData);
        const blob = new Blob([xml], { type: 'text/xml;charset=utf-8' });
        downloadBlob(blob, `${safeName}.vsqx`);
      } else if (format === 'svp') {
        const json = exportSvpJson(pData);
        const blob = new Blob([json], { type: 'application/json;charset=utf-8' });
        downloadBlob(blob, `${safeName}.svp`);
      } else if (format === 'midi') {
        const buffer = exportMidiBuffer(pData);
        const blob = new Blob([buffer], { type: 'audio/midi' });
        downloadBlob(blob, `${safeName}.mid`);
      }

      setToast({
        type: 'success',
        title: '書き出し完了',
        desc: `${safeName}.${format === 'midi' ? 'mid' : format} ファイルを出力しました。`
      });
    } catch (err: any) {
      setToast({
        type: 'error',
        title: '書き出しエラー',
        desc: `ファイルの生成に失敗しました: ${err.message}`
      });
    }
  };

  const downloadBlob = (blob: Blob, filename: string) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.style.display = 'none';
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  // Export WAV Audio File (Real Voicebank WAV + High Quality Offline Rendering)
  const handleExportWav = async () => {
    let notesToRender: Note[] = [];
    if (tracks && tracks.length > 0) {
      tracks.forEach((t) => {
        if (!t.isMuted && t.type === 'vocal' && t.notes && t.notes.length > 0) {
          notesToRender.push(...t.notes);
        }
      });
    }
    if (notesToRender.length === 0) {
      notesToRender = notes;
    }

    if (notesToRender.length === 0) {
      alert('書き出すノートが存在しません。');
      return;
    }
    const targetVb = selectedVoicebank || (customVoicebanks.length > 0 ? customVoicebanks[0].name : '');
    if (!targetVb) {
      alert('UTAU音源が設定されていません。先にUTAU音源(.zip)をアップロードしてください。');
      return;
    }
    setIsRenderingWav(true);
    const startTime = performance.now();
    setRenderProgress({ pct: 0, remainingSec: null, elapsedSec: 0 });
    setToast({
      type: 'info',
      title: 'WAV書き出し中... 0% (計算中...)',
      desc: 'ノートと音源サンプルを処理し、高音質WAV音声を合成しています...'
    });
    try {
      const url = await renderWasm(notesToRender, tempo, targetVb, (pct: number) => {
        const elapsedSec = (performance.now() - startTime) / 1000;
        let remainingSec: number | null = null;
        if (pct > 2 && pct < 100) {
          remainingSec = Math.max(0, Math.round((elapsedSec / pct) * (100 - pct)));
        } else if (pct >= 100) {
          remainingSec = 0;
        }

        setRenderProgress({
          pct,
          remainingSec,
          elapsedSec: Math.round(elapsedSec)
        });

        const etaText = pct >= 100 ? 'まもなく完了' : `残り ${formatEta(remainingSec)}`;
        setToast({
          type: 'info',
          title: `WAV書き出し中... ${pct}% (${etaText})`,
          desc: 'ノートと音源サンプルを処理し、高音質WAV音声を合成しています...'
        });
      });
      if (url) {
        const totalDurationSec = Math.round((performance.now() - startTime) / 1000);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = `${projectName.replace(/\s+/g, '_')}_rendered.wav`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setToast({
          type: 'success',
          title: 'WAV書き出し完了',
          desc: `${projectName}_rendered.wav を書き出しました。(処理時間: ${totalDurationSec}秒)`
        });
      } else {
        throw new Error('音声データの生成に失敗しました。');
      }
    } catch (err: any) {
      alert('WAV音声書き出しに失敗しました: ' + err.message);
    } finally {
      setIsRenderingWav(false);
      setRenderProgress(null);
    }
  };

  // Core Note Vocal Audio Node Scheduler (WAV Voicebank with Pitch Bends & Formant Synth Fallback)
  // AudioBufferSourceNode.loop はサンプル単位のハードループで、クロスフェードを一切
  // 行わない。UTAU系音源のサステイン区間（母音の伸ばし部分）を単純にループすると、
  // loopStart/loopEnd がちょうど同じ位相・振幅で一致することはまず無いため、
  // ループ1周ごとに波形が不連続にジャンプ＝「ブツッ」というクリック音が鳴り続ける。
  // これを防ぐため、ループ終端の直前を、ループ始点直後の波形とイコールパワーで
  // ブレンドした専用バッファを作る。alias+loop位置ごとに一度だけ計算してキャッシュに
  // ぶら下げておく（毎ノート再計算しない）。共有キャッシュの元バッファ自体は書き換えない。
  const getLoopCrossfadedBuffer = (
    ctx: AudioContext,
    cached: any,
    loopStartSec: number,
    loopEndSec: number
  ): AudioBuffer => {
    const key = `${loopStartSec.toFixed(4)}_${loopEndSec.toFixed(4)}`;
    if (!cached._loopXfadeCache) {
      cached._loopXfadeCache = new Map<string, AudioBuffer>();
    }
    const existing = cached._loopXfadeCache.get(key);
    if (existing) return existing;

    const src: AudioBuffer = cached.buffer;
    const sr = src.sampleRate;
    const loopLenSec = Math.max(0.001, loopEndSec - loopStartSec);
    const xfadeSec = Math.min(0.015, loopLenSec * 0.25); // 最大15ms、ループ幅の25%まで
    const xfadeSamples = Math.max(1, Math.floor(xfadeSec * sr));
    const loopStartSample = Math.max(0, Math.floor(loopStartSec * sr));
    const loopEndSample = Math.min(src.length, Math.floor(loopEndSec * sr));

    const newBuffer = ctx.createBuffer(src.numberOfChannels, src.length, sr);
    for (let ch = 0; ch < src.numberOfChannels; ch++) {
      const srcData = src.getChannelData(ch);
      const dstData = newBuffer.getChannelData(ch);
      dstData.set(srcData);

      for (let i = 0; i < xfadeSamples; i++) {
        const tailIdx = loopEndSample - xfadeSamples + i;
        const headIdx = loopStartSample + i;
        if (tailIdx < 0 || tailIdx >= src.length || headIdx >= src.length) continue;
        const t = i / xfadeSamples;
        const fadeOut = Math.cos((t * Math.PI) / 2);
        const fadeIn = Math.sin((t * Math.PI) / 2);
        dstData[tailIdx] = srcData[tailIdx] * fadeOut + srcData[headIdx] * fadeIn;
      }
    }

    cached._loopXfadeCache.set(key, newBuffer);
    return newBuffer;
  };

  const scheduleVocalNoteNode = (
    ctx: AudioContext,
    targetVb: string,
    note: Note,
    noteStartCtxTime: number,
    trackVol: number = 1.0,
    prevLyric?: string
  ) => {
    if (isRestLyric(note.lyric)) {
      return;
    }
    const durSec = (note.length / 480) * (60 / tempo);
    const cacheKey = getSampleCacheKey(targetVb, note.lyric, prevLyric, note.noteNum);
    const cached = sampleCacheRef.current.get(cacheKey);

    if (cached && cached.buffer) {
      try {
        const source = ctx.createBufferSource();
        source.buffer = cached.buffer;

        const sampleBase = cached.baseMidi || 60;
        const semitoneShift = softClampSemitone(note.noteNum - sampleBase);
        const baseRate = Math.min(4.0, Math.max(0.18, Math.pow(2, semitoneShift / 12)));

        // Pitch bend curve (PBS / PBW / PBY) integration with soft limiting, slew-rate smoothing & formant anti-aliasing filter
        let formantFilter: BiquadFilterNode | null = null;
        if (note.pbs && note.pbw && note.pby) {
          try {
            const rawPoints = parsePitchBend(note.pbs, note.pbw, note.pby);
            const points = smoothPitchBendPoints(rawPoints);

            formantFilter = ctx.createBiquadFilter();
            formantFilter.type = 'lowpass';
            formantFilter.Q.setValueAtTime(0.707, Math.max(0, noteStartCtxTime));

            scheduleSafePitchRamp(
              source.playbackRate,
              baseRate,
              points,
              noteStartCtxTime,
              (st) => Math.max(0.18, Math.min(4.0, baseRate * Math.pow(2, st / 12))),
              ctx.currentTime,
              noteStartCtxTime + durSec
            );

            scheduleSafePitchRamp(
              formantFilter.frequency,
              calculateFormantCutoff(sampleBase, 0),
              points,
              noteStartCtxTime,
              (st) => calculateFormantCutoff(sampleBase, st),
              ctx.currentTime,
              noteStartCtxTime + durSec
            );
          } catch (e) {
            source.playbackRate.setValueAtTime(baseRate, Math.max(ctx.currentTime, noteStartCtxTime));
          }
        } else {
          source.playbackRate.setValueAtTime(baseRate, Math.max(ctx.currentTime, noteStartCtxTime));
        }

        const offsetSec = Math.max(0, (cached.left_blank || 0) / 1000);
        const preuttSec = Math.max(0, (cached.preutterance || 0) / 1000);
        const fixedSec = Math.max(0, (cached.fixed_range || 0) / 1000);
        const effectivePreuttSec = preuttSec / baseRate;
        const wavDuration = cached.buffer.duration;

        // UTAU standard Cutoff (right_blank):
        // - rb > 0: Cutoff distance from the end of the WAV file (in ms)
        // - rb < 0: Distance from offset (left_blank) with opposite sign (in ms)
        // - rb === 0: Whole remaining wav
        const rb = cached.right_blank || 0;
        let cutoffEndSec = wavDuration;
        if (rb > 0) {
          cutoffEndSec = Math.max(offsetSec + 0.05, wavDuration - (rb / 1000));
        } else if (rb < 0) {
          cutoffEndSec = Math.max(offsetSec + 0.05, Math.min(wavDuration, offsetSec + Math.abs(rb) / 1000));
        }
        const maxSampleDur = Math.max(0.04, cutoffEndSec - offsetSec);

        const actualStartTime = Math.max(ctx.currentTime, noteStartCtxTime - effectivePreuttSec);
        const timeDiff = actualStartTime - (noteStartCtxTime - effectivePreuttSec);
        const startOffsetInWav = Math.min(offsetSec + timeDiff * baseRate, cutoffEndSec - 0.02);
        const playLen = effectivePreuttSec + durSec;

        // Looping for extended notes within steady vowel region
        const requiredSampleSec = (startOffsetInWav - offsetSec) + playLen * baseRate;
        if (requiredSampleSec > maxSampleDur + 0.02) {
          const loopStartSec = Math.min(cutoffEndSec - 0.06, offsetSec + Math.max(0.02, fixedSec || preuttSec || 0.05));
          const loopEndSec = Math.min(wavDuration - 0.01, Math.max(loopStartSec + 0.04, cutoffEndSec - 0.01));
          if (loopEndSec > loopStartSec + 0.03) {
            source.loop = true;
            source.loopStart = loopStartSec;
            source.loopEnd = loopEndSec;
            // ループ境界のクリック音を消すため、クロスフェード済みバッファに差し替える
            try {
              source.buffer = getLoopCrossfadedBuffer(ctx, cached, loopStartSec, loopEndSec);
            } catch (e) {
              // 失敗しても元のバッファのまま続行（無音になるよりはクリック音の方がまし）
            }
          }
        }

        const gain = ctx.createGain();
        const volGain = Math.max(0.05, Math.min(1.5, (note.intensity || 120) / 120)) * 0.92 * Math.min(1.5, trackVol);

        const tStart = Math.max(ctx.currentTime, actualStartTime);

        // ★修正: 以前はアタック完了を常に "拍そのもの (noteStartCtxTime)" に固定していたため、
        // 次のノートが拍の時点で既にフルボリュームである一方、前のノートのフェードアウトが
        // 拍境界を最大10ms過ぎてから完了する設計になっていた。結果、拍境界の前後で
        // 隣接する2つの別音素が両方ともフルボリュームで同時に鳴る瞬間が生まれ、
        // 「ブツッ」という衝突音の原因になっていた。
        // oto.ini の Overlap（前のノートとクロスフェードする長さ）を使って、
        // アタックは実際の音声開始位置(tStart)からOverlap分だけで完了させる。
        const overlapSec = Math.max(0, (cached.overlap || 0) / 1000) / baseRate;
        const attackDur = Math.max(0.006, Math.min(0.03, overlapSec || 0.008));
        const tAttack = tStart + attackDur;

        // フェードアウトは必ずノート自身の終了時刻(noteStartCtxTime + durSec)までに
        // 完了させ、次のノートの領域へ音量が食い込まないようにする。
        const noteEndTime = noteStartCtxTime + durSec;
        const releaseDur = 0.015;
        const tDecay = Math.max(tAttack + 0.003, noteEndTime - releaseDur);
        const tEnd = Math.min(tDecay + releaseDur, noteEndTime);

        gain.gain.setValueAtTime(0.0001, tStart);
        gain.gain.linearRampToValueAtTime(volGain, tAttack);
        if (tDecay > tAttack + 0.002) {
          gain.gain.setValueAtTime(volGain, tDecay);
        }
        gain.gain.linearRampToValueAtTime(0.0001, tEnd);

        const hpf = ctx.createBiquadFilter();
        hpf.type = 'highpass';
        hpf.frequency.setValueAtTime(80, tStart);
        hpf.Q.setValueAtTime(0.707, tStart);

        source.connect(hpf);
        if (formantFilter) {
          hpf.connect(formantFilter);
          formantFilter.connect(gain);
        } else {
          hpf.connect(gain);
        }

        if (masterGainRef.current) {
          gain.connect(masterGainRef.current);
        } else {
          gain.connect(ctx.destination);
        }

        const activeNodeWrapper: ActiveAudioNode = {
          stop: () => {
            try { source.stop(); } catch (e) {}
          },
          disconnect: () => {
            try {
              source.disconnect();
              hpf.disconnect();
              if (formantFilter) formantFilter.disconnect();
              gain.disconnect();
            } catch (e) {}
          }
        };

        activeAudioNodesRef.current.add(activeNodeWrapper);
        source.onended = () => {
          activeAudioNodesRef.current.delete(activeNodeWrapper);
          activeNodeWrapper.disconnect();
        };

        const safeStartOffset = Math.max(0, Math.min(wavDuration - 0.02, startOffsetInWav));
        source.start(actualStartTime, safeStartOffset);
        source.stop(tEnd + 0.01);
        return;
      } catch (err) {
        console.warn('AudioBufferSource scheduling failed:', err);
      }
    }

    // Warm, click-free harmonic synthesizer fallback with smooth micro-fades and pitch bend automation
    try {
      const baseFreq = 440 * Math.pow(2, (note.noteNum - 69) / 12);
      let f1 = 500, f2 = 1500;
      const lyric = note.lyric || 'あ';
      if (lyric.includes('あ') || lyric.includes('a') || lyric.includes('か') || lyric.includes('た') || lyric.includes('さ')) {
        f1 = 800; f2 = 1250;
      } else if (lyric.includes('い') || lyric.includes('i') || lyric.includes('き') || lyric.includes('し')) {
        f1 = 300; f2 = 2300;
      } else if (lyric.includes('う') || lyric.includes('u') || lyric.includes('く') || lyric.includes('す')) {
        f1 = 350; f2 = 1200;
      } else if (lyric.includes('え') || lyric.includes('e') || lyric.includes('け') || lyric.includes('せ')) {
        f1 = 500; f2 = 1900;
      } else if (lyric.includes('お') || lyric.includes('o') || lyric.includes('こ') || lyric.includes('そ')) {
        f1 = 450; f2 = 800;
      }

      const osc = ctx.createOscillator();
      osc.type = 'sine';
      const osc2 = ctx.createOscillator();
      osc2.type = 'triangle';

      // Pitch bend support on oscillator fallback
      if (note.pbs && note.pbw && note.pby) {
        try {
          const rawPoints = parsePitchBend(note.pbs, note.pbw, note.pby);
          const points = smoothPitchBendPoints(rawPoints);

          scheduleSafePitchRamp(
            osc.frequency,
            baseFreq,
            points,
            noteStartCtxTime,
            (st) => Math.max(20, Math.min(20000, baseFreq * Math.pow(2, st / 12))),
            ctx.currentTime,
            noteStartCtxTime + durSec
          );

          scheduleSafePitchRamp(
            osc2.frequency,
            baseFreq * 2,
            points,
            noteStartCtxTime,
            (st) => Math.max(20, Math.min(20000, baseFreq * 2 * Math.pow(2, st / 12))),
            ctx.currentTime,
            noteStartCtxTime + durSec
          );
        } catch (e) {
          osc.frequency.setValueAtTime(baseFreq, Math.max(ctx.currentTime, noteStartCtxTime));
          osc2.frequency.setValueAtTime(baseFreq * 2, Math.max(ctx.currentTime, noteStartCtxTime));
        }
      } else {
        osc.frequency.setValueAtTime(baseFreq, Math.max(ctx.currentTime, noteStartCtxTime));
        osc2.frequency.setValueAtTime(baseFreq * 2, Math.max(ctx.currentTime, noteStartCtxTime));
      }

      const osc2Gain = ctx.createGain();
      osc2Gain.gain.setValueAtTime(0.25, noteStartCtxTime);
      osc2.connect(osc2Gain);

      const vibLfo = ctx.createOscillator();
      vibLfo.frequency.setValueAtTime(5.5, noteStartCtxTime);
      const vibGain = ctx.createGain();
      vibGain.gain.setValueAtTime(baseFreq * 0.012, noteStartCtxTime);
      vibLfo.connect(vibGain);
      vibGain.connect(osc.frequency);

      const filter1 = ctx.createBiquadFilter();
      filter1.type = 'bandpass';
      filter1.frequency.setValueAtTime(f1, noteStartCtxTime);
      filter1.Q.setValueAtTime(3.5, noteStartCtxTime);

      const filter2 = ctx.createBiquadFilter();
      filter2.type = 'bandpass';
      filter2.frequency.setValueAtTime(f2, noteStartCtxTime);
      filter2.Q.setValueAtTime(4.0, noteStartCtxTime);

      const gain = ctx.createGain();
      const volGain = Math.max(0.05, Math.min(1.5, (note.intensity || 120) / 120)) * 0.35 * Math.min(1.5, trackVol);
      
      const tStart = Math.max(ctx.currentTime, noteStartCtxTime);
      const tAttack = tStart + 0.012;
      const tDecay = Math.max(tAttack + 0.01, noteStartCtxTime + durSec - 0.02);
      const tEnd = tDecay + 0.025;

      gain.gain.setValueAtTime(0.0001, tStart);
      gain.gain.linearRampToValueAtTime(volGain, tAttack);
      gain.gain.setValueAtTime(volGain, tDecay);
      gain.gain.linearRampToValueAtTime(0.0001, tEnd);

      osc.connect(filter1);
      osc2Gain.connect(filter2);
      filter1.connect(gain);
      filter2.connect(gain);

      if (masterGainRef.current) {
        gain.connect(masterGainRef.current);
      } else {
        gain.connect(ctx.destination);
      }

      const activeNodeWrapper: ActiveAudioNode = {
        stop: () => {
          try {
            osc.stop();
            osc2.stop();
            vibLfo.stop();
          } catch (e) {}
        },
        disconnect: () => {
          try {
            osc.disconnect();
            osc2.disconnect();
            osc2Gain.disconnect();
            vibLfo.disconnect();
            vibGain.disconnect();
            filter1.disconnect();
            filter2.disconnect();
            gain.disconnect();
          } catch (e) {}
        }
      };

      activeAudioNodesRef.current.add(activeNodeWrapper);
      osc.onended = () => {
        activeAudioNodesRef.current.delete(activeNodeWrapper);
        activeNodeWrapper.disconnect();
      };

      osc.start(tStart);
      osc2.start(tStart);
      vibLfo.start(tStart + 0.05);
      vibLfo.stop(tEnd);
      osc.stop(tEnd);
      osc2.stop(tEnd);

      // Trigger background fetch for this phoneme
      fetchAndCacheSample(targetVb, note.lyric, prevLyric, note.noteNum);
    } catch (err) {
      console.warn('Harmonic synth fallback error:', err);
    }
  };

  // Direct Note Preview (e.g. click on piano roll or virtual keyboard)
  const playVocalNote = async (
    pitchMidi: number,
    lyric: string,
    durationSec: number = 0.4,
    intensity: number = 120,
    pbs?: string,
    pbw?: string,
    pby?: string,
    prevLyric?: string,
    startTimeCtx?: number
  ) => {
    if (isRestLyric(lyric)) return;
    const ctx = getOrCreateAudioContext();
    if (ctx.state === 'suspended') await ctx.resume();

    const targetTime = startTimeCtx !== undefined ? startTimeCtx : ctx.currentTime;
    const targetVb = selectedVoicebank || (customVoicebanks.length > 0 ? customVoicebanks[0].name : '');

    scheduleVocalNoteNode(
      ctx,
      targetVb,
      {
        id: 'audition',
        lyric,
        noteNum: pitchMidi,
        tick: 0,
        length: Math.round((durationSec / (60 / tempo)) * 480),
        intensity,
        flags: '',
        pbs: pbs || '0;0',
        pbw: pbw || '50',
        pby: pby || '0'
      },
      targetTime,
      1.0,
      prevLyric
    );
  };

  // High Efficiency Lookahead Scheduler + 60/120Hz Decoupled Playhead
  useEffect(() => {
    if (!isPlaying) {
      if (schedulerTimerRef.current !== null) {
        clearInterval(schedulerTimerRef.current);
        schedulerTimerRef.current = null;
      }
      if (animFrameRef.current !== null) {
        cancelAnimationFrame(animFrameRef.current);
        animFrameRef.current = null;
      }
      activeAudioNodesRef.current.forEach((node) => {
        try {
          node.stop();
          node.disconnect();
        } catch (e) {}
      });
      activeAudioNodesRef.current.clear();
      playbackStartRef.current = null;
      return;
    }

    if (!audioCtxRef.current || !playbackStartRef.current) return;
    const ctx = audioCtxRef.current;

    // Lookahead Scheduler Step (runs every 25ms looking ahead 350ms with 4.0s background sample prefetching)
    const scheduleAhead = () => {
      if (!playbackStartRef.current) return;
      const { audioStartCtxTime, audioStartTick } = playbackStartRef.current;
      const nowCtx = ctx.currentTime;
      const scheduleHorizonCtx = nowCtx + 0.35; // 350ms Web Audio node schedule horizon
      const prefetchHorizonCtx = nowCtx + 4.0;  // 4.0s background sample pre-fetch horizon

      sortedTrackNotesRef.current.forEach((sortedNotes, trackId) => {
        const track = tracks.find(t => t.id === trackId);
        if (!track) return;
        const targetVb = track.voicebank || selectedVoicebank || (customVoicebanks.length > 0 ? customVoicebanks[0].name : '');
        let ptr = trackSchedulePointersRef.current.get(trackId) ?? 0;

        // Background Pre-fetch upcoming samples for long songs
        for (let i = ptr; i < Math.min(sortedNotes.length, ptr + 20); i++) {
          const n = sortedNotes[i];
          const nTime = audioStartCtxTime + ((n.tick - audioStartTick) / 480) * (60 / tempo);
          if (nTime > prefetchHorizonCtx) break;
          if (isRestLyric(n.lyric)) continue;
          const prevN = i > 0 ? sortedNotes[i - 1] : null;
          const isCont = prevN && (n.tick - (prevN.tick + prevN.length) <= 240);
          const pLyric = isCont ? prevN.lyric : undefined;
          fetchAndCacheSample(targetVb, n.lyric, pLyric, n.noteNum);
        }

        // Schedule notes entering playback horizon
        while (ptr < sortedNotes.length) {
          const note = sortedNotes[ptr];
          const noteStartCtxTime = audioStartCtxTime + ((note.tick - audioStartTick) / 480) * (60 / tempo);

          if (noteStartCtxTime < nowCtx - 0.15) {
            ptr++;
            continue;
          }

          if (noteStartCtxTime <= scheduleHorizonCtx) {
            if (!isRestLyric(note.lyric)) {
              const prevNote = ptr > 0 ? sortedNotes[ptr - 1] : null;
              const isContinuous = prevNote && (note.tick - (prevNote.tick + prevNote.length) <= 240);
              const prevLyric = isContinuous ? prevNote.lyric : undefined;
              const cacheKey = getSampleCacheKey(targetVb, note.lyric, prevLyric, note.noteNum);
              const cached = sampleCacheRef.current.get(cacheKey);

              // 猶予期限: 予定時刻から1.2秒以上遅れてもまだキャッシュが来ないなら、
              // 曲全体をこれ以上止めないよう諦めてスキップする（安全弁）
              const graceDeadline = noteStartCtxTime + 1.2;

              if (cached && cached.buffer) {
                scheduleVocalNoteNode(
                  ctx,
                  targetVb,
                  note,
                  noteStartCtxTime,
                  track.volume ?? 0.8,
                  prevLyric
                );
                ptr++;
              } else if (nowCtx < graceDeadline) {
                // まだサンプル未取得 → フェッチをキックし、ptrは進めず次tick(25ms後)で再試行
                fetchAndCacheSample(targetVb, note.lyric, prevLyric, note.noteNum);
                break;
              } else {
                console.warn(`[Scheduler] サンプル取得タイムアウトでノートをスキップ: lyric='${note.lyric}'`);
                ptr++;
              }
            } else {
              ptr++;
            }
          } else {
            break;
          }
        }
        trackSchedulePointersRef.current.set(trackId, ptr);
      });
    };

    // Run scheduler immediately and on interval
    scheduleAhead();
    schedulerTimerRef.current = window.setInterval(scheduleAhead, 25);

    // RAF Playhead Animation Loop (Hardware Accelerated 60/120Hz)
    const renderPlayhead = () => {
      if (!playbackStartRef.current) return;
      const { audioStartCtxTime, audioStartTick } = playbackStartRef.current;
      const elapsedSec = ctx.currentTime - audioStartCtxTime;
      const calculatedTick = audioStartTick + (elapsedSec * (tempo * 480)) / 60;
      currentTickRef.current = calculatedTick;

      const songMaxTick = Math.max(
        totalTicks,
        notes.reduce((max, n) => Math.max(max, (n.tick || 0) + (n.length || 480)), 0) + 480
      );

      if (calculatedTick >= songMaxTick) {
        setIsPlaying(false);
        showUnresolvedAliasToastIfAny();
        activeAudioNodesRef.current.forEach((node) => {
          try {
            node.stop();
            node.disconnect();
          } catch (e) {}
        });
        activeAudioNodesRef.current.clear();
        setCurrentTick(0);
        currentTickRef.current = 0;
        if (gridPlayheadRef.current) gridPlayheadRef.current.style.left = '0%';
        if (rulerPlayheadRef.current) rulerPlayheadRef.current.style.left = '0%';
        if (tickDisplayRef.current) tickDisplayRef.current.textContent = '0';
        playbackStartRef.current = null;
        return;
      }

      // Direct GPU/DOM transforms without re-rendering React tree
      if (gridPlayheadRef.current) {
        gridPlayheadRef.current.style.left = `${(calculatedTick / totalTicks) * 100}%`;
      }
      if (rulerPlayheadRef.current) {
        rulerPlayheadRef.current.style.left = `${(calculatedTick / totalTicks) * 100 * pianoRollZoomX}%`;
      }
      if (tickDisplayRef.current) {
        tickDisplayRef.current.textContent = String(Math.round(calculatedTick));
      }

      // Throttle React state re-renders (~8Hz) so CPU is completely free for audio
      const now = performance.now();
      if (now - lastTickStateUpdateRef.current > 120) {
        lastTickStateUpdateRef.current = now;
        setCurrentTick(calculatedTick);
      }

      animFrameRef.current = requestAnimationFrame(renderPlayhead);
    };

    animFrameRef.current = requestAnimationFrame(renderPlayhead);

    return () => {
      if (schedulerTimerRef.current !== null) {
        clearInterval(schedulerTimerRef.current);
        schedulerTimerRef.current = null;
      }
      if (animFrameRef.current !== null) {
        cancelAnimationFrame(animFrameRef.current);
        animFrameRef.current = null;
      }
    };
  }, [isPlaying, tempo, tracks, totalTicks, pianoRollZoomX]);

  // Viewport culled notes for smooth 60fps rendering in large projects
  const visibleNotes = React.useMemo(() => {
    if (notes.length <= 40) return notes;
    const { startTick, endTick } = visibleTickRange;
    return notes.filter(
      (n) => n.id === selectedNoteId || ((n.tick + (n.length || 480)) >= startTick && n.tick <= endTick)
    );
  }, [notes, visibleTickRange, selectedNoteId]);

  const selectedNote = notes.find((n) => n.id === selectedNoteId);

  const updateSelectedNote = (field: keyof Note, value: any) => {
    if (!selectedNoteId) return;
    setNotes((prev) =>
      prev.map((n) => (n.id === selectedNoteId ? { ...n, [field]: value } : n))
    );
  };

  const addNote = () => {
    const maxTick = notes.reduce((max, n) => Math.max(max, n.tick + n.length), 0);
    const newNote: Note = {
      id: String(Date.now()),
      lyric: 'あ',
      noteNum: 60,
      tick: maxTick,
      length: 480,
      intensity: 120,
      flags: '',
      pbs: '0;0',
      pbw: '50',
      pby: '0'
    };
    setNotes([...notes, newNote]);
    setSelectedNoteId(newNote.id);
  };

  const deleteNote = (id: string) => {
    setNotes(notes.filter((n) => n.id !== id));
    if (selectedNoteId === id) setSelectedNoteId(null);
  };

  return (
    <div
      className="flex flex-col h-screen w-full bg-slate-950 text-slate-100 overflow-hidden select-none relative"
      onDragOver={(e) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDraggingFile(true);
      }}
      onDragLeave={(e) => {
        e.preventDefault();
        e.stopPropagation();
        // Check if leaving window
        if (e.currentTarget.contains(e.relatedTarget as Node)) return;
        setIsDraggingFile(false);
      }}
      onDrop={async (e) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDraggingFile(false);

        const file = e.dataTransfer.files?.[0];
        if (!file) return;

        const lowerName = file.name.toLowerCase();
        if (lowerName.endsWith('.zip')) {
          await uploadVoicebankZipFile(file);
        } else {
          await loadProjectFile(file);
        }
      }}
    >
      {/* Drag & Drop Visual Overlay */}
      {isDraggingFile && (
        <div className="absolute inset-0 z-50 bg-slate-950/85 backdrop-blur-sm border-2 border-dashed border-cyan-400 flex flex-col items-center justify-center p-8 pointer-events-none animate-in fade-in duration-150">
          <div className="w-16 h-16 rounded-2xl bg-cyan-500/20 border border-cyan-400/50 flex items-center justify-center mb-4 text-cyan-400 shadow-lg shadow-cyan-500/20">
            <Upload className="w-8 h-8 animate-bounce" />
          </div>
          <h3 className="text-xl font-bold text-slate-100 mb-2">ファイルをドロップしてインポート</h3>
          <p className="text-sm text-slate-400 text-center max-w-md">
            UST / VSQX / SVP / MIDI プロジェクトファイル、または UTAU音源ZIP (.zip) を自動認識して読み込みます
          </p>
        </div>
      )}

      {/* --- Top Navigation Header --- */}
      <header className="h-14 border-b border-slate-800 bg-slate-900/90 px-4 flex items-center justify-between shrink-0">
        <div className="flex items-center space-x-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-cyan-500 to-blue-600 flex items-center justify-center shadow-lg shadow-cyan-500/20">
            <Music className="w-5 h-5 text-white" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <h1 className="font-bold text-slate-100 tracking-wide text-base">VO-SE Pro Studio</h1>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-cyan-950 text-cyan-400 border border-cyan-800/50">
                v1.0.0
              </span>
            </div>
            <p className="text-xs text-slate-400">Vocal Synthesizer Engine & Editor</p>
          </div>
        </div>

        {/* Voicebank Selector / Active Status */}
        <div className="hidden sm:flex items-center space-x-2">
          <button
            onClick={() => setActiveTab('voicebanks')}
            className="flex items-center space-x-1.5 text-xs text-cyan-300 hover:text-white bg-slate-800/80 hover:bg-slate-700/80 px-3 py-1.5 rounded-lg border border-slate-700 transition cursor-pointer"
            title="UTAU音源ライブラリを開く"
          >
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" />
            <span className="text-slate-400">選択音源:</span>
            <span className="truncate max-w-[140px] font-semibold text-cyan-200">{selectedVoicebank}</span>
            <span className="text-[10px] bg-cyan-900/60 text-cyan-300 px-1.5 py-0.5 rounded font-mono border border-cyan-700/50">管理</span>
          </button>
        </div>

        {/* Right Toolbar Actions */}
        <div className="flex items-center space-x-2">
          {/* Universal Project Import */}
          <button
            onClick={() => setIsUstImportModalOpen(true)}
            className="flex items-center space-x-1.5 text-xs bg-slate-800 hover:bg-slate-700 text-cyan-300 hover:text-white px-3 py-1.5 rounded-md cursor-pointer transition border border-slate-700 shadow-sm"
            title="対応フォーマット: UST, VSQX, SVP, Standard MIDI"
          >
            <Upload className="w-3.5 h-3.5 text-cyan-400" />
            <span className="font-medium">インポート (UST/VSQX/SVP/MIDI)</span>
          </button>
          <input
            ref={projectFileInputRef}
            type="file"
            accept="*/*,.ust,.UST,.vsqx,.VSQX,.svp,.SVP,.mid,.MID,.midi,.MIDI,.txt,.TXT,text/plain,application/octet-stream"
            onChange={handleProjectFileUpload}
            className="hidden"
          />

          {/* UTAU Voicebank Zip Upload */}
          <label className="flex items-center space-x-1.5 text-xs bg-cyan-700 hover:bg-cyan-600 text-white font-medium px-3 py-1.5 rounded-md cursor-pointer transition border border-cyan-600 shadow-sm shadow-cyan-900/30">
            {isUploadingVb ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
            <span>{isUploadingVb ? '音源解凍中...' : 'UTAU音源(.zip) 追加'}</span>
            <input type="file" accept=".zip,application/zip,application/x-zip,application/x-zip-compressed,multipart/x-zip,application/octet-stream" onChange={handleVoicebankZipUpload} disabled={isUploadingVb} className="hidden" />
          </label>

          {/* Export Format Select & Download Button */}
          <div className="flex items-center space-x-1 bg-slate-800 border border-slate-700 rounded-md px-2 py-1">
            <Download className="w-3.5 h-3.5 text-slate-400 shrink-0" />
            <span className="text-[11px] text-slate-400 font-medium">書き出し:</span>
            <select
              onChange={(e) => {
                const val = e.target.value as 'ust' | 'vsqx' | 'svp' | 'midi';
                if (val) {
                  handleExportProject(val);
                  e.target.value = ''; // Reset select
                }
              }}
              defaultValue=""
              className="bg-slate-950 text-cyan-300 text-xs font-bold rounded px-1.5 py-0.5 border border-slate-700 cursor-pointer focus:outline-none focus:border-cyan-500"
            >
              <option value="" disabled>形式を選択...</option>
              <option value="ust">.ust (UTAU Project)</option>
              <option value="vsqx">.vsqx (VOCALOID 3/4)</option>
              <option value="svp">.svp (Synthesizer V)</option>
              <option value="midi">.mid (Standard MIDI)</option>
            </select>
          </div>

          <button
            onClick={handleExportWav}
            disabled={isRenderingWav}
            className="flex items-center space-x-1.5 text-xs bg-cyan-600 hover:bg-cyan-500 disabled:opacity-50 text-white font-medium px-3 py-1.5 rounded-md transition shadow-md shadow-cyan-600/20 font-sans"
            title="WAV音声ファイルをレンダリングしてダウンロードします"
          >
            {isRenderingWav ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
            ) : (
              <Download className="w-3.5 h-3.5 shrink-0" />
            )}
            <span>
              {isRenderingWav
                ? `WAV 書き出し中... ${renderProgress?.pct ?? 0}% (${
                    renderProgress?.remainingSec !== null && renderProgress?.remainingSec !== undefined
                      ? `残${formatEta(renderProgress.remainingSec)}`
                      : '計算中'
                  })`
                : 'WAV 音声書き出し'}
            </span>
          </button>
        </div>
      </header>

      {/* --- Main Workspace Layout --- */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left Sidebar Menu */}
        <div className="w-16 bg-slate-900 border-r border-slate-800 flex flex-col items-center py-4 space-y-4 shrink-0">
          <button
            onClick={() => setActiveTab('editor')}
            className={`p-2.5 rounded-xl transition flex flex-col items-center space-y-1 ${
              activeTab === 'editor' ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/30' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
            }`}
            title="Piano Roll Editor"
          >
            <Sliders className="w-5 h-5" />
            <span className="text-[10px]">エディタ</span>
          </button>

          <button
            onClick={() => setActiveTab('voicebanks')}
            className={`p-2.5 rounded-xl transition flex flex-col items-center space-y-1 relative ${
              activeTab === 'voicebanks' ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/30' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
            }`}
            title="UTAU Voicebanks Library"
          >
            <Library className="w-5 h-5" />
            <span className="text-[10px]">音源ライブラリ</span>
            {customVoicebanks.length > 0 && (
              <span className="absolute top-1 right-1 w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
            )}
          </button>

          <button
            onClick={() => setActiveTab('oto')}
            className={`p-2.5 rounded-xl transition flex flex-col items-center space-y-1 ${
              activeTab === 'oto' ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/30' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
            }`}
            title="Oto Database & Voicebank"
          >
            <Layers className="w-5 h-5" />
            <span className="text-[10px]">音源原音</span>
          </button>

          <button
            onClick={() => setActiveTab('tests')}
            className={`p-2.5 rounded-xl transition flex flex-col items-center space-y-1 ${
              activeTab === 'tests' ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/30' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
            }`}
            title="System Tests & Evaluation"
          >
            <Cpu className="w-5 h-5" />
            <span className="text-[10px]">テスト検証</span>
          </button>

          <button
            onClick={() => setActiveTab('desktop')}
            className={`p-2.5 rounded-xl transition flex flex-col items-center space-y-1 ${
              activeTab === 'desktop' ? 'bg-cyan-500/10 text-cyan-400 border border-cyan-500/30' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
            }`}
            title="PySide6 Desktop App Info"
          >
            <Monitor className="w-5 h-5" />
            <span className="text-[10px]">PySide6</span>
          </button>
        </div>

        {/* Central Active View Content */}
        <div className="flex-1 flex flex-col overflow-hidden bg-slate-950">
          {activeTab === 'editor' && (
            <div className="flex-1 flex flex-col overflow-hidden">
              {/* Multi-Track Mixer Panel */}
              <MultiTrackPanel
                tracks={tracks}
                currentTrackId={currentTrackId}
                onSelectTrack={setCurrentTrackId}
                onAddTrack={handleAddTrack}
                onDuplicateTrack={handleDuplicateTrack}
                onDeleteTrack={handleDeleteTrack}
                onUpdateTrack={handleUpdateTrack}
                showGhostNotes={showGhostNotes}
                setShowGhostNotes={setShowGhostNotes}
                customVoicebanks={customVoicebanks}
                onImportProject={() => setIsUstImportModalOpen(true)}
              />

              {/* Transport Control Bar */}
              <div className="h-12 bg-slate-900/60 border-b border-slate-800 px-4 flex items-center justify-between shrink-0">
                <div className="flex items-center space-x-3">
                  <button
                    onClick={togglePlay}
                    className={`w-9 h-9 rounded-full flex items-center justify-center transition shadow-md ${
                      isPlaying
                        ? 'bg-amber-500 hover:bg-amber-400 text-slate-950'
                        : 'bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-bold'
                    }`}
                  >
                    {isPlaying ? <Pause className="w-4 h-4 fill-current" /> : <Play className="w-4 h-4 fill-current ml-0.5" />}
                  </button>

                  <button
                    onClick={() => {
                      setIsPlaying(false);
                      seekToTick(0);
                    }}
                    className="w-8 h-8 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 flex items-center justify-center transition"
                  >
                    <Square className="w-3.5 h-3.5 fill-current" />
                  </button>

                  <div className="h-4 w-px bg-slate-800" />

                  {/* Tempo & Settings */}
                  <div className="flex items-center space-x-2 text-xs">
                    <span className="text-slate-400 font-medium">BPM:</span>
                    <input
                      type="number"
                      value={tempo}
                      onChange={(e) => setTempo(parseFloat(e.target.value) || 120)}
                      className="w-16 bg-slate-950 border border-slate-700 rounded px-2 py-1 text-cyan-300 font-mono text-center font-bold"
                    />
                  </div>

                  <div className="h-4 w-px bg-slate-800" />

                  <div className="flex items-center space-x-2 text-xs">
                    <span className="text-slate-400 font-medium">Voicebank:</span>
                    <select
                      value={selectedVoicebank}
                      onChange={(e) => setSelectedVoicebank(e.target.value)}
                      className="bg-slate-950 border border-slate-700 rounded px-2 py-1 text-slate-200 text-xs font-medium"
                    >
                      <option value="" disabled>音源を選択...</option>
                      {customVoicebanks.map((vb) => (
                        <option key={vb.name} value={vb.name}>
                          {vb.name} ({vb.aliasCount} エイリアス{vb.hasVcv ? ' / VCV' : ''})
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="flex items-center space-x-3">
                  {/* Piano Roll Zoom Controls */}
                  <div className="flex items-center space-x-2 bg-slate-950 border border-slate-800 rounded px-2 py-1 text-xs">
                    <span className="text-slate-400 text-[10px] font-bold">ロールズーム:</span>
                    <div className="flex items-center space-x-1">
                      <button
                        onClick={() => setPianoRollZoomX((prev) => Math.max(1.0, Math.round((prev - 0.25) * 100) / 100))}
                        disabled={pianoRollZoomX <= 1.0}
                        className="p-1 bg-slate-900 hover:bg-slate-800 text-slate-300 rounded disabled:opacity-30"
                        title="時間軸を縮小"
                      >
                        <ZoomOut className="w-3 h-3" />
                      </button>
                      <span className="font-mono text-cyan-400 font-bold min-w-[36px] text-center text-[11px]">
                        {Math.round(pianoRollZoomX * 100)}%
                      </span>
                      <button
                        onClick={() => setPianoRollZoomX((prev) => Math.min(4.0, Math.round((prev + 0.25) * 100) / 100))}
                        disabled={pianoRollZoomX >= 4.0}
                        className="p-1 bg-slate-900 hover:bg-slate-800 text-slate-300 rounded disabled:opacity-30"
                        title="時間軸を拡大"
                      >
                        <ZoomIn className="w-3 h-3" />
                      </button>
                    </div>

                    <div className="w-px h-3 bg-slate-800" />

                    <div className="flex items-center space-x-1">
                      <span className="text-slate-500 text-[10px]">鍵盤高:</span>
                      <button
                        onClick={() => setPianoRollRowHeight((prev) => Math.max(20, prev - 4))}
                        disabled={pianoRollRowHeight <= 20}
                        className="p-1 bg-slate-900 hover:bg-slate-800 text-slate-300 rounded text-[10px] font-bold disabled:opacity-30"
                        title="鍵盤高さを縮小"
                      >
                        -
                      </button>
                      <span className="font-mono text-cyan-400 font-bold min-w-[28px] text-center text-[11px]">
                        {pianoRollRowHeight}px
                      </span>
                      <button
                        onClick={() => setPianoRollRowHeight((prev) => Math.min(64, prev + 4))}
                        disabled={pianoRollRowHeight >= 64}
                        className="p-1 bg-slate-900 hover:bg-slate-800 text-slate-300 rounded text-[10px] font-bold disabled:opacity-30"
                        title="鍵盤高さを拡大"
                      >
                        +
                      </button>
                    </div>

                    <button
                      onClick={() => {
                        setPianoRollZoomX(1.0);
                        setPianoRollRowHeight(28);
                      }}
                      className="p-1 bg-slate-900 hover:bg-slate-800 text-slate-400 hover:text-slate-200 rounded"
                      title="ズームリセット"
                    >
                      <RotateCcw className="w-3 h-3" />
                    </button>

                    <div className="w-px h-3 bg-slate-800" />

                    {/* Horizontal Scroll Quick Buttons */}
                    <div className="flex items-center space-x-1">
                      <span className="text-slate-500 text-[10px] hidden sm:inline">横移動:</span>
                      <button
                        onClick={scrollPianoRollToStart}
                        className="px-1.5 py-0.5 bg-slate-900 hover:bg-slate-800 text-slate-300 rounded text-[10px] font-mono"
                        title="曲頭へスクロール"
                      >
                        ◀◀
                      </button>
                      <button
                        onClick={() => scrollPianoRollHorizontal(-300)}
                        className="px-1.5 py-0.5 bg-slate-900 hover:bg-slate-800 text-slate-300 rounded text-[10px] font-mono"
                        title="左へスクロール"
                      >
                        ◀
                      </button>
                      <button
                        onClick={() => scrollPianoRollHorizontal(300)}
                        className="px-1.5 py-0.5 bg-slate-900 hover:bg-slate-800 text-slate-300 rounded text-[10px] font-mono"
                        title="右へスクロール"
                      >
                        ▶
                      </button>
                      <button
                        onClick={scrollPianoRollToPlayhead}
                        className="px-1.5 py-0.5 bg-cyan-950 hover:bg-cyan-900 border border-cyan-800 text-cyan-300 rounded text-[10px]"
                        title="再生バー位置へスクロール"
                      >
                        📍
                      </button>
                    </div>
                  </div>

                  <div className="flex items-center space-x-1.5">
                    <button
                      onClick={() => setIsUstImportModalOpen(true)}
                      className="flex items-center space-x-1 text-xs bg-slate-800 hover:bg-cyan-950/80 text-cyan-300 hover:text-cyan-200 px-2.5 py-1.5 rounded border border-slate-700 hover:border-cyan-600/60 transition shadow-sm"
                      title="UST / VSQX / SVP / MIDI プロジェクトを読み込み"
                    >
                      <Upload className="w-3.5 h-3.5 text-cyan-400" />
                      <span>UST読込</span>
                    </button>
                    <button
                      onClick={() => setIsBatchLyricModalOpen(true)}
                      className="flex items-center space-x-1 text-xs bg-slate-800 hover:bg-cyan-950/80 text-cyan-300 hover:text-cyan-200 px-2.5 py-1.5 rounded border border-slate-700 hover:border-cyan-600/60 transition shadow-sm"
                      title="トラック内の全ノートに歌詞を一括で流し込みます"
                    >
                      <Type className="w-3.5 h-3.5 text-cyan-400" />
                      <span>歌詞一括入力</span>
                    </button>
                    <button
                      onClick={addNote}
                      className="flex items-center space-x-1 text-xs bg-slate-800 hover:bg-slate-700 text-cyan-300 px-2.5 py-1.5 rounded border border-slate-700 transition"
                    >
                      <Plus className="w-3.5 h-3.5" />
                      <span>ノート追加</span>
                    </button>
                  </div>
                  <div className="text-xs text-slate-500 font-mono">
                    Tick: <span ref={tickDisplayRef} className="text-slate-300 font-bold">{Math.round(currentTick)}</span> / {totalTicks}
                  </div>
                </div>
              </div>

              {/* Piano Roll Workspace Canvas */}
              <div className="flex-1 flex overflow-hidden">
                {/* Left Keybed Column */}
                <div className="w-20 bg-slate-900 border-r border-slate-800 flex flex-col shrink-0 select-none">
                  <div className="h-7 border-b border-slate-800 bg-slate-950 text-[10px] text-slate-500 flex items-center justify-center font-mono shrink-0">
                    Measure
                  </div>
                  <div className="flex-1 overflow-y-auto flex flex-col" ref={keybedScrollRef}>
                      {Array.from({ length: 37 }).map((_, i) => {
                        const midiNum = 84 - i; // C6 (84) down to C3 (48)
                        const isBlack = isBlackKey(midiNum);
                        return (
                          <div
                            key={midiNum}
                            onClick={() => playVocalNote(midiNum, selectedNote?.lyric || 'あ', 0.5)}
                            onTouchStart={() => playVocalNote(midiNum, selectedNote?.lyric || 'あ', 0.5)}
                            style={{ height: `${pianoRollRowHeight}px` }}
                            className={`border-b flex items-center justify-between px-2 text-[10px] font-mono cursor-pointer transition select-none active:bg-cyan-600 shrink-0 ${
                              isBlack
                                ? 'bg-slate-950 text-slate-400 border-slate-900 hover:bg-slate-800'
                                : 'bg-slate-800 text-slate-200 border-slate-700 hover:bg-slate-700'
                            }`}
                          >
                            <span>{getNoteName(midiNum)}</span>
                            <span className="text-[9px] opacity-40">{midiNum}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* Center Timeline & Grid Canvas Column */}
                  <div className="flex-1 flex flex-col overflow-hidden relative min-w-0">
                    {/* Timeline Ruler Header Bar */}
                    <div
                      ref={rulerScrollRef}
                      className="h-7 bg-slate-900 border-b border-slate-800 relative cursor-pointer overflow-x-auto overflow-y-hidden scrollbar-none flex items-center shrink-0 select-none"
                      onClick={(e) => {
                        const rect = e.currentTarget.getBoundingClientRect();
                        const clickX = e.clientX - rect.left;
                        const pct = Math.max(0, Math.min(1, clickX / rect.width));
                        seekToTick(pct * totalTicks);
                      }}
                    >
                      {/* Ruler measure markers */}
                      <div
                        className="absolute inset-0 flex h-full"
                        style={{
                          width: `${Math.round(pianoRollZoomX * 100)}%`,
                          minWidth: '1000px',
                        }}
                      >
                        {Array.from({ length: totalMeasures }).map((_, mIdx) => {
                          const mStartTick = mIdx * 480;
                          const mEndTick = mStartTick + 480;
                          const isMeasureVisible = totalMeasures <= 60 || (mEndTick >= visibleTickRange.startTick && mStartTick <= visibleTickRange.endTick);
                          return (
                            <div key={mIdx} className="flex-1 border-r border-slate-700/60 flex items-center justify-between px-1 text-[10px] text-slate-400 font-mono">
                              {isMeasureVisible ? (
                                <>
                                  <span className="font-bold text-cyan-400">{mIdx + 1}</span>
                                  <span className="text-[9px] text-slate-600">.</span>
                                  <span className="text-[9px] text-slate-600">.</span>
                                  <span className="text-[9px] text-slate-600">.</span>
                                </>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>

                      {/* Ruler Playhead Handle */}
                      <div
                        ref={rulerPlayheadRef}
                        className="absolute top-0 bottom-0 w-0.5 bg-red-500 z-20 pointer-events-none"
                        style={{ left: `${(currentTick / totalTicks) * 100 * pianoRollZoomX}%` }}
                      >
                        <div className="w-3 h-3 bg-red-500 rounded-b -ml-[5px] shadow flex items-center justify-center">
                          <div className="w-1 h-1 bg-white rounded-full" />
                        </div>
                      </div>
                    </div>

                    {/* Grid Timeline Canvas */}
                    <div
                      ref={gridScrollRef}
                      onScroll={handlePianoRollScroll}
                      onTouchStart={handlePianoRollTouchStart}
                      onTouchMove={handlePianoRollTouchMove}
                      onTouchEnd={handlePianoRollTouchEnd}
                      className="flex-1 relative overflow-auto bg-slate-950 touch-grid no-scroll-chain"
                      onClick={(e) => {
                        // Check if click was on grid background (not on a note)
                        if (e.target === e.currentTarget || (e.target as HTMLElement).classList.contains('border-r')) {
                          const rect = e.currentTarget.getBoundingClientRect();
                          const clickX = e.clientX - rect.left;
                          const pct = Math.max(0, Math.min(1, clickX / rect.width));
                          seekToTick(pct * totalTicks);
                        }
                      }}
                    >
                      {/* Grid wrapper for zoom scaling */}
                      <div
                        style={{
                          width: `${Math.round(pianoRollZoomX * 100)}%`,
                          minWidth: '1000px',
                          height: `${37 * pianoRollRowHeight}px`,
                        }}
                        className="relative cursor-crosshair"
                        ref={gridRef}
                        onDoubleClick={(e) => {
                          if (!gridRef.current) return;
                          const rect = gridRef.current.getBoundingClientRect();
                          const clickX = e.clientX - rect.left;
                          const clickY = e.clientY - rect.top;

                          const pct = Math.max(0, Math.min(1, clickX / rect.width));
                          const rawTick = pct * totalTicks;
                          const tick = Math.round(rawTick / 240) * 240;

                          const rowIdx = Math.floor(clickY / pianoRollRowHeight);
                          const noteNum = Math.max(48, Math.min(84, 84 - rowIdx));

                          const newNote: Note = {
                            id: String(Date.now()),
                            lyric: 'あ',
                            noteNum,
                            tick,
                            length: 480,
                            intensity: 120,
                            flags: '',
                            pbs: '0;0',
                            pbw: '50',
                            pby: '0',
                          };
                          setNotes((prev) => [...prev, newNote]);
                          setSelectedNoteId(newNote.id);
                          playVocalNote(noteNum, 'あ', 0.4);
                        }}
                      >
                        {/* Playhead indicator bar */}
                        <div
                          ref={gridPlayheadRef}
                          className="absolute top-0 bottom-0 w-0.5 bg-red-500 z-30 pointer-events-none shadow-sm shadow-red-500"
                          style={{
                            left: `${(currentTick / totalTicks) * 100}%`
                          }}
                        >
                          <div className="w-2.5 h-2.5 bg-red-500 rounded-full -ml-[4px] -mt-1 shadow" />
                        </div>

                        {/* Grid lines background */}
                        <div className="absolute inset-0 flex">
                          {Array.from({ length: totalMeasures }).map((_, bIdx) => (
                            <div key={bIdx} className="flex-1 border-r border-slate-800/80 flex">
                              <div className="flex-1 border-r border-slate-900/40" />
                              <div className="flex-1 border-r border-slate-900/40" />
                              <div className="flex-1 border-r border-slate-900/40" />
                            </div>
                          ))}
                        </div>

                        {/* Note Blocks (Viewport culled) */}
                        {visibleNotes.map((note) => {
                          const rowIdx = 84 - note.noteNum;
                          const topPos = rowIdx * pianoRollRowHeight;
                          const leftPct = (note.tick / totalTicks) * 100;
                          const widthPct = (note.length / totalTicks) * 100;
                          const isSelected = note.id === selectedNoteId;

                          const handleNotePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
                            e.stopPropagation();
                            if ((e.target as HTMLElement).classList.contains('resize-handle')) {
                              return; // Handled by resize logic
                            }
                            
                            setSelectedNoteId(note.id);
                            (e.target as HTMLElement).setPointerCapture(e.pointerId);
                            playVocalNote(note.noteNum, note.lyric, 0.4);

                            const startX = e.clientX;
                            const startY = e.clientY;
                            const startTick = note.tick;
                            const startNoteNum = note.noteNum;

                            const onPointerMove = (moveEvent: PointerEvent) => {
                              if (!gridRef.current) return;
                              const rect = gridRef.current.getBoundingClientRect();
                              const deltaX = moveEvent.clientX - startX;
                              const deltaY = moveEvent.clientY - startY;

                              const ticksPerPx = totalTicks / rect.width;
                              let newTick = Math.max(0, startTick + deltaX * ticksPerPx);
                              newTick = Math.round(newTick / 60) * 60; // Snap to 32nd notes

                              const noteDelta = Math.round(deltaY / pianoRollRowHeight);
                              const newNoteNum = Math.min(84, Math.max(48, startNoteNum - noteDelta));

                              setNotes(prev => prev.map(n => n.id === note.id ? { ...n, tick: newTick, noteNum: newNoteNum } : n));
                            };

                            const onPointerUp = () => {
                              window.removeEventListener('pointermove', onPointerMove);
                              window.removeEventListener('pointerup', onPointerUp);
                            };

                            window.addEventListener('pointermove', onPointerMove);
                            window.addEventListener('pointerup', onPointerUp);
                          };

                          const handleResizePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
                            e.stopPropagation();
                            setSelectedNoteId(note.id);
                            (e.target as HTMLElement).setPointerCapture(e.pointerId);

                            const startX = e.clientX;
                            const startLength = note.length;

                            const onPointerMove = (moveEvent: PointerEvent) => {
                              if (!gridRef.current) return;
                              const rect = gridRef.current.getBoundingClientRect();
                              const deltaX = moveEvent.clientX - startX;

                              const ticksPerPx = totalTicks / rect.width;
                              let newLength = Math.max(60, startLength + deltaX * ticksPerPx);
                              newLength = Math.round(newLength / 60) * 60; // Snap length

                              setNotes(prev => prev.map(n => n.id === note.id ? { ...n, length: newLength } : n));
                            };

                            const onPointerUp = () => {
                              window.removeEventListener('pointermove', onPointerMove);
                              window.removeEventListener('pointerup', onPointerUp);
                            };

                            window.addEventListener('pointermove', onPointerMove);
                            window.addEventListener('pointerup', onPointerUp);
                          };

                          return (
                            <div
                              key={note.id}
                              onPointerDown={handleNotePointerDown}
                              className={`absolute rounded-md px-2 flex items-center justify-between text-xs font-bold cursor-pointer transition shadow border gpu-accelerated group ${
                                isSelected
                                  ? 'bg-cyan-500 text-slate-950 border-white ring-2 ring-cyan-400/50 z-20'
                                  : 'bg-indigo-600/90 hover:bg-indigo-500 text-white border-indigo-400/30 z-10'
                              }`}
                              style={{
                                top: `${topPos + 1}px`,
                                height: `${Math.max(16, pianoRollRowHeight - 2)}px`,
                                left: `${leftPct}%`,
                                width: `${Math.max(widthPct, 2)}%`
                              }}
                            >
                              <span className="truncate pointer-events-none">{note.lyric}</span>
                              <span className="text-[9px] font-mono opacity-80 pl-1 pointer-events-none">{getNoteName(note.noteNum)}</span>
                              
                              {/* Resize Handle */}
                              <div 
                                className="resize-handle absolute right-0 top-0 bottom-0 w-3 cursor-ew-resize opacity-0 group-hover:opacity-100 bg-black/20 hover:bg-black/40 rounded-r-md"
                                onPointerDown={handleResizePointerDown}
                              />
                            </div>
                          );
                        })}

                        {/* 他トラックの透視 (Ghost Notes / Pitches) */}
                        {showGhostNotes && (
                          <GhostTrackOverlay
                            ghostTracks={tracks.filter((t) => t.id !== currentTrackId && t.type === 'vocal')}
                            totalTicks={totalTicks}
                            rowHeightPx={pianoRollRowHeight}
                            visibleStartTick={visibleTickRange.startTick}
                            visibleEndTick={visibleTickRange.endTick}
                          />
                        )}

                        {/* インタラクティブ・ピッチカーブオーバーレイ */}
                        <PitchCurveOverlay
                          notes={notes}
                          selectedNoteId={selectedNoteId}
                          tempo={tempo}
                          gridRef={gridRef}
                          gridHeightPx={37 * pianoRollRowHeight}
                          totalTicks={totalTicks}
                          rowHeightPx={pianoRollRowHeight}
                          visibleStartTick={visibleTickRange.startTick}
                          visibleEndTick={visibleTickRange.endTick}
                          onUpdateNote={(noteId, pitchData) =>
                            setNotes((prev) =>
                              prev.map((n) => (n.id === noteId ? { ...n, ...pitchData } : n))
                            )
                          }
                          onSelectNote={setSelectedNoteId}
                        />
                      </div>
                    </div>
                  </div>

                {/* Right Parameter Inspector Panel */}
                <div className="w-72 bg-slate-900 border-l border-slate-800 p-4 flex flex-col space-y-4 shrink-0 overflow-y-auto">
                  <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                    <h3 className="font-semibold text-xs text-slate-200 flex items-center space-x-1.5">
                      <Edit3 className="w-4 h-4 text-cyan-400" />
                      <span>ノートパラメータ設定</span>
                    </h3>
                    {selectedNote && (
                      <button
                        onClick={() => deleteNote(selectedNote.id)}
                        className="text-red-400 hover:text-red-300 p-1 rounded hover:bg-slate-800"
                        title="ノート削除"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>

                  {selectedNote ? (
                    <div className="space-y-3 text-xs">
                      <div>
                        <div className="flex items-center justify-between mb-1">
                          <label className="text-slate-400">歌詞 / 音素 (Lyric / Phoneme):</label>
                          <button
                            type="button"
                            onClick={() => setIsBatchLyricModalOpen(true)}
                            className="text-[10px] text-cyan-400 hover:text-cyan-300 hover:underline flex items-center gap-0.5"
                          >
                            <Type className="w-2.5 h-2.5" />
                            <span>一括入力</span>
                          </button>
                        </div>
                        <input
                          type="text"
                          value={selectedNote.lyric}
                          onChange={(e) => updateSelectedNote('lyric', e.target.value)}
                          className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-cyan-300 font-bold"
                        />
                      </div>

                      <div>
                        <label className="text-slate-400 block mb-1">音高 (MIDI Note):</label>
                        <div className="flex space-x-2">
                          <input
                            type="number"
                            min="36"
                            max="84"
                            value={selectedNote.noteNum}
                            onChange={(e) => updateSelectedNote('noteNum', parseInt(e.target.value) || 60)}
                            className="w-1/2 bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-slate-200 font-mono"
                          />
                          <div className="w-1/2 bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-cyan-400 font-mono font-bold flex items-center justify-center">
                            {getNoteName(selectedNote.noteNum)}
                          </div>
                        </div>
                      </div>

                      <div>
                        <label className="text-slate-400 block mb-1">長さ (Length Ticks):</label>
                        <input
                          type="number"
                          step="60"
                          value={selectedNote.length}
                          onChange={(e) => updateSelectedNote('length', parseInt(e.target.value) || 480)}
                          className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-slate-200 font-mono"
                        />
                      </div>

                      <div>
                        <label className="text-slate-400 block mb-1">音量強度 (Intensity): {selectedNote.intensity}</label>
                        <input
                          type="range"
                          min="0"
                          max="150"
                          value={selectedNote.intensity}
                          onChange={(e) => updateSelectedNote('intensity', parseFloat(e.target.value))}
                          className="w-full accent-cyan-400"
                        />
                      </div>

                      <div>
                        <label className="text-slate-400 block mb-1">フラグ (Flags, e.g. g-5B50):</label>
                        <input
                          type="text"
                          value={selectedNote.flags}
                          onChange={(e) => updateSelectedNote('flags', e.target.value)}
                          placeholder="g-5B50"
                          className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1.5 text-slate-200 font-mono"
                        />
                      </div>

                      <div className="border-t border-slate-800 pt-3">
                        <label className="text-slate-300 font-medium block mb-2 flex items-center space-x-1">
                          <AudioWaveform className="w-3.5 h-3.5 text-cyan-400" />
                          <span>ピッチカーブ (PBS/PBW/PBY)</span>
                        </label>

                        <PitchCurveMiniEditor
                          pbs={selectedNote.pbs}
                          pbw={selectedNote.pbw}
                          pby={selectedNote.pby}
                          noteLengthTicks={selectedNote.length}
                          tempo={tempo}
                          onChange={({ pbs, pbw, pby }) => {
                            updateSelectedNote('pbs', pbs);
                            updateSelectedNote('pbw', pbw);
                            updateSelectedNote('pby', pby);
                          }}
                        />

                        <details className="mt-2">
                          <summary className="text-[10px] text-slate-500 cursor-pointer select-none">
                            生の値を直接編集 (詳細)
                          </summary>
                          <div className="space-y-2 mt-2">
                            <input
                              type="text"
                              value={selectedNote.pbs}
                              onChange={(e) => updateSelectedNote('pbs', e.target.value)}
                              placeholder="PBS (e.g. -20;0)"
                              className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1 text-slate-300 font-mono text-[11px]"
                            />
                            <input
                              type="text"
                              value={selectedNote.pbw}
                              onChange={(e) => updateSelectedNote('pbw', e.target.value)}
                              placeholder="PBW (e.g. 50,100)"
                              className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1 text-slate-300 font-mono text-[11px]"
                            />
                            <input
                              type="text"
                              value={selectedNote.pby}
                              onChange={(e) => updateSelectedNote('pby', e.target.value)}
                              placeholder="PBY (e.g. 0,5)"
                              className="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1 text-slate-300 font-mono text-[11px]"
                            />
                          </div>
                        </details>
                      </div>
                    </div>
                  ) : (
                    <div className="text-center py-8 text-slate-500 text-xs">
                      ピアノロール上のノートを選択してください
                    </div>
                  )}
                </div>
              </div>

              {/* 歌詞一括入力モーダル (Batch Lyric Input Modal) */}
              <BatchLyricModal
                isOpen={isBatchLyricModalOpen}
                onClose={() => setIsBatchLyricModalOpen(false)}
                notes={notes}
                onApplyLyrics={handleApplyBatchLyrics}
              />
            </div>
          )}

          {activeTab === 'voicebanks' && (
            <div className="p-6 overflow-y-auto space-y-6 flex-1 bg-slate-950">
              {/* Header & Metric Banner */}
              <div className="flex flex-col lg:flex-row lg:items-center justify-between border-b border-slate-800 pb-5 gap-4">
                <div>
                  <div className="flex items-center space-x-3">
                    <div className="p-2.5 rounded-xl bg-gradient-to-tr from-cyan-600 to-blue-600 text-white shadow-lg shadow-cyan-500/20">
                      <Library className="w-6 h-6" />
                    </div>
                    <div>
                      <h2 className="text-xl font-bold text-slate-100 tracking-wide flex items-center space-x-2">
                        <span>UTAU 音源ライブラリ・マネージャー</span>
                        <span className="text-xs font-mono font-normal px-2.5 py-0.5 rounded-full bg-cyan-950 text-cyan-300 border border-cyan-800/60">
                          {customVoicebanks.length} 個の音源が利用可能
                        </span>
                      </h2>
                      <p className="text-xs text-slate-400 mt-1">
                        ZIP音源の追加・削除・原音設定 (oto.ini) 確認・アクティブ選択
                      </p>
                    </div>
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-3">
                  <div className="relative">
                    <Search className="w-4 h-4 text-slate-500 absolute left-3 top-2.5" />
                    <input
                      type="text"
                      placeholder="音源名で検索..."
                      value={vbSearchQuery}
                      onChange={(e) => setVbSearchQuery(e.target.value)}
                      className="pl-9 pr-4 py-1.5 bg-slate-900 border border-slate-800 rounded-lg text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-500 w-48 sm:w-60"
                    />
                  </div>

                  <div className="flex items-center gap-1 bg-slate-900 border border-slate-800 rounded-lg p-1">
                    {(['all', 'official', 'custom'] as const).map((key) => (
                      <button
                        key={key}
                        onClick={() => setVbCategoryFilter(key)}
                        className={`px-2.5 py-1 rounded-md text-[11px] font-semibold transition ${
                          vbCategoryFilter === key
                            ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40'
                            : 'text-slate-400 hover:text-slate-200 border border-transparent'
                        }`}
                      >
                        {key === 'all' ? 'すべて' : key === 'official' ? '内蔵' : 'カスタム'}
                      </button>
                    ))}
                  </div>

                  <button
                    onClick={() => {
                      fetchVoicebanks();
                      setToast({ type: 'info', title: 'ライブラリ更新', desc: '最新の登録音源状態を取得しました。' });
                    }}
                    className="flex items-center space-x-1.5 text-xs bg-slate-900 hover:bg-slate-800 text-slate-300 px-3 py-2 rounded-lg border border-slate-800 transition cursor-pointer"
                    title="音源ライブラリの最新状態を取得"
                  >
                    <RefreshCw className="w-3.5 h-3.5 text-cyan-400" />
                    <span>更新</span>
                  </button>

                  {isUploadingVb ? (
                    <div className="flex items-center gap-2">
                      <div className="flex items-center space-x-2 text-xs bg-slate-900 border border-cyan-500/50 text-cyan-300 font-semibold px-3 py-2 rounded-lg shadow-sm">
                        <Loader2 className="w-4 h-4 animate-spin text-cyan-400" />
                        <span>アップロード中 ({uploadProgress}%)</span>
                      </div>
                      <button
                        onClick={handleCancelVoicebankUpload}
                        className="flex items-center space-x-1.5 text-xs bg-rose-600 hover:bg-rose-500 text-white font-semibold px-3.5 py-2 rounded-lg cursor-pointer transition shadow-md shadow-rose-900/40"
                        title="アップロードを中断"
                      >
                        <X className="w-4 h-4" />
                        <span>キャンセル</span>
                      </button>
                    </div>
                  ) : (
                    <label className="flex items-center space-x-2 text-xs bg-cyan-600 hover:bg-cyan-500 text-white font-semibold px-4 py-2 rounded-lg cursor-pointer transition shadow-lg shadow-cyan-900/40">
                      <Upload className="w-4 h-4" />
                      <span>UTAU音源(.zip) 追加</span>
                      <input ref={fileInputRef1} type="file" accept=".zip,application/zip,application/x-zip,application/x-zip-compressed,multipart/x-zip,application/octet-stream" onChange={handleVoicebankZipUpload} className="hidden" />
                    </label>
                  )}
                </div>
              </div>

              {/* Status Overview Cards */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-4 flex items-center space-x-4 shadow-sm">
                  <div className="p-3 bg-cyan-950 rounded-lg border border-cyan-800/50 text-cyan-400">
                    <CheckCircle2 className="w-6 h-6" />
                  </div>
                  <div>
                    <div className="text-[11px] text-slate-400 font-medium">現在選択中のアクティブ音源</div>
                    <div className="text-sm font-bold text-cyan-300 truncate max-w-[180px]">{selectedVoicebank || '未設定'}</div>
                    <div className={`text-[10px] font-mono mt-0.5 ${selectedVoicebank ? 'text-emerald-400' : 'text-amber-400'}`}>
                      {selectedVoicebank ? '● 合成可能・準備完了' : '○ 音源ZIPを追加してください'}
                    </div>
                  </div>
                </div>

                <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-4 flex items-center space-x-4 shadow-sm">
                  <div className="p-3 bg-blue-950 rounded-lg border border-blue-800/50 text-blue-400">
                    <HardDrive className="w-6 h-6" />
                  </div>
                  <div>
                    <div className="text-[11px] text-slate-400 font-medium">ダウンロード済み・追加音源</div>
                    <div className="text-sm font-bold text-slate-100 font-mono">{customVoicebanks.length} 個</div>
                    <div className="text-[10px] text-slate-400 mt-0.5">ZIP自動解凍 & oto.ini 解析済</div>
                  </div>
                </div>

                <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-4 flex items-center space-x-4 shadow-sm">
                  <div className="p-3 bg-amber-950 rounded-lg border border-amber-800/50 text-amber-400">
                    <AudioWaveform className="w-6 h-6" />
                  </div>
                  <div>
                    <div className="text-[11px] text-slate-400 font-medium">総登録原音・エイリアス数</div>
                    <div className="text-sm font-bold text-amber-300 font-mono">
                      {customVoicebanks.reduce((acc, v) => acc + (v.aliasCount || 0), 0)} 件
                    </div>
                    <div className="text-[10px] text-slate-400 mt-0.5">連続音 (VCV) & 単独音 (CV)</div>
                  </div>
                </div>
              </div>

              {/* Section 1: Installed Voicebanks */}
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-bold text-slate-200 uppercase tracking-wider flex items-center space-x-2">
                    <HardDrive className="w-4 h-4 text-cyan-400" />
                    <span>登録済みUTAU音源一覧</span>
                  </h3>
                  <span className="text-xs text-slate-400 font-mono">
                    {customVoicebanks.length} 音源登録中
                  </span>
                </div>

                {customVoicebanks.length === 0 ? (
                  <div className="bg-slate-900/60 border border-dashed border-slate-800 rounded-2xl p-10 text-center flex flex-col items-center justify-center space-y-4">
                    <div className="w-16 h-16 rounded-full bg-slate-800 flex items-center justify-center text-slate-400">
                      <HardDrive className="w-8 h-8 text-cyan-400/80" />
                    </div>
                    <div className="max-w-md">
                      <h4 className="text-base font-bold text-slate-200">音源が登録されていません</h4>
                      <p className="text-xs text-slate-400 mt-1.5 leading-relaxed">
                        UTAU音源（単独音・連続音・VCV）のZIPファイルをアップロードしてください。自動で展開され、oto.iniの原音設定がインデックスされます。
                      </p>
                    </div>
                    <label className="flex items-center space-x-2 text-xs bg-cyan-600 hover:bg-cyan-500 text-white font-semibold px-5 py-2.5 rounded-xl cursor-pointer transition shadow-lg shadow-cyan-950/50">
                      <Upload className="w-4 h-4" />
                      <span>UTAU音源(.zip)をアップロード</span>
                      <input ref={fileInputRef2} type="file" accept=".zip,application/zip,application/x-zip,application/x-zip-compressed,multipart/x-zip,application/octet-stream" onChange={handleVoicebankZipUpload} className="hidden" />
                    </label>
                  </div>
                ) : (
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  {/* Custom Installed Voicebanks */}
                  {customVoicebanks
                    .filter((vb) => vb.name.toLowerCase().includes(vbSearchQuery.toLowerCase()))
                    .map((vb) => {
                      const isSelected = selectedVoicebank === vb.name;
                      return (
                        <div
                          key={vb.name}
                          className={`bg-slate-900 rounded-xl border p-4 transition-all flex flex-col justify-between space-y-4 relative ${
                            isSelected
                              ? 'border-cyan-500 bg-cyan-950/20 shadow-lg shadow-cyan-500/10'
                              : 'border-slate-800 hover:border-slate-700'
                          }`}
                        >
                          <div>
                            <div className="flex items-start justify-between">
                              <div className="flex items-center space-x-3">
                                <div className="w-10 h-10 rounded-lg bg-gradient-to-tr from-emerald-500 to-teal-600 flex items-center justify-center font-bold text-white shadow shrink-0">
                                  {vb.hasVcv ? 'VCV' : 'CV'}
                                </div>
                                <div className="overflow-hidden">
                                  <h4 className="font-bold text-slate-100 text-sm truncate" title={vb.name}>
                                    {vb.name}
                                  </h4>
                                  <p className="text-[11px] text-emerald-400 flex items-center space-x-1">
                                    <CheckCircle2 className="w-3 h-3" />
                                    <span>インストール済み (解凍完了)</span>
                                  </p>
                                </div>
                              </div>
                              {isSelected && (
                                <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-950 text-emerald-400 border border-emerald-800 shrink-0">
                                  使用中
                                </span>
                              )}
                            </div>

                            <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] font-mono bg-slate-950 p-2.5 rounded-lg border border-slate-800/80">
                              <div>
                                <span className="text-slate-500">方式:</span>{' '}
                                <span className="text-cyan-300 font-bold">{vb.hasVcv ? '連続音 (VCV)' : '単独音 (CV)'}</span>
                              </div>
                              <div>
                                <span className="text-slate-500">原音数:</span>{' '}
                                <span className="text-amber-300 font-bold">{vb.aliasCount}</span>
                              </div>
                              <div className="col-span-2 flex items-center space-x-1">
                                <span className="text-slate-500">エイリアス試聴:</span>
                                <div className="flex items-center space-x-1 overflow-x-auto">
                                  {['あ', 'い', 'う'].map((vowel) => (
                                    <button
                                      key={vowel}
                                      onClick={() => playSampleAudio(vb.name, vowel, 60, 0.8)}
                                      className="px-1.5 py-0.5 bg-cyan-950 hover:bg-cyan-600 text-cyan-300 hover:text-white rounded text-[10px] font-bold transition border border-cyan-800/60"
                                    >
                                      {vowel}
                                    </button>
                                  ))}
                                </div>
                              </div>
                            </div>
                          </div>

                          <div className="flex items-center justify-between pt-2 border-t border-slate-800/80 gap-2">
                            <button
                              onClick={() => setSelectedVoicebank(vb.name)}
                              disabled={isSelected}
                              className={`flex-1 py-1.5 px-3 rounded-lg text-xs font-semibold transition flex items-center justify-center space-x-1 ${
                                isSelected
                                  ? 'bg-slate-800 text-slate-500 cursor-default'
                                  : 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-md'
                              }`}
                            >
                              <Check className="w-3.5 h-3.5" />
                              <span>{isSelected ? '選択中' : '選択'}</span>
                            </button>

                            <button
                              onClick={() => {
                                setSelectedVoicebank(vb.name);
                                setActiveTab('oto');
                              }}
                              className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-lg text-xs font-medium transition border border-slate-700 flex items-center space-x-1"
                              title="原音設定 (oto.ini) インスペクタを開く"
                            >
                              <Layers className="w-3.5 h-3.5 text-cyan-400" />
                              <span>原音設定</span>
                            </button>

                            <button
                              onClick={() => deleteVoicebank(vb.name)}
                              className="p-1.5 bg-rose-950/60 hover:bg-rose-900 text-rose-300 rounded-lg transition border border-rose-800/50"
                              title="ライブラリから削除"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          )}

          {activeTab === 'oto' && (
            <div className="p-6 overflow-y-auto space-y-6">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between border-b border-slate-800 pb-4 gap-4">
                <div>
                  <h2 className="text-lg font-bold text-slate-100 flex items-center space-x-2">
                    <Layers className="w-5 h-5 text-cyan-400" />
                    <span>UTAU 原音設定 (Oto Database Inspector)</span>
                  </h2>
                  <p className="text-xs text-slate-400 mt-1">
                    oto.ini エイリアス解析、オフセット、先行発声、オーバーラップの視覚化 & アップロード管理
                  </p>
                </div>

                <div className="flex items-center space-x-3">
                  {isUploadingVb ? (
                    <div className="flex items-center gap-2">
                      <div className="flex items-center space-x-2 text-xs bg-slate-900 border border-cyan-500/50 text-cyan-300 font-medium px-3 py-2 rounded-lg shadow-sm">
                        <Loader2 className="w-4 h-4 animate-spin text-cyan-400" />
                        <span>アップロード中 ({uploadProgress}%)</span>
                      </div>
                      <button
                        onClick={handleCancelVoicebankUpload}
                        className="flex items-center space-x-1 text-xs bg-rose-600 hover:bg-rose-500 text-white font-medium px-3 py-2 rounded-lg cursor-pointer transition shadow-md shadow-rose-900/40"
                        title="アップロードを中断"
                      >
                        <X className="w-3.5 h-3.5" />
                        <span>キャンセル</span>
                      </button>
                    </div>
                  ) : (
                    <label className="flex items-center space-x-1.5 text-xs bg-cyan-600 hover:bg-cyan-500 text-white font-medium px-3.5 py-2 rounded-lg cursor-pointer transition shadow-md shadow-cyan-900/40">
                      <Upload className="w-4 h-4" />
                      <span>UTAU音源(.zip) アップロード</span>
                      <input ref={fileInputRef2} type="file" accept=".zip" onChange={handleVoicebankZipUpload} className="hidden" />
                    </label>
                  )}
                </div>
              </div>

              {/* Voicebank Info Summary Header */}
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-col space-y-4 shadow-lg">
                <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
                  <div className="flex items-center space-x-3">
                    <div className="p-3 bg-cyan-950/80 rounded-lg border border-cyan-800/40 text-cyan-400 relative">
                      <AudioWaveform className="w-6 h-6" />
                      <span className="absolute -top-1 -right-1 w-3 h-3 bg-emerald-500 rounded-full border-2 border-slate-900 animate-pulse" />
                    </div>
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs text-slate-400 font-medium">選択中音源:</span>
                        <select
                          value={selectedVoicebank}
                          onChange={(e) => setSelectedVoicebank(e.target.value)}
                          className="bg-slate-950 border border-cyan-800/60 text-cyan-300 font-bold rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-cyan-400 shadow-inner"
                        >
                          <option value="" disabled>音源を選択...</option>
                          {customVoicebanks.map((vb) => (
                            <option key={vb.name} value={vb.name}>
                              ✅ {vb.name} ({vb.aliasCount} エイリアス{vb.hasVcv ? ' / VCV' : ''})
                            </option>
                          ))}
                        </select>

                        <span className="text-[11px] font-mono px-2.5 py-1 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800/60 flex items-center space-x-1">
                          <CheckCircle2 className="w-3.5 h-3.5" />
                          <span>アクティブ音源 (準備完了)</span>
                        </span>
                      </div>
                      <p className="text-xs text-slate-300 mt-1.5 flex flex-wrap items-center gap-2">
                        <span>
                          {customVoicebanks.find((v) => v.name === selectedVoicebank)
                            ? `解析済みエイリアス: ${customVoicebanks.find((v) => v.name === selectedVoicebank)?.aliasCount} 件 (${
                                customVoicebanks.find((v) => v.name === selectedVoicebank)?.hasVcv ? '連続音対応' : '単独音'
                              })`
                            : '音源が選択されていません'}
                        </span>
                        {selectedVbDetails && (
                          <span className="text-[10px] bg-emerald-950 text-emerald-300 px-2 py-0.5 rounded border border-emerald-800/60 font-mono">
                            WAV実音声ファイル: {selectedVbDetails.entries.filter((e) => e.wav_exists !== false).length} / {selectedVbDetails.entries.length} 検出済み
                          </span>
                        )}
                        {customVoicebanks.find((v) => v.name === selectedVoicebank) && (
                          <span className="text-[10px] bg-slate-800 text-slate-400 px-2 py-0.5 rounded border border-slate-700">
                            ZIP全サブフォルダ自動解凍・パース済み
                          </span>
                        )}
                      </p>
                    </div>
                  </div>

                  <div className="flex flex-col sm:flex-row items-start sm:items-center gap-2">
                    {/* Live WAV Sample Test buttons */}
                    {customVoicebanks.some((v) => v.name === selectedVoicebank) && (
                      <div className="flex items-center space-x-1.5 bg-slate-950 p-1.5 rounded-lg border border-slate-800">
                        <span className="text-[10px] text-cyan-400 font-bold px-1">生WAVテスト試聴:</span>
                        {['あ', 'い', 'う', 'え', 'お'].map((vowel) => (
                          <button
                            key={vowel}
                            onClick={() => playSampleAudio(selectedVoicebank, vowel, 60, 1.0, true)}
                            className="px-2 py-1 bg-cyan-950 hover:bg-cyan-600 text-cyan-300 hover:text-white rounded text-xs font-bold transition border border-cyan-800/60 flex items-center space-x-1"
                          >
                            <Play className="w-2.5 h-2.5 fill-current" />
                            <span>{vowel}</span>
                          </button>
                        ))}
                      </div>
                    )}

                    <div className="flex items-center space-x-2 text-xs">
                      <input
                        type="text"
                        placeholder="エイリアス検索 (例: あ, a い, - か)..."
                        value={selectedAliasSearch}
                        onChange={(e) => setSelectedAliasSearch(e.target.value)}
                        className="bg-slate-950 border border-slate-800 rounded-lg px-3 py-1.5 text-slate-200 placeholder-slate-500 w-52 focus:outline-none focus:border-cyan-500"
                      />
                    </div>
                  </div>
                </div>

                {/* Upload & Unzip Progress Indicator */}
                {(isUploadingVb || uploadProgress > 0) && (
                  <div className="bg-slate-950/80 rounded-xl p-3 border border-cyan-800/50 space-y-2 animate-pulse">
                    <div className="flex items-center justify-between text-xs font-mono">
                      <span className="text-cyan-300 font-bold flex items-center space-x-2">
                        <Loader2 className="w-3.5 h-3.5 text-cyan-400 animate-spin" />
                        <span>UTAU音源ZIP転送 & 解凍・パース進行中</span>
                      </span>
                      <span className="text-emerald-400 font-bold text-sm">{uploadProgress}%</span>
                    </div>
                    <div className="w-full bg-slate-900 rounded-full h-2.5 overflow-hidden border border-slate-800 p-0.5">
                      <div
                        className="bg-gradient-to-r from-cyan-500 via-teal-400 to-emerald-400 h-full rounded-full transition-all duration-300"
                        style={{ width: `${Math.max(5, uploadProgress)}%` }}
                      />
                    </div>
                  </div>
                )}
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-4">
                  <h3 className="text-sm font-semibold text-slate-200 flex items-center justify-between">
                    <span>原音パラメータ設定 (Oto Parameters)</span>
                    {selectedOtoEntry && (
                      <span className="text-xs font-mono text-cyan-400 bg-cyan-950 border border-cyan-800/60 px-2 py-0.5 rounded">
                        {selectedOtoEntry.alias} ({selectedOtoEntry.filename})
                      </span>
                    )}
                  </h3>

                  <div className="space-y-3 text-xs">
                    <div>
                      <div className="flex justify-between mb-1">
                        <span className="text-slate-400">オフセット (Offset ms):</span>
                        <span className="text-cyan-400 font-mono">{otoOffset} ms</span>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max="200"
                        value={otoOffset}
                        onChange={(e) => setOtoOffset(Number(e.target.value))}
                        className="w-full accent-cyan-400"
                      />
                    </div>

                    <div>
                      <div className="flex justify-between mb-1">
                        <span className="text-slate-400">オーバーラップ (Overlap ms):</span>
                        <span className="text-cyan-400 font-mono">{otoOverlap} ms</span>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max="100"
                        value={otoOverlap}
                        onChange={(e) => setOtoOverlap(Number(e.target.value))}
                        className="w-full accent-cyan-400"
                      />
                    </div>

                    <div>
                      <div className="flex justify-between mb-1">
                        <span className="text-slate-400">先行発声 (Preutterance ms):</span>
                        <span className="text-cyan-400 font-mono">{otoPreutterance} ms</span>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max="150"
                        value={otoPreutterance}
                        onChange={(e) => setOtoPreutterance(Number(e.target.value))}
                        className="w-full accent-cyan-400"
                      />
                    </div>

                    <div>
                      <div className="flex justify-between mb-1">
                        <span className="text-slate-400">ブランク (Cutoff ms):</span>
                        <span className="text-cyan-400 font-mono">{otoBlank} ms</span>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max="300"
                        value={otoBlank}
                        onChange={(e) => setOtoBlank(Number(e.target.value))}
                        className="w-full accent-cyan-400"
                      />
                    </div>

                    <div>
                      <div className="flex justify-between mb-1">
                        <span className="text-slate-400">固定範囲 (Consonant Velocity):</span>
                        <span className="text-cyan-400 font-mono">{otoConsonant} ms</span>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max="200"
                        value={otoConsonant}
                        onChange={(e) => setOtoConsonant(Number(e.target.value))}
                        className="w-full accent-cyan-400"
                      />
                    </div>

                    <div className="pt-2 flex space-x-2">
                      <button
                        onClick={() => playVocalNote(60, selectedOtoEntry?.alias || 'あ', 0.8)}
                        className="flex-1 bg-cyan-600 hover:bg-cyan-500 text-white font-medium py-2 rounded-lg transition text-center flex items-center justify-center space-x-1.5 shadow"
                      >
                        <Play className="w-3.5 h-3.5 fill-current" />
                        <span>原音パラメータ テスト再生</span>
                      </button>
                    </div>
                  </div>
                </div>

                <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 flex flex-col justify-between">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-200 mb-2">波形エンベロープ プレビュー</h3>
                    <p className="text-xs text-slate-400 mb-4">
                      VSE-vocal の音源エンジン (VCV Resolver & World Synthesizer) による合成タイミング視覚化
                    </p>

                    <div className="h-40 bg-slate-950 border border-slate-800 rounded-lg relative overflow-hidden flex items-center justify-center p-4">
                      {/* Envelope SVG lines */}
                      <svg className="w-full h-full text-cyan-400 stroke-current fill-none stroke-2" viewBox="0 0 300 100">
                        <path d="M 10 90 L 40 20 L 120 20 L 260 90" />
                        <line x1="40" y1="0" x2="40" y2="100" className="stroke-rose-500 stroke-1 stroke-dasharray-2" />
                        <line x1="80" y1="0" x2="80" y2="100" className="stroke-amber-400 stroke-1 stroke-dasharray-2" />
                      </svg>
                      <div className="absolute top-2 left-2 text-[10px] text-rose-400 font-mono">
                        Preutterance: {otoPreutterance}ms
                      </div>
                      <div className="absolute top-2 left-28 text-[10px] text-amber-300 font-mono">
                        Overlap: {otoOverlap}ms
                      </div>
                    </div>
                  </div>

                  <div className="text-xs text-slate-400 bg-slate-950/60 p-3 rounded-lg border border-slate-800 mt-4">
                    <span className="text-cyan-400 font-bold">ヒント:</span> ZIP形式でアップロードされた UTAU
                    音源は自動的に解凍され、<code className="text-slate-200 font-mono">oto.ini</code> が Shift-JIS / UTF-8
                    両対応で全サブフォルダ再帰ロードされます。
                  </div>
                </div>
              </div>

              {/* Oto Entries Database Table */}
              <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-4">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-200 flex items-center space-x-2">
                      <span>ロード済み oto.ini エントリ一覧</span>
                      {isLoadingDetails && <Loader2 className="w-3.5 h-3.5 text-cyan-400 animate-spin" />}
                    </h3>
                    <p className="text-[11px] text-slate-400 mt-0.5">
                      クリックでパラメータを編集、または「▶ 試聴」で音源の実WAVサンプル音声を試聴できます。
                    </p>
                  </div>
                  <span className="text-xs text-slate-400 font-mono bg-slate-950 px-3 py-1 rounded-lg border border-slate-800">
                    {
                      ((selectedVbDetails && selectedVbDetails.entries) ||
                        customVoicebanks.find((v) => v.name === selectedVoicebank)?.entries || []
                      ).filter((e: any) => (selectedAliasSearch ? e.alias.includes(selectedAliasSearch) : true)).length
                    }{' '}
                    / {selectedVbDetails?.aliasCount || customVoicebanks.find((v) => v.name === selectedVoicebank)?.aliasCount || 0} エントリ表示中
                  </span>
                </div>

                <div className="overflow-x-auto max-h-80 border border-slate-800 rounded-lg bg-slate-950/50">
                  <table className="w-full text-left border-collapse text-xs">
                    <thead className="bg-slate-950 text-slate-400 font-mono border-b border-slate-800 sticky top-0 z-10">
                      <tr>
                        <th className="py-2.5 px-3">エイリアス (Alias)</th>
                        <th className="py-2.5 px-3">WAVファイル</th>
                        <th className="py-2.5 px-3">WAV状態</th>
                        <th className="py-2.5 px-3">Offset (ms)</th>
                        <th className="py-2.5 px-3">Consonant (ms)</th>
                        <th className="py-2.5 px-3">Preutterance (ms)</th>
                        <th className="py-2.5 px-3">Overlap (ms)</th>
                        <th className="py-2.5 px-3 text-right">実音試聴</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/60 font-mono text-slate-300">
                      {((selectedVbDetails && selectedVbDetails.entries) ||
                        customVoicebanks.find((v) => v.name === selectedVoicebank)?.entries || [
                          { alias: '- あ', filename: '_a.wav', wav_exists: true, left_blank: 10, fixed_range: 80, preutterance: 30, overlap: 10 },
                          { alias: 'a い', filename: '_ai.wav', wav_exists: true, left_blank: 15, fixed_range: 100, preutterance: 25, overlap: 8 },
                          { alias: 'i う', filename: '_iu.wav', wav_exists: true, left_blank: 12, fixed_range: 90, preutterance: 28, overlap: 9 },
                          { alias: 'u え', filename: '_ue.wav', wav_exists: true, left_blank: 18, fixed_range: 110, preutterance: 22, overlap: 7 },
                          { alias: 'e お', filename: '_eo.wav', wav_exists: true, left_blank: 14, fixed_range: 95, preutterance: 26, overlap: 8 }
                        ]
                      )
                        .filter((e: any) => (selectedAliasSearch ? e.alias.includes(selectedAliasSearch) : true))
                        .map((entry: any, index: number) => {
                          const isThisPlaying = playingAlias === entry.alias;
                          return (
                            <tr
                              key={index}
                              onClick={() => {
                                setSelectedOtoEntry(entry);
                                if (entry.left_blank !== undefined) setOtoOffset(Math.round(entry.left_blank));
                                if (entry.overlap !== undefined) setOtoOverlap(Math.round(entry.overlap));
                                if (entry.preutterance !== undefined) setOtoPreutterance(Math.round(entry.preutterance));
                                if (entry.fixed_range !== undefined) setOtoConsonant(Math.round(entry.fixed_range));
                              }}
                              className={`hover:bg-slate-800/70 transition cursor-pointer ${
                                selectedOtoEntry?.alias === entry.alias ? 'bg-cyan-950/60 text-cyan-200' : ''
                              }`}
                            >
                              <td className="py-2 px-3 font-bold text-cyan-400 flex items-center space-x-1.5">
                                <span>{entry.alias}</span>
                              </td>
                              <td className="py-2 px-3 text-slate-400">{entry.filename}</td>
                              <td className="py-2 px-3">
                                {entry.wav_exists !== false ? (
                                  <span className="inline-flex items-center space-x-1 text-[10px] text-emerald-400 bg-emerald-950/80 px-2 py-0.5 rounded border border-emerald-800/60">
                                    <CheckCircle2 className="w-3 h-3" />
                                    <span>検出OK</span>
                                  </span>
                                ) : (
                                  <span className="inline-flex items-center space-x-1 text-[10px] text-amber-400 bg-amber-950/80 px-2 py-0.5 rounded border border-amber-800/60">
                                    <span>WAV未検出</span>
                                  </span>
                                )}
                              </td>
                              <td className="py-2 px-3">{Math.round(entry.left_blank || 0)}</td>
                              <td className="py-2 px-3">{Math.round(entry.fixed_range || 0)}</td>
                              <td className="py-2 px-3">{Math.round(entry.preutterance || 0)}</td>
                              <td className="py-2 px-3">{Math.round(entry.overlap || 0)}</td>
                              <td className="py-2 px-3 text-right">
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    playSampleAudio(selectedVoicebank, entry.alias, 60, 1.2);
                                  }}
                                  className={`px-2.5 py-1 rounded text-xs font-sans font-medium transition flex items-center space-x-1 ml-auto ${
                                    isThisPlaying
                                      ? 'bg-emerald-600 text-white animate-pulse'
                                      : 'bg-cyan-900/80 hover:bg-cyan-600 text-cyan-200 hover:text-white border border-cyan-700/60'
                                  }`}
                                  title="実WAVサンプルの再生"
                                >
                                  {isThisPlaying ? (
                                    <>
                                      <Volume2 className="w-3.5 h-3.5 animate-bounce" />
                                      <span>再生中</span>
                                    </>
                                  ) : (
                                    <>
                                      <Play className="w-3 h-3 fill-current" />
                                      <span>実音試聴</span>
                                    </>
                                  )}
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'tests' && (
            <div className="p-6 overflow-y-auto space-y-6">
              <div className="flex items-center justify-between border-b border-slate-800 pb-4">
                <div>
                  <h2 className="text-lg font-bold text-slate-100 flex items-center space-x-2">
                    <Cpu className="w-5 h-5 text-cyan-400" />
                    <span>システム統合テスト & コード評価 (System Verification)</span>
                  </h2>
                  <p className="text-xs text-slate-400 mt-1">Pythonバックエンド、USTパーサー、 timelineモジュールの動作検証</p>
                </div>

                <button
                  onClick={handleRunTests}
                  disabled={isRunningTests}
                  className="flex items-center space-x-2 bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold px-4 py-2 rounded-lg transition shadow-md disabled:opacity-50"
                >
                  <RefreshCw className={`w-4 h-4 ${isRunningTests ? 'animate-spin' : ''}`} />
                  <span>{isRunningTests ? 'テスト実行中...' : 'テスト実行 (python -m unittest)'}</span>
                </button>
              </div>

              {testResult && (
                <div className={`p-4 rounded-xl border ${testResult.success ? 'bg-emerald-950/30 border-emerald-800/50' : 'bg-slate-900 border-slate-800'}`}>
                  <div className="flex items-center space-x-2 mb-2">
                    {testResult.success ? (
                      <CheckCircle2 className="w-5 h-5 text-emerald-400" />
                    ) : (
                      <AlertCircle className="w-5 h-5 text-amber-400" />
                    )}
                    <span className="font-semibold text-sm text-slate-200">
                      {testResult.success ? '全テストパス成功' : 'テスト完了 (レポート出力あり)'}
                    </span>
                  </div>

                  {testResult.stdout && (
                    <div className="mt-3">
                      <span className="text-xs text-slate-400 block mb-1 font-mono">STDOUT:</span>
                      <pre className="bg-slate-950 p-3 rounded-lg text-xs font-mono text-slate-300 overflow-x-auto max-h-48 border border-slate-800">
                        {testResult.stdout}
                      </pre>
                    </div>
                  )}

                  {testResult.stderr && (
                    <div className="mt-3">
                      <span className="text-xs text-amber-400 block mb-1 font-mono">STDERR:</span>
                      <pre className="bg-slate-950 p-3 rounded-lg text-xs font-mono text-amber-200/90 overflow-x-auto max-h-48 border border-slate-800">
                        {testResult.stderr}
                      </pre>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {activeTab === 'desktop' && (
            <div className="p-6 overflow-y-auto space-y-6">
              <div className="border-b border-slate-800 pb-4">
                <h2 className="text-lg font-bold text-slate-100 flex items-center space-x-2">
                  <Monitor className="w-5 h-5 text-emerald-400" />
                  <span>PySide6 デスクトップ環境情報 (Desktop Native Integration)</span>
                </h2>
                <p className="text-xs text-slate-400 mt-1">
                  ユーザー様の要求通り PySide6 デスクトップアプリケーション (main.py) は完全に固定・併用維持されています。
                </p>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
                  <h3 className="text-sm font-semibold text-slate-200 flex items-center space-x-2">
                    <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                    <span>PySide6 環境ステータス</span>
                  </h3>
                  <div className="space-y-2 text-xs">
                    <div className="flex justify-between py-1.5 border-b border-slate-800">
                      <span className="text-slate-400">エントリポイント:</span>
                      <span className="text-cyan-400 font-mono font-bold">{pyStatus?.desktopEntryPoint || 'main.py'}</span>
                    </div>
                    <div className="flex justify-between py-1.5 border-b border-slate-800">
                      <span className="text-slate-400">Python バージョン:</span>
                      <span className="text-slate-200 font-mono">{pyStatus?.pythonVersion || 'Python 3.10'}</span>
                    </div>
                    <div className="flex justify-between py-1.5 border-b border-slate-800">
                      <span className="text-slate-400">PySide6 モジュール:</span>
                      <span className="text-emerald-400 font-bold">インストール済み (固定維持)</span>
                    </div>
                    <div className="flex justify-between py-1.5">
                      <span className="text-slate-400">動作モード:</span>
                      <span className="text-cyan-300 font-medium">{pyStatus?.mode || 'Dual (Web + PySide6)'}</span>
                    </div>
                  </div>
                </div>

                <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
                  <h3 className="text-sm font-semibold text-slate-200">ローカルでの起動方法</h3>
                  <p className="text-xs text-slate-400">
                    デスクトップ環境 (Windows / Mac / Linux) でネイティブ PySide6 GUI アプリケーションを直接起動する場合:
                  </p>
                  <pre className="bg-slate-950 p-3 rounded-lg text-xs font-mono text-cyan-300 border border-slate-800">
                    python3 main.py
                  </pre>
                  <p className="text-xs text-slate-400">
                    PyInstallerビルドスペック: <code className="text-slate-300 font-mono">vose_pro.spec</code>
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Floating Toast Notification Popup */}
      {toast && (
        <div className="fixed bottom-5 right-5 z-50 max-w-sm w-full animate-bounce-short">
          <div
            className={`p-4 rounded-xl border shadow-2xl backdrop-blur-md flex flex-col space-y-2 ${
              toast.type === 'success'
                ? 'bg-slate-900/95 border-emerald-500/80 text-emerald-300 shadow-emerald-950/50'
                : toast.type === 'error'
                ? 'bg-slate-900/95 border-rose-500/80 text-rose-300 shadow-rose-950/50'
                : 'bg-slate-900/95 border-cyan-500/80 text-cyan-300 shadow-cyan-950/50'
            }`}
          >
            <div className="flex items-start space-x-3">
              <div className="shrink-0 mt-0.5">
                {toast.type === 'success' && <CheckCircle2 className="w-5 h-5 text-emerald-400" />}
                {toast.type === 'error' && <AlertCircle className="w-5 h-5 text-rose-400" />}
                {toast.type === 'info' && <Loader2 className="w-5 h-5 text-cyan-400 animate-spin" />}
              </div>
              <div className="flex-1">
                <h4 className="font-bold text-xs text-white flex items-center justify-between">
                  <span>{toast.title}</span>
                  {uploadProgress > 0 && uploadProgress < 100 && (
                    <span className="font-mono text-cyan-400 font-bold ml-2 text-[11px]">
                      {uploadProgress}%
                    </span>
                  )}
                </h4>
                <p className="text-xs mt-0.5 text-slate-300 leading-relaxed">{toast.desc}</p>
              </div>
              <button
                onClick={() => setToast(null)}
                className="shrink-0 p-1 rounded-md hover:bg-slate-800 text-slate-400 hover:text-white transition"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>

            {/* Graphical Progress Bar for Upload / Extract */}
            {uploadProgress > 0 && (
              <div className="w-full bg-slate-950 rounded-full h-2 overflow-hidden border border-slate-800 p-0.5">
                <div
                  className={`h-full rounded-full transition-all duration-300 ${
                    uploadProgress >= 100 ? 'bg-emerald-500' : 'bg-gradient-to-r from-cyan-500 to-emerald-400'
                  }`}
                  style={{ width: `${uploadProgress}%` }}
                />
              </div>
            )}

            {/* Graphical Progress Bar for WAV Rendering with ETA */}
            {isRenderingWav && renderProgress && (
              <div className="space-y-1.5 pt-1">
                <div className="flex items-center justify-between text-[11px] font-mono">
                  <span className="text-cyan-300 font-bold">進捗: {renderProgress.pct}%</span>
                  <span className="text-emerald-300 font-semibold bg-emerald-950/80 px-2 py-0.5 rounded border border-emerald-800/60">
                    残り: {formatEta(renderProgress.remainingSec)}
                  </span>
                </div>
                <div className="w-full bg-slate-950 rounded-full h-2 overflow-hidden border border-slate-800 p-0.5">
                  <div
                    className="h-full rounded-full transition-all duration-200 bg-gradient-to-r from-cyan-500 via-teal-400 to-emerald-400"
                    style={{ width: `${Math.max(2, renderProgress.pct)}%` }}
                  />
                </div>
                {renderProgress.elapsedSec > 0 && (
                  <div className="text-[10px] text-slate-400 text-right font-mono">
                    経過時間: {renderProgress.elapsedSec}秒
                  </div>
                )}
              </div>
            )}

            {/* Cancel Upload Button inside Active Toast */}
            {isUploadingVb && (
              <div className="pt-1 flex justify-end">
                <button
                  onClick={handleCancelVoicebankUpload}
                  className="flex items-center space-x-1.5 text-[11px] font-semibold bg-rose-950/90 hover:bg-rose-900 text-rose-300 hover:text-white border border-rose-700/70 px-2.5 py-1 rounded-md transition shadow-sm"
                >
                  <X className="w-3.5 h-3.5" />
                  <span>アップロードを中止する</span>
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* プロジェクト / USTファイル読み込みモーダル */}
      <UstImportModal
        isOpen={isUstImportModalOpen}
        onClose={() => setIsUstImportModalOpen(false)}
        onImportData={handleApplyProjectData}
      />
    </div>
  );
}
