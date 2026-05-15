import React, { useState, useEffect } from 'react';
import { X, Wand2, Loader2, AlertCircle, CheckCircle } from 'lucide-react';
import { getApiUrl } from '../config';
import RemotionPreview from './RemotionPreview';

const STYLE_PRESETS = [
    { value: 'auto', label: 'Auto (AI)', desc: 'Let AI decide' },
    { value: 'cinematic', label: 'Cinematic', desc: 'Zoom & color' },
    { value: 'dynamic', label: 'Dynamic', desc: 'Fast zooms' },
    { value: 'minimal', label: 'Minimal', desc: 'Subtle zoom' },
];

export default function AutoEditModal({
    isOpen,
    onClose,
    onApply,
    isProcessing,
    videoUrl,
    jobId,
    clipIndex,
    clipDuration,
    existingSubtitles,
    existingHook,
    apiKey,
    baseUrl,
    provider,
    model,
}) {
    const [effectsConfig, setEffectsConfig] = useState(null);
    const [step, setStep] = useState('idle'); // idle | generating | ready | error
    const [error, setError] = useState(null);
    const [localDuration, setLocalDuration] = useState(clipDuration || 30);

    // Fetch clip duration if not provided
    useEffect(() => {
        if (!isOpen || clipDuration) return;
        fetch(getApiUrl(`/api/clip/${jobId}/${clipIndex}/transcript`))
            .then(res => res.ok ? res.json() : null)
            .then(data => {
                if (data && data.durationSec) setLocalDuration(data.durationSec);
            })
            .catch(() => {});
    }, [isOpen, jobId, clipIndex, clipDuration]);

    // Auto-generate effects when modal opens
    useEffect(() => {
        if (!isOpen || !jobId || clipIndex === undefined) return;
        if (step !== 'idle') return;

        const sendFilename = videoUrl && !videoUrl.startsWith('blob:')
            ? videoUrl.split('/').pop()
            : undefined;

        setStep('generating');
        setError(null);
        setEffectsConfig(null);

        fetch(getApiUrl('/api/effects/generate'), {
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
        })
            .then(res => {
                if (!res.ok) return res.text().then(t => Promise.reject(t));
                return res.json();
            })
            .then(data => {
                if (data.effects && data.effects.segments) {
                    setEffectsConfig(data.effects);
                    setStep('ready');
                } else {
                    throw new Error('No effects returned from AI.');
                }
            })
            .catch(err => {
                console.error('[AutoEditModal] Generation error:', err);
                let msg = String(err);
                try { msg = JSON.parse(msg).detail || msg; } catch (_) {}
                setError(msg);
                setStep('error');
            });
    }, [isOpen]); // eslint-disable-line react-hooks/exhaustive-deps

    const handleApply = () => {
        if (!effectsConfig) return;
        onApply(effectsConfig);
        onClose();
    };

    const handleRetry = () => {
        setStep('idle');
    };

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-[fadeIn_0.2s_ease-out]">
            <div className="bg-[#121214] border border-white/10 p-6 rounded-2xl w-full max-w-5xl shadow-2xl relative flex flex-col md:flex-row gap-6 max-h-[90vh]">
                <button
                    onClick={onClose}
                    className="absolute top-4 right-4 text-zinc-500 hover:text-white z-10"
                >
                    <X size={20} />
                </button>

                {/* Left: Preview */}
                <div className="flex-1 flex flex-col items-center justify-center bg-black rounded-lg border border-white/5 overflow-hidden relative aspect-[9/16] max-h-[600px]">
                    {step === 'idle' || step === 'generating' ? (
                        <div className="flex flex-col items-center justify-center gap-4 p-8 text-center">
                            <Loader2 size={40} className="text-purple-400 animate-spin" />
                            <div>
                                <p className="text-white font-bold text-sm">AI Magic in Progress...</p>
                                <p className="text-zinc-400 text-xs mt-1">Analyzing video & generating viral effects</p>
                            </div>
                        </div>
                    ) : step === 'error' ? (
                        <div className="flex flex-col items-center justify-center gap-4 p-8 text-center">
                            <AlertCircle size={40} className="text-red-400" />
                            <div>
                                <p className="text-white font-bold text-sm mb-1">Generation Failed</p>
                                <p className="text-red-400 text-xs max-w-xs">{error}</p>
                            </div>
                            <button
                                onClick={handleRetry}
                                className="px-4 py-2 bg-white/10 hover:bg-white/20 text-white text-xs font-bold rounded-lg transition-colors border border-white/10"
                            >
                                Retry
                            </button>
                        </div>
                    ) : (
                        <RemotionPreview
                            videoUrl={videoUrl}
                            durationInSeconds={clipDuration || localDuration}
                            effects={effectsConfig}
                            subtitles={existingSubtitles || null}
                            hook={existingHook || null}
                        />
                    )}
                </div>

                {/* Right: Controls */}
                <div className="w-full md:w-80 flex flex-col">
                    <h3 className="text-xl font-bold text-white mb-4 flex items-center gap-2 shrink-0">
                        <Wand2 className="text-purple-400" /> Auto Edit
                    </h3>

                    {step === 'ready' && effectsConfig && (
                        <div className="space-y-4 flex-1 overflow-y-auto custom-scrollbar pr-1">
                            {/* Effects Summary */}
                            <div className="p-3 bg-purple-500/10 border border-purple-500/20 rounded-lg">
                                <div className="flex items-center gap-2 text-purple-300 text-xs font-bold mb-2">
                                    <CheckCircle size={14} />
                                    Effects Generated
                                </div>
                                <div className="text-zinc-400 text-[11px] space-y-1">
                                    <div>{effectsConfig.segments?.length || 0} effect segments detected</div>
                                    {effectsConfig.zoomEffects && (
                                        <div>Zoom effects: {effectsConfig.zoomEffects.length || 0}</div>
                                    )}
                                </div>
                            </div>

                            {/* Effect Segments Preview */}
                            {effectsConfig.segments && effectsConfig.segments.length > 0 && (
                                <div>
                                    <label className="text-xs font-bold text-zinc-400 uppercase tracking-wider mb-2 block">
                                        Effect Timeline
                                    </label>
                                    <div className="space-y-1.5 max-h-40 overflow-y-auto custom-scrollbar">
                                        {effectsConfig.segments.map((seg, i) => (
                                            <div key={i} className="flex items-center gap-2 text-[10px] text-zinc-500 bg-white/5 rounded px-2 py-1.5">
                                                <span className="font-mono text-zinc-600 shrink-0">
                                                    {seg.startSec?.toFixed(1)}s
                                                </span>
                                                <span className="flex-1 truncate text-zinc-400">
                                                    {seg.type || 'effect'} {seg.description ? `- ${seg.description}` : ''}
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Tip */}
                            <div className="p-3 bg-white/5 rounded-lg border border-white/5 text-[11px] text-zinc-400">
                                <strong className="text-zinc-300">Tip:</strong> The AI analyzes your video to add dynamic zooms, cuts, and color grading for maximum viral potential.
                            </div>
                        </div>
                    )}

                    {step === 'generating' && (
                        <div className="flex-1 flex items-center justify-center">
                            <p className="text-zinc-500 text-sm text-center">Generating effects configuration...</p>
                        </div>
                    )}

                    {step === 'error' && (
                        <div className="flex-1 flex items-center justify-center">
                            <p className="text-red-400 text-sm text-center">Failed to generate effects. Click Retry or close.</p>
                        </div>
                    )}

                    {/* Apply Button */}
                    <button
                        onClick={handleApply}
                        disabled={step !== 'ready' || isProcessing}
                        className="w-full py-3 mt-4 bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 text-white font-bold rounded-xl shadow-lg shadow-purple-500/20 transition-all active:scale-[0.98] flex items-center justify-center gap-2 shrink-0 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                        {isProcessing ? (
                            <><Loader2 size={20} className="animate-spin" /> Applying...</>
                        ) : (
                            <><Wand2 size={20} /> Apply Effects</>
                        )}
                    </button>
                </div>
            </div>
        </div>
    );
}
