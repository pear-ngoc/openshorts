import React, { useState, useEffect, useCallback } from 'react';
import { X, Wand2, Type, Sparkles, Loader2, CheckCircle, AlertCircle, ChevronDown, ChevronUp, Eye, EyeOff } from 'lucide-react';
import { getApiUrl } from '../config';
import RemotionPreview from './RemotionPreview';

const STYLE_PRESETS = [
    { value: 'auto', label: 'Auto (AI)', desc: 'Let AI decide' },
    { value: 'cinematic', label: 'Cinematic', desc: 'Zoom & color' },
    { value: 'dynamic', label: 'Dynamic', desc: 'Fast zooms' },
    { value: 'minimal', label: 'Minimal', desc: 'Subtle zoom' },
];

const FONT_OPTIONS = [
    { value: 'Verdana', label: 'Verdana' },
    { value: 'Arial', label: 'Arial' },
    { value: 'Impact', label: 'Impact' },
    { value: 'Helvetica', label: 'Helvetica' },
    { value: 'Georgia', label: 'Georgia' },
    { value: 'Courier New', label: 'Courier New' },
];

const COLOR_PRESETS = [
    { color: '#FFFFFF', label: 'White' },
    { color: '#FFFF00', label: 'Yellow' },
    { color: '#00FFFF', label: 'Cyan' },
    { color: '#00FF00', label: 'Green' },
    { color: '#FF0000', label: 'Red' },
    { color: '#FF69B4', label: 'Pink' },
];

const ANIMATION_OPTIONS = [
    { value: 'pop', label: 'Pop' },
    { value: 'word-highlight', label: 'Glow' },
    { value: 'karaoke', label: 'Karaoke' },
    { value: 'none', label: 'None' },
];

const ENTRANCE_OPTIONS = [
    { value: 'spring', label: 'Bounce' },
    { value: 'fade', label: 'Fade' },
    { value: 'slide-up', label: 'Slide Up' },
    { value: 'none', label: 'None' },
];

