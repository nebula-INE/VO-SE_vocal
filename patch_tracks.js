const fs = require('fs');
let code = fs.readFileSync('src/App.tsx', 'utf-8');

// We need to inject the Track interface and update states
code = code.replace(
  'interface UstProjectData {',
  `interface Track {
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

interface UstProjectData {`
);

// Replace notes state with tracks state
code = code.replace(
  "  const [projectName, setProjectName] = useState<string>('VO-SE Song 1');\n  const [notes, setNotes] = useState<Note[]>(INITIAL_NOTES);\n  const [selectedNoteId, setSelectedNoteId] = useState<string | null>('1');\n  const gridRef = useRef<HTMLDivElement>(null);\n  const [clipboardNote, setClipboardNote] = useState<Note | null>(null);",
  `  const [projectName, setProjectName] = useState<string>('VO-SE Song 1');
  const [tracks, setTracks] = useState<Track[]>([
    {
      id: 'track_1',
      name: 'Vocal 1',
      type: 'vocal',
      voicebank: 'Official Voice (VCV)',
      notes: INITIAL_NOTES,
      volume: 0.8,
      isMuted: false,
      isSolo: false
    }
  ]);
  const [currentTrackId, setCurrentTrackId] = useState<string>('track_1');
  const currentTrack = tracks.find(t => t.id === currentTrackId) || tracks[0];
  const notes = currentTrack.type === 'vocal' ? currentTrack.notes : [];
  
  const setNotes = (updater: any) => {
    setTracks(prev => prev.map(t => {
      if (t.id === currentTrackId && t.type === 'vocal') {
        const newNotes = typeof updater === 'function' ? updater(t.notes) : updater;
        return { ...t, notes: newNotes };
      }
      return t;
    }));
  };

  const [selectedNoteId, setSelectedNoteId] = useState<string | null>('1');
  const gridRef = useRef<HTMLDivElement>(null);
  const [clipboardNote, setClipboardNote] = useState<Note | null>(null);`
);

// Replace selectedVoicebank state
code = code.replace(
  "  const [selectedVoicebank, setSelectedVoicebank] = useState<string>('Official Voice (VCV)');",
  `  const selectedVoicebank = currentTrack?.voicebank || 'Official Voice (VCV)';
  const setSelectedVoicebank = (vb: string) => {
    setTracks(prev => prev.map(t => t.id === currentTrackId ? { ...t, voicebank: vb } : t));
  };`
);

fs.writeFileSync('src/App.tsx', code);
console.log("Patched tracks state");
