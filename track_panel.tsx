                {/* Tracks Panel */}
                <div className="w-56 bg-slate-900 border-r border-slate-800 flex flex-col shrink-0">
                  <div className="h-7 border-b border-slate-800 bg-slate-950 flex items-center px-3 justify-between">
                    <span className="text-[10px] text-slate-400 font-bold tracking-wider">TRACKS</span>
                    <div className="flex space-x-1">
                      <button 
                        onClick={() => setTracks(prev => [...prev, { id: `track_${Date.now()}`, name: `Vocal ${prev.length + 1}`, type: 'vocal', voicebank: 'Official Voice (VCV)', notes: [], volume: 0.8, isMuted: false, isSolo: false }])}
                        className="text-[9px] bg-slate-800 hover:bg-cyan-900 text-cyan-400 px-1.5 py-0.5 rounded transition"
                      >+ VOCAL</button>
                    </div>
                  </div>
                  <div className="flex-1 overflow-y-auto flex flex-col p-2 space-y-2">
                    {tracks.map(track => (
                      <div 
                        key={track.id} 
                        onClick={() => setCurrentTrackId(track.id)}
                        className={`p-2 rounded-lg border cursor-pointer transition ${currentTrackId === track.id ? 'bg-cyan-950/40 border-cyan-500/50' : 'bg-slate-800 border-slate-700 hover:bg-slate-700'}`}
                      >
                        <div className="flex items-center justify-between mb-1">
                          <span className={`text-xs font-bold ${currentTrackId === track.id ? 'text-cyan-300' : 'text-slate-300'}`}>{track.name}</span>
                          <span className="text-[9px] px-1 rounded bg-slate-950 text-slate-400">{track.type.toUpperCase()}</span>
                        </div>
                        {track.type === 'vocal' && (
                          <div className="text-[10px] text-slate-400 truncate mb-2">{track.voicebank}</div>
                        )}
                        <div className="flex items-center space-x-2">
                          <button onClick={(e) => { e.stopPropagation(); setTracks(prev => prev.map(t => t.id === track.id ? { ...t, isMuted: !t.isMuted } : t)) }} className={`w-6 h-6 rounded flex items-center justify-center text-[10px] font-bold ${track.isMuted ? 'bg-red-500/20 text-red-400' : 'bg-slate-950 text-slate-500 hover:text-slate-300'}`}>M</button>
                          <button onClick={(e) => { e.stopPropagation(); setTracks(prev => prev.map(t => t.id === track.id ? { ...t, isSolo: !t.isSolo } : t)) }} className={`w-6 h-6 rounded flex items-center justify-center text-[10px] font-bold ${track.isSolo ? 'bg-amber-500/20 text-amber-400' : 'bg-slate-950 text-slate-500 hover:text-slate-300'}`}>S</button>
                          <input type="range" min="0" max="1" step="0.05" value={track.volume} onChange={(e) => setTracks(prev => prev.map(t => t.id === track.id ? { ...t, volume: parseFloat(e.target.value) } : t))} className="w-16 accent-cyan-500" onClick={(e) => e.stopPropagation()} />
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