export default function CombinedEditModal({
    isOpen,
    onClose,
    onSubmit,
    isProcessing,
    processingStage,
    videoUrl,
    jobId,
    clipIndex,
    clipDuration,
    apiKey,
    baseUrl,
    provider,
    model,
    existingHook,
}) {
    const [durationSec, setDurationSec] = useState(clipDuration || 30);

    // Feature toggles
    const [enableAutoEdit, setEnableAutoEdit] = useState(false);
    const [enableSubtitles, setEnableSubtitles] = useState(false);
    const [enableHook, setEnableHook] = useState(false);

    // AutoEdit state
    const [effectsConfig, setEffectsConfig] = useState(null);
    const [autoEditLoading, setAutoEditLoading] = useState(false);
    const [autoEditError, setAutoEditError] = useState(null);

    // Subtitle state
    const [subPosition, setSubPosition] = useState('bottom');
    const [subFontSize, setSubFontSize] = useState(24);
    const [subFontName, setSubFontName] = useState('Verdana');
    const [subFontColor, setSubFontColor] = useState('#FFFFFF');
    const [subHighlightColor, setSubHighlightColor] = useState('#FFDD00');
    const [subBorderColor, setSubBorderColor] = useState('#000000');
    const [subBorderWidth, setSubBorderWidth] = useState(2);
    const [subBgColor, setSubBgColor] = useState('#000000');
    const [subBgOpacity, setSubBgOpacity] = useState(0.0);
    const [subAnimation, setSubAnimation] = useState('pop');
    const [captions, setCaptions] = useState([]);
    const [captionsLoading, setCaptionsLoading] = useState(false);
    const [showTextEditor, setShowTextEditor] = useState(false);
    const [editableText, setEditableText] = useState('');
    const [originalCaptions, setOriginalCaptions] = useState([]);

    // Hook state
    const [hookText, setHookText] = useState(existingHook || '');
    const [hookPosition, setHookPosition] = useState('top');
    const [hookSize, setHookSize] = useState('M');
    const [hookEntrance, setHookEntrance] = useState('spring');
    const [hookDuration, setHookDuration] = useState(5);

    // Section expand/collapse
    const [expandedSections, setExpandedSections] = useState({
        autoEdit: true,
        subtitles: true,
        hook: false,
    });

    // Sync hook text when modal opens with a new clip
    useEffect(() => {
        if (isOpen && existingHook) {
            setHookText(existingHook);
        }
    }, [isOpen, existingHook]);

    // Load duration — uses prop if provided, otherwise fetches from transcript
    useEffect(() => {
        if (!isOpen || !jobId || clipIndex === undefined) return;

        if (clipDuration) {
            setDurationSec(clipDuration);
            return;
        }

        const controller = new AbortController();

        fetch(getApiUrl(`/api/clip/${jobId}/${clipIndex}/transcript`), { signal: controller.signal })
            .then(res => res.ok ? res.json() : null)
            .then(data => {
                if (data?.durationSec) setDurationSec(data.durationSec);
            })
            .catch(err => { if (err.name !== 'AbortError') {} });
    }, [isOpen, jobId, clipIndex, clipDuration]);

    // Always fetch captions when modal opens, regardless of clipDuration
    useEffect(() => {
        if (!isOpen || !jobId || clipIndex === undefined) return;

        const controller = new AbortController();
        setCaptionsLoading(true);

        fetch(getApiUrl(`/api/clip/${jobId}/${clipIndex}/transcript`), { signal: controller.signal })
            .then(res => res.ok ? res.json() : null)
            .then(data => {
                if (data?.captions && data.captions.length > 0) {
                    setCaptions(data.captions);
                    setOriginalCaptions(data.captions);
                    setEditableText(data.captions.map(c => c.text).join(' '));
                } else {
                    setCaptions([]);
                    setOriginalCaptions([]);
                    setEditableText('');
                }
            })
            .catch(err => {
                if (err.name !== 'AbortError') {
                    setCaptions([]);
                    setOriginalCaptions([]);
                    setEditableText('');
                }
            })
            .finally(() => {
                if (!controller.signal.aborted) setCaptionsLoading(false);
            });

        return () => controller.abort();
    }, [isOpen, jobId, clipIndex]);

    // Generate auto-edit effects when enabled
    const generateEffects = useCallback(async () => {
        if (!enableAutoEdit) return;
        setAutoEditLoading(true);
        setAutoEditError(null);
        setEffectsConfig(null);

        const sendFilename = videoUrl && !videoUrl.startsWith('blob:')
            ? videoUrl.split('/').pop()
            : undefined;

        try {
            const res = await fetch(getApiUrl('/api/effects/generate'), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-LLM-Key': apiKey || '',
                    'X-LLM-Provider': provider || 'gemini',
                    ...(baseUrl?.trim() ? { 'X-LLM-Base-Url': baseUrl.trim() } : {}),
                    ...(model?.trim() ? { 'X-LLM-Model': model.trim() } : {}),
                },
                body: JSON.stringify({
                    job_id: jobId,
                    clip_index: clipIndex,
                    ...(sendFilename ? { input_filename: sendFilename } : {}),
                }),
            });

            if (!res.ok) {
                const text = await res.text();
                throw new Error(JSON.parse(text).detail || text);
            }

            const data = await res.json();
            if (data.effects && data.effects.segments && data.effects.segments.length > 0) {
                setEffectsConfig(data.effects);
            } else {
                throw new Error('No effects returned from AI');
            }
        } catch (err) {
            setAutoEditError(err.message);
        } finally {
            setAutoEditLoading(false);
        }
    }, [enableAutoEdit, jobId, clipIndex, videoUrl, apiKey, baseUrl, provider, model]);

    useEffect(() => {
        if (isOpen && enableAutoEdit) {
            generateEffects();
        }
    }, [isOpen, enableAutoEdit]);

    // Subtitle text edit
    const handleTextEdit = (newText) => {
        setEditableText(newText);
        const newWords = newText.split(/\s+/).filter(w => w.length > 0);
        if (newWords.length === 0 || originalCaptions.length === 0) {
            setCaptions([]);
            return;
        }
        const totalDurationMs = originalCaptions[originalCaptions.length - 1].endMs - originalCaptions[0].startMs;
        const startMs = originalCaptions[0].startMs;
        const wordDurationMs = totalDurationMs / newWords.length;
        const newCaptions = newWords.map((word, i) => ({
            text: word,
            startMs: Math.round(startMs + i * wordDurationMs),
            endMs: Math.round(startMs + (i + 1) * wordDurationMs),
        }));
        setCaptions(newCaptions);
    };

    // Build configs for preview
    const subtitleConfig = captions.length > 0 ? {
        captions,
        position: subPosition,
        style: {
            fontFamily: subFontName,
            fontSize: subFontSize * 2.2,
            fontColor: subFontColor,
            highlightColor: subHighlightColor,
            borderColor: subBorderColor,
            borderWidth: subBorderWidth * 1.5,
            bgColor: subBgColor,
            bgOpacity: subBgOpacity,
            animation: subAnimation,
        },
    } : null;

    const hookConfig = enableHook && hookText.trim() ? {
        text: hookText,
        position: hookPosition,
        size: hookSize,
        entranceAnimation: hookEntrance,
        displayDurationSec: hookDuration,
    } : null;

    const hasAnyFeature = enableAutoEdit || enableSubtitles || enableHook;
    const canSubmit = hasAnyFeature && (processingStage === null || processingStage === 'done');

    const toggleSection = (section) => {
        setExpandedSections(prev => ({ ...prev, [section]: !prev[section] }));
    };

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-[fadeIn_0.2s_ease-out]">
            <div className="bg-[#121214] border border-white/10 p-6 rounded-2xl w-full max-w-5xl shadow-2xl relative flex flex-col md:flex-row gap-6 max-h-[90vh]">
                <button onClick={onClose} className="absolute top-4 right-4 text-zinc-500 hover:text-white z-10">
                    <X size={20} />
                </button>

                {/* Left: Preview */}
                <div className="flex-1 flex flex-col items-center justify-center bg-black rounded-lg border border-white/5 overflow-hidden relative aspect-[9/16] max-h-[600px]">
                    {isProcessing ? (
                        <div className="flex flex-col items-center justify-center gap-4 p-8 text-center">
                            <Loader2 size={40} className="text-primary animate-spin" />
                            <div>
                                <p className="text-white font-bold text-sm">
                                    {processingStage === 'effects' ? 'Generating effects...' :
                                     processingStage === 'rendering' ? 'Rendering video...' :
                                     processingStage === 'saving' ? 'Saving to server...' :
                                     'Processing...'}
                                </p>
                                <p className="text-zinc-400 text-xs mt-1">
                                    {processingStage === 'rendering' && 'This may take a few minutes for longer videos'}
                                </p>
                            </div>
                        </div>
                    ) : (
                        <RemotionPreview
                            videoUrl={videoUrl}
                            durationInSeconds={durationSec}
                            effects={enableAutoEdit && effectsConfig ? effectsConfig : null}
                            subtitles={enableSubtitles && subtitleConfig ? subtitleConfig : null}
                            hook={enableHook && hookConfig ? hookConfig : null}
                        />
                    )}
                </div>

                {/* Right: Controls */}
                <div className="w-full md:w-96 flex flex-col">
                    <h3 className="text-xl font-bold text-white mb-4 flex items-center gap-2 shrink-0">
                        <Wand2 className="text-primary" /> Combined Editor
                    </h3>

                    <div className="space-y-3 flex-1 overflow-y-auto custom-scrollbar pr-1">

                        {/* Section: Auto Edit */}
                        <Section
                            title="Auto Edit"
                            icon={<Wand2 size={14} />}
                            colorClass="text-purple-400"
                            enabled={enableAutoEdit}
                            onToggle={() => setEnableAutoEdit(v => !v)}
                            expanded={expandedSections.autoEdit}
                            onToggleExpand={() => toggleSection('autoEdit')}
                            loading={autoEditLoading}
                            error={autoEditError}
                            onRetry={generateEffects}
                        >
                            {effectsConfig && (
                                <div className="mt-2 space-y-2">
                                    <div className="p-2 bg-purple-500/10 border border-purple-500/20 rounded-lg">
                                        <div className="text-[11px] text-purple-300">
                                            <span className="font-bold">{effectsConfig.segments?.length || 0}</span> effect segments
                                        </div>
                                    </div>
                                    {effectsConfig.segments?.slice(0, 3).map((seg, i) => (
                                        <div key={i} className="text-[10px] text-zinc-500 bg-white/5 rounded px-2 py-1">
                                            {seg.startSec?.toFixed(1)}s — {seg.type || 'effect'}
                                        </div>
                                    ))}
                                    {effectsConfig.segments?.length > 3 && (
                                        <div className="text-[10px] text-zinc-600">+{effectsConfig.segments.length - 3} more</div>
                                    )}
                                </div>
                            )}
                        </Section>

                        {/* Section: Subtitles */}
                        <Section
                            title="Subtitles"
                            icon={<Type size={14} />}
                            colorClass="text-yellow-400"
                            enabled={enableSubtitles}
                            onToggle={() => setEnableSubtitles(v => !v)}
                            expanded={expandedSections.subtitles}
                            onToggleExpand={() => toggleSection('subtitles')}
                            loading={captionsLoading}
                        >
                            <div className="space-y-3">
                                {/* Position */}
                                <div>
                                    <label className="text-[11px] font-bold text-zinc-400 uppercase mb-1 block">Position</label>
                                    <div className="grid grid-cols-3 gap-1">
                                        {['top', 'middle', 'bottom'].map(p => (
                                            <button key={p} onClick={() => setSubPosition(p)}
                                                className={`py-1 rounded text-[11px] font-medium border transition-all ${subPosition === p ? 'bg-yellow-500/20 border-yellow-500/50 text-yellow-300' : 'bg-white/5 border-white/5 text-zinc-400'}`}>
                                                {p}
                                            </button>
                                        ))}
                                    </div>
                                </div>

                                {/* Animation */}
                                <div>
                                    <label className="text-[11px] font-bold text-zinc-400 uppercase mb-1 block">Animation</label>
                                    <div className="grid grid-cols-2 gap-1">
                                        {ANIMATION_OPTIONS.map(a => (
                                            <button key={a.value} onClick={() => setSubAnimation(a.value)}
                                                className={`py-1 rounded text-[11px] font-medium border transition-all ${subAnimation === a.value ? 'bg-yellow-500/20 border-yellow-500/50 text-yellow-300' : 'bg-white/5 border-white/5 text-zinc-400'}`}>
                                                {a.label}
                                            </button>
                                        ))}
                                    </div>
                                </div>

                                {/* Font */}
                                <div>
                                    <label className="text-[11px] font-bold text-zinc-400 uppercase mb-1 block">Font</label>
                                    <select value={subFontName} onChange={e => setSubFontName(e.target.value)}
                                        className="w-full bg-black/40 border border-white/10 rounded-lg p-1.5 text-xs text-white">
                                        {FONT_OPTIONS.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
                                    </select>
                                </div>

                                {/* Text Color */}
                                <div>
                                    <label className="text-[11px] font-bold text-zinc-400 uppercase mb-1 block">Text Color</label>
                                    <div className="flex flex-wrap gap-1">
                                        {COLOR_PRESETS.map(c => (
                                            <button key={c.color} onClick={() => setSubFontColor(c.color)}
                                                className={`w-6 h-6 rounded-full border-2 transition-all ${subFontColor === c.color ? 'border-white scale-110' : 'border-white/20'}`}
                                                style={{ backgroundColor: c.color }} />
                                        ))}
                                        <label className="w-6 h-6 rounded-full border-2 border-dashed border-white/20 cursor-pointer flex items-center justify-center text-[10px] text-zinc-400 hover:border-white/50">
                                            +
                                            <input type="color" value={subFontColor} onChange={e => setSubFontColor(e.target.value)} className="sr-only" />
                                        </label>
                                    </div>
                                </div>

                                {/* Editable transcript */}
                                {captions.length > 0 && (
                                    <div>
                                        <button onClick={() => setShowTextEditor(v => !v)}
                                            className="text-[11px] font-bold text-zinc-400 uppercase flex items-center gap-1 mb-1">
                                            Edit Text ({captions.length} words)
                                            {showTextEditor ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                                        </button>
                                        {showTextEditor && (
                                            <textarea value={editableText} onChange={e => handleTextEdit(e.target.value)}
                                                rows={3}
                                                className="w-full bg-black/40 border border-white/10 rounded-lg p-2 text-xs text-white resize-none" />
                                        )}
                                    </div>
                                )}
                            </div>
                        </Section>

                        {/* Section: Viral Hook */}
                        <Section
                            title="Viral Hook"
                            icon={<Sparkles size={14} />}
                            colorClass="text-yellow-300"
                            enabled={enableHook}
                            onToggle={() => setEnableHook(v => !v)}
                            expanded={expandedSections.hook}
                            onToggleExpand={() => toggleSection('hook')}
                        >
                            <div className="space-y-3">
                                <textarea
                                    value={hookText}
                                    onChange={e => setHookText(e.target.value)}
                                    rows={2}
                                    placeholder="POV: You just discovered..."
                                    className="w-full bg-black/40 border border-white/10 rounded-lg p-2 text-xs text-white resize-none placeholder-zinc-600"
                                />

                                {/* Position */}
                                <div>
                                    <label className="text-[11px] font-bold text-zinc-400 uppercase mb-1 block">Position</label>
                                    <div className="grid grid-cols-3 gap-1">
                                        {[['top', 'Top'], ['center', 'Center'], ['bottom', 'Bottom']].map(([val, label]) => (
                                            <button key={val} onClick={() => setHookPosition(val)}
                                                className={`py-1 rounded text-[11px] font-medium border transition-all ${hookPosition === val ? 'bg-white text-black border-white' : 'bg-white/5 border-white/5 text-zinc-400'}`}>
                                                {label}
                                            </button>
                                        ))}
                                    </div>
                                </div>

                                {/* Size + Entrance */}
                                <div className="grid grid-cols-2 gap-2">
                                    <div>
                                        <label className="text-[11px] font-bold text-zinc-400 uppercase mb-1 block">Size</label>
                                        <div className="grid grid-cols-3 gap-1">
                                            {['S', 'M', 'L'].map(s => (
                                                <button key={s} onClick={() => setHookSize(s)}
                                                    className={`py-1 rounded text-[11px] font-medium border transition-all ${hookSize === s ? 'bg-white text-black border-white' : 'bg-white/5 border-white/5 text-zinc-400'}`}>
                                                    {s}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                    <div>
                                        <label className="text-[11px] font-bold text-zinc-400 uppercase mb-1 block">Entrance</label>
                                        <div className="grid grid-cols-2 gap-1">
                                            {[['spring', 'Bounce'], ['fade', 'Fade']].map(([val, label]) => (
                                                <button key={val} onClick={() => setHookEntrance(val)}
                                                    className={`py-1 rounded text-[11px] font-medium border transition-all ${hookEntrance === val ? 'bg-white text-black border-white' : 'bg-white/5 border-white/5 text-zinc-400'}`}>
                                                    {label}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                </div>

                                {/* Duration */}
                                <div>
                                    <label className="text-[11px] font-bold text-zinc-400 uppercase mb-1 block">Duration: {hookDuration}s</label>
                                    <input type="range" min="2" max="15" value={hookDuration}
                                        onChange={e => setHookDuration(parseInt(e.target.value))}
                                        className="w-full accent-yellow-500" />
                                </div>
                            </div>
                        </Section>

                    </div>

                    {/* Submit */}
                    <button
                        onClick={() => {
                            onSubmit({
                                enableAutoEdit,
                                enableSubtitles,
                                enableHook,
                                effectsConfig: enableAutoEdit ? effectsConfig : null,
                                subtitleConfig: enableSubtitles ? subtitleConfig : null,
                                hookConfig: enableHook && hookText.trim() ? hookConfig : null,
                            });
                        }}
                        disabled={!canSubmit}
                        className="w-full py-3 mt-4 bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 text-white font-bold rounded-xl shadow-lg transition-all active:scale-[0.98] flex items-center justify-center gap-2 shrink-0 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                        {isProcessing ? <Loader2 size={20} className="animate-spin" /> : <Wand2 size={20} />}
                        {isProcessing ? 'Processing...' : 'Render All'}
                    </button>
                </div>
            </div>
        </div>
    );
}

/* --- Section Component --- */
function Section({ title, icon, colorClass, enabled, onToggle, expanded, onToggleExpand, loading, error, onRetry, children }) {
    return (
        <div className={`rounded-xl border transition-all ${enabled ? 'border-white/15 bg-white/[0.03]' : 'border-white/5 bg-white/[0.01]'}`}>
            {/* Header */}
            <div className="flex items-center gap-2 px-3 py-2.5">
                <span className={colorClass}>{icon}</span>
                <span className={`text-sm font-bold flex-1 ${enabled ? 'text-white' : 'text-zinc-400'}`}>{title}</span>

                {/* Toggle */}
                <button
                    onClick={onToggle}
                    className={`relative w-9 h-5 rounded-full transition-all ${enabled ? 'bg-primary' : 'bg-zinc-700'}`}
                >
                    <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-all ${enabled ? 'left-[18px]' : 'left-0.5'}`} />
                </button>

                {/* Expand/collapse */}
                <button onClick={onToggleExpand} className="text-zinc-500 hover:text-zinc-300 ml-1">
                    {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                </button>
            </div>

            {/* Body */}
            {expanded && (
                <div className="px-3 pb-3">
                    {loading && (
                        <div className="flex items-center gap-2 text-zinc-400 py-2">
                            <Loader2 size={14} className="animate-spin text-zinc-500" />
                            <span className="text-xs">Generating...</span>
                        </div>
                    )}
                    {error && (
                        <div className="flex items-center gap-2 py-2">
                            <AlertCircle size={14} className="text-red-400 shrink-0" />
                            <span className="text-xs text-red-400">{error}</span>
                            {onRetry && (
                                <button onClick={onRetry} className="ml-auto text-xs text-zinc-400 hover:text-white underline">Retry</button>
                            )}
                        </div>
                    )}
                    {!loading && !error && children}
                </div>
            )}
        </div>
    );
}
