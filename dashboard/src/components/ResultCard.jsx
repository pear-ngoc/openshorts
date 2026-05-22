import React, { useState, useEffect, useRef } from 'react';
import { Download, Share2, Instagram, Youtube, Video, CheckCircle, AlertCircle, X, Loader2, Copy, Wand2, Calendar, Clock, Languages, Send } from 'lucide-react';
import { getApiUrl } from '../config';
import TranslateModal from './TranslateModal';
import CombinedEditModal from './CombinedEditModal';
import { renderViaService } from '../lib/renderViaService';

async function fetchAndDownload(url, filename, jobId, clipIndex) {
    const response = await fetch(url);
    if (!response.ok) throw new Error('Download failed');
    const blob = await response.blob();
    const objUrl = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.style.display = 'none';
    a.href = objUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(objUrl);
    document.body.removeChild(a);
    fetch(getApiUrl('/api/jobs/downloaded'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: jobId, clip_index: clipIndex })
    }).catch(() => {});
}

export default function ResultCard({ clip, index, jobId, uploadPostKey, uploadUserId, geminiApiKey, geminiBaseUrl, llmProvider, llmModel, elevenLabsKey, onPlay, onPause }) {
    const [showModal, setShowModal] = useState(false);
    const videoRef = useRef(null);

    // Canonical source URL from backend (changes when server-side edits happen)
    const [originalVideoUrl, setOriginalVideoUrl] = useState(getApiUrl(clip.video_url));

    // Active video source for the player; guarded against stale polling
    const [currentVideoUrl, setCurrentVideoUrl] = useState(originalVideoUrl);

    // Track all rendered outputs from this card; newest is always at end.
    // Each entry: { blobUrl, serverUrl, filename, version }
    const [renderedOutputs, setRenderedOutputs] = useState([]);

    // Sync originalVideoUrl when the server prop changes (e.g., job re-poll).
    // Does NOT reset currentVideoUrl if we have a pending local render — the
    // stale-guard in the render effect handles that.
    useEffect(() => {
        const newUrl = getApiUrl(clip.video_url);
        if (newUrl !== originalVideoUrl) {
            setOriginalVideoUrl(newUrl);
            // Only sync currentVideoUrl when there's no pending local render
            if (renderedOutputs.length === 0) {
                setCurrentVideoUrl(newUrl);
            }
        }
    }, [clip.video_url]); // eslint-disable-line react-hooks/exhaustive-deps

    // Reload the video element whenever currentVideoUrl changes.
    useEffect(() => {
        if (videoRef.current) {
            videoRef.current.load();
        }
    }, [currentVideoUrl]);

    // Accumulate Remotion layers across operations
    const [activeLayers, setActiveLayers] = useState({ subtitles: null, hook: null, effects: null });

    // Reset Remotion layers when the base clip changes, since those layers
    // are tied to the original source video.
    useEffect(() => {
        setActiveLayers({ subtitles: null, hook: null, effects: null });
    }, [originalVideoUrl]);

    const [platforms, setPlatforms] = useState({
        tiktok: true,
        instagram: true,
        youtube: true
    });
    const [postTitle, setPostTitle] = useState("");
    const [postDescription, setPostDescription] = useState("");
    const [isScheduling, setIsScheduling] = useState(false);
    const [scheduleDate, setScheduleDate] = useState("");

    const [posting, setPosting] = useState(false);
    const [postResult, setPostResult] = useState(null);

    const [isTranslating, setIsTranslating] = useState(false);
    const [isSendingToTL, setIsSendingToTL] = useState(false);
    const [tlResult, setTLResult] = useState(null);
    const [showTranslateModal, setShowTranslateModal] = useState(false);
    const [showCombinedModal, setShowCombinedModal] = useState(false);
    const [editError, setEditError] = useState(null);
    const [processingStage, setProcessingStage] = useState(null);

    const [clipDuration, setClipDuration] = useState(clip.end && clip.start ? clip.end - clip.start : 30);

    // Fetch clip duration from transcript endpoint
    useEffect(() => {
        if (!jobId || index === undefined) return;
        fetch(getApiUrl(`/api/clip/${jobId}/${index}/transcript`))
            .then(res => res.ok ? res.json() : null)
            .then(data => {
                if (data && data.durationSec) setClipDuration(data.durationSec);
            })
            .catch(() => {});
    }, [jobId, index]);

    // Initialize/Reset form when modal opens
    useEffect(() => {
        if (showModal) {
            setPostTitle(clip.video_title_for_youtube_short || "Viral Short");
            setPostDescription(clip.video_description_for_instagram || clip.video_description_for_tiktok || "");
            setIsScheduling(false);
            setScheduleDate("");
            setPostResult(null);
        }
    }, [showModal, clip]);

    /**
     * Unified handler for combined editor - all layers applied in one render pass,
     * output saved to server with proper naming, then reflected in the client.
     */
    // Derive the most recent render output
    const latestOutput = renderedOutputs.length > 0
        ? renderedOutputs[renderedOutputs.length - 1]
        : null;

    // Callback for the modal's Download Latest button
    const handleDownloadLatest = async () => {
        if (!latestOutput) return;
        const { serverUrl, blobUrl, filename, version } = latestOutput;
        const urlToFetch = version
            ? `${serverUrl || blobUrl}?v=${version}`
            : (serverUrl || blobUrl);
        try {
            await fetchAndDownload(urlToFetch, filename, jobId, index);
        } catch (err) {
            console.error('Download error:', err);
        }
    };

    // Reset rendered outputs when the base clip changes
    useEffect(() => {
        setRenderedOutputs([]);
    }, [originalVideoUrl]);

    const handleCombinedEdit = async ({
        enableAutoEdit,
        enableSubtitles,
        enableHook,
        effectsConfig,
        subtitleConfig,
        hookConfig,
    }) => {
        setProcessingStage('rendering');
        setEditError(null);

        try {
            const layers = {
                subtitles: enableSubtitles && subtitleConfig ? subtitleConfig : null,
                hook: enableHook && hookConfig ? hookConfig : null,
                effects: enableAutoEdit && effectsConfig ? effectsConfig : null,
            };
            const hasAnyLayer = layers.subtitles || layers.hook || layers.effects;

            if (hasAnyLayer) {
                setProcessingStage('rendering');
                const result = await renderViaService({
                    jobId,
                    clipIndex: index,
                    videoUrl: originalVideoUrl,
                    durationInSeconds: clipDuration,
                    subtitles: layers.subtitles,
                    hook: layers.hook,
                    effects: layers.effects,
                });

                const latestOutput = {
                    blobUrl: result.blobUrl,
                    serverUrl: result.serverUrl,
                    filename: result.filename,
                    version: result.version,
                };

                setRenderedOutputs(prev => [...prev, latestOutput]);

                const playerUrl = result.version
                    ? `${result.serverUrl || result.blobUrl}?v=${result.version}`
                    : (result.serverUrl || result.blobUrl);
                setCurrentVideoUrl(playerUrl);
                setActiveLayers(layers);
                if (videoRef.current) videoRef.current.load();
            }

            setShowCombinedModal(false);
        } catch (e) {
            console.error('[CombinedEdit] Error:', e);
            setEditError(e.message);
            setTimeout(() => setEditError(null), 8000);
        } finally {
            setProcessingStage(null);
        }
    };

    // Legacy: TranslateModal is still separate

    const handleTranslate = async (options) => {
        console.log('[Translate] Starting translation with options:', options);
        setIsTranslating(true);
        setEditError(null);
        try {
            const apiKey = elevenLabsKey;
            console.log('[Translate] API Key available:', !!apiKey);

            if (!apiKey) {
                throw new Error("ElevenLabs API Key is missing. Please set it in Settings.");
            }

            const requestBody = {
                job_id: jobId,
                clip_index: index,
                target_language: options.targetLanguage,
                ...(currentVideoUrl && !currentVideoUrl.startsWith('blob:') ? { input_filename: currentVideoUrl.split('/').pop() } : {})
            };
            console.log('[Translate] Request body:', requestBody);
            console.log('[Translate] Sending request to /api/translate');

            const res = await fetch(getApiUrl('/api/translate'), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-ElevenLabs-Key': apiKey
                },
                body: JSON.stringify(requestBody)
            });

            console.log('[Translate] Response status:', res.status);

            if (!res.ok) {
                const errText = await res.text();
                console.error('[Translate] Error response:', errText);
                try {
                    const jsonErr = JSON.parse(errText);
                    throw new Error(jsonErr.detail || errText);
                } catch (e) {
                    if (e.message !== errText) throw e;
                    throw new Error(errText);
                }
            }

            const data = await res.json();
            console.log('[Translate] Success response:', data);
            if (data.new_video_url) {
                const newUrl = getApiUrl(data.new_video_url);
                setCurrentVideoUrl(newUrl);
                setRenderedOutputs(prev => [...prev, {
                    blobUrl: null,
                    serverUrl: newUrl,
                    filename: `${jobId}_clip_${index + 1}.mp4`,
                    version: null,
                }]);
                if (videoRef.current) {
                    videoRef.current.load();
                }
                setShowTranslateModal(false);
            }

        } catch (e) {
            console.error('[Translate] Exception:', e);
            setEditError(e.message);
            setTimeout(() => setEditError(null), 5000);
        } finally {
            setIsTranslating(false);
        }
    };

    const handlePost = async () => {
        if (!uploadPostKey || !uploadUserId) {
            setPostResult({ success: false, msg: "Missing API Key or User ID." });
            return;
        }

        const selectedPlatforms = Object.keys(platforms).filter(k => platforms[k]);
        if (selectedPlatforms.length === 0) {
            setPostResult({ success: false, msg: "Select at least one platform." });
            return;
        }

        if (isScheduling && !scheduleDate) {
            setPostResult({ success: false, msg: "Please select a date and time." });
            return;
        }

        setPosting(true);
        setPostResult(null);

        try {
            const payload = {
                job_id: jobId,
                clip_index: index,
                api_key: uploadPostKey,
                user_id: uploadUserId,
                platforms: selectedPlatforms,
                title: postTitle,
                description: postDescription
            };

            if (isScheduling && scheduleDate) {
                // Convert to ISO-8601
                payload.scheduled_date = new Date(scheduleDate).toISOString();
                // Optional: pass timezone if needed, backend defaults to UTC or we can send user's timezone
                payload.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
            }

            const res = await fetch(getApiUrl('/api/social/post'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!res.ok) {
                const errText = await res.text();
                try {
                    const jsonErr = JSON.parse(errText);
                    throw new Error(jsonErr.detail || errText);
                } catch (e) {
                    throw new Error(errText);
                }
            }

            setPostResult({ success: true, msg: isScheduling ? "Scheduled successfully!" : "Posted successfully!" });
            setTimeout(() => {
                setShowModal(false);
                setPostResult(null);
            }, 3000);

        } catch (e) {
            setPostResult({ success: false, msg: `Failed: ${e.message}` });
        } finally {
            setPosting(false);
        }
    };

    return (
        <div className="bg-surface border border-white/5 rounded-2xl overflow-hidden flex flex-col md:flex-row group hover:border-white/10 transition-all animate-[fadeIn_0.5s_ease-out] min-h-[300px] h-auto" style={{ animationDelay: `${index * 0.1}s` }}>
            {/* Left: Video Preview (Responsive Width) */}
            <div className="w-full md:w-[180px] lg:w-[200px] bg-black relative shrink-0 aspect-[9/16] md:aspect-auto group/video">
                <video
                    ref={videoRef}
                    src={currentVideoUrl}
                    controls
                    className="w-full h-full object-cover"
                    playsInline
                    onPlay={() => {
                        const currentTime = videoRef.current ? videoRef.current.currentTime : 0;
                        onPlay && onPlay(clip.start + currentTime);
                    }}
                    onPause={() => onPause && onPause()}
                    onEnded={() => {
                        if (videoRef.current) {
                            videoRef.current.currentTime = 0;
                            videoRef.current.play();
                        }
                    }}
                />
                <div className="absolute top-3 left-3 flex gap-2">
                    <span className="bg-black/60 backdrop-blur-md text-white text-[10px] font-bold px-2 py-1 rounded-md border border-white/10 uppercase tracking-wide">
                        Clip {index + 1}
                    </span>
                </div>

                {/* Processing Overlay */}
                {processingStage !== null && (
                    <div className="absolute inset-0 bg-black/60 backdrop-blur-sm flex flex-col items-center justify-center z-10 p-4 text-center">
                        <Loader2 size={32} className="text-primary animate-spin mb-3" />
                        <span className="text-xs font-bold text-white uppercase tracking-wider">
                            {processingStage === 'saving' ? 'Saving...' : 'Rendering...'}
                        </span>
                        <span className="text-[10px] text-zinc-400 mt-1">Applying edits, subtitles & hooks</span>
                    </div>
                )}
            </div>

            {/* Right: Content & Details */}
            <div className="flex-1 p-4 md:p-5 flex flex-col bg-[#121214] overflow-hidden min-w-0">
                <div className="mb-4">
                    <h3 className="text-base font-bold text-white leading-tight line-clamp-2 mb-2 break-words" title={clip.video_title_for_youtube_short}>
                        {clip.video_title_for_youtube_short || "Viral Clip Generated"}
                    </h3>
                    <div className="flex flex-wrap gap-2 text-[10px] text-zinc-500 font-mono">
                        <span className="bg-white/5 px-1.5 py-0.5 rounded border border-white/5 shrink-0">{Math.floor(clip.end - clip.start)}s</span>
                        <span className="bg-white/5 px-1.5 py-0.5 rounded border border-white/5 shrink-0">#shorts</span>
                        <span className="bg-white/5 px-1.5 py-0.5 rounded border border-white/5 shrink-0">#viral</span>
                    </div>
                </div>

                {/* Scrollable Descriptions Area */}
                <div className="flex-1 overflow-y-auto custom-scrollbar space-y-3 pr-2 mb-4">
                    {/* YouTube */}
                    <div className="bg-black/20 rounded-lg p-3 border border-white/5">
                        <div className="flex items-center gap-2 text-[10px] font-bold text-red-400 mb-1.5 uppercase tracking-wider">
                            <Youtube size={12} className="shrink-0" /> <span className="truncate">YouTube Title</span>
                        </div>
                        <p className="text-xs text-zinc-300 select-all break-words">
                            {clip.video_title_for_youtube_short || "Viral Short Video"}
                        </p>
                    </div>

                    {/* TikTok / IG */}
                    <div className="bg-black/20 rounded-lg p-3 border border-white/5">
                        <div className="flex items-center gap-2 text-[10px] font-bold text-zinc-400 mb-1.5 uppercase tracking-wider">
                            <Video size={12} className="text-cyan-400 shrink-0" />
                            <span className="text-zinc-500">/</span>
                            <Instagram size={12} className="text-pink-400 shrink-0" />
                            <span className="truncate">Caption</span>
                        </div>
                        <p className="text-xs text-zinc-300 line-clamp-3 hover:line-clamp-none transition-all cursor-pointer select-all break-words">
                            {clip.video_description_for_tiktok || clip.video_description_for_instagram}
                        </p>
                    </div>
                </div>

                {/* Error Message */}
                {editError && (
                    <div className="mb-3 p-2 bg-red-500/10 border border-red-500/20 text-red-400 text-[10px] rounded-lg flex items-center gap-2">
                        <AlertCircle size={12} className="shrink-0" />
                        {editError}
                    </div>
                )}
                {tlResult && (
                    <div className={`mb-3 p-2 rounded-lg text-[10px] flex items-center gap-2 ${tlResult.success ? 'bg-green-500/10 border border-green-500/20 text-green-400' : 'bg-red-500/10 border border-red-500/20 text-red-400'}`}>
                        {tlResult.success ? <CheckCircle size={12} className="shrink-0" /> : <AlertCircle size={12} className="shrink-0" />}
                        {tlResult.msg}
                    </div>
                )}

                {/* Actions Footer */}
                <div className="grid grid-cols-2 gap-3 mt-auto pt-4 border-t border-white/5">
                    <button
                        onClick={() => {
                            if (!geminiApiKey && !localStorage.getItem('gemini_key')) {
                                setEditError("Gemini API Key is missing. Please set it in Settings.");
                                setTimeout(() => setEditError(null), 5000);
                                return;
                            }
                            setShowCombinedModal(true);
                        }}
                        disabled={processingStage !== null}
                        className="col-span-1 py-2 bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 text-white rounded-lg text-xs font-bold shadow-lg shadow-purple-500/20 transition-all active:scale-[0.98] flex items-center justify-center gap-2 mb-1 truncate px-1"
                    >
                        {processingStage !== null ? <Loader2 size={14} className="animate-spin" /> : <Wand2 size={14} />}
                        {processingStage !== null ? 'Editing...' : 'Edit All'}
                    </button>

                    <button
                        onClick={() => setShowTranslateModal(true)}
                        disabled={isTranslating}
                        className="col-span-1 py-2 bg-gradient-to-r from-green-500 to-teal-600 hover:from-green-400 hover:to-teal-500 text-white rounded-lg text-xs font-bold shadow-lg shadow-green-500/20 transition-all active:scale-[0.98] flex items-center justify-center gap-2 mb-1 truncate px-1"
                    >
                        {isTranslating ? <Loader2 size={14} className="animate-spin" /> : <Languages size={14} />}
                        {isTranslating ? 'Translating...' : 'Dub Voice'}
                    </button>

                    <button
                        onClick={() => setShowModal(true)}
                        className="col-span-1 py-2 bg-primary hover:bg-blue-600 text-white rounded-lg text-xs font-bold shadow-lg shadow-primary/20 transition-all active:scale-[0.98] flex items-center justify-center gap-2 truncate px-2"
                    >
                        <Share2 size={14} className="shrink-0" /> Post
                    </button>
                    <button
                        onClick={async (e) => {
                            e.preventDefault();
                            e.stopPropagation();

                            if (latestOutput) {
                                const { serverUrl, blobUrl, filename, version } = latestOutput;
                                const urlToFetch = version
                                    ? `${serverUrl || blobUrl}?v=${version}`
                                    : (serverUrl || blobUrl);
                                setIsSendingToTL(true);
                                try {
                                    await fetchAndDownload(urlToFetch, filename, jobId, index);
                                    setTLResult({ success: true, msg: 'Downloaded!' });
                                } catch (err) {
                                    console.error('Download error:', err);
                                    setTLResult({ success: false, msg: 'Download failed' });
                                } finally {
                                    setIsSendingToTL(false);
                                    setTimeout(() => setTLResult(null), 4000);
                                }
                                return;
                            }

                            setIsSendingToTL(true);
                            setTLResult(null);
                            try {
                                const response = await fetch(currentVideoUrl);
                                if (!response.ok) throw new Error('Download failed');
                                const blob = await response.blob();
                                const url = window.URL.createObjectURL(blob);
                                const a = document.createElement('a');
                                a.style.display = 'none';
                                a.href = url;
                                a.download = `${jobId}_clip_${index + 1}.mp4`;
                                document.body.appendChild(a);
                                a.click();
                                window.URL.revokeObjectURL(url);
                                document.body.removeChild(a);
                                fetch(getApiUrl('/api/jobs/downloaded'), {
                                    method: 'POST',
                                    headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify({ job_id: jobId, clip_index: index })
                                }).catch(() => {});
                                setTLResult({ success: true, msg: 'Downloaded!' });
                            } catch (err) {
                                console.error('Download error:', err);
                                window.open(currentVideoUrl, '_blank');
                                setTLResult({ success: false, msg: 'Download failed' });
                            } finally {
                                setIsSendingToTL(false);
                                setTimeout(() => setTLResult(null), 4000);
                            }
                        }}
                        disabled={isSendingToTL}
                        className={`col-span-1 py-2 rounded-lg text-xs font-medium transition-all active:scale-[0.98] flex items-center justify-center gap-2 border truncate px-2 ${latestOutput ? 'bg-green-500/20 border-green-500/30 text-green-400 hover:bg-green-500/30 shadow-green-500/10 shadow-lg' : 'bg-white/5 hover:bg-white/10 text-zinc-300 hover:text-white border-white/5'}`}
                    >
                        {isSendingToTL ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} className="shrink-0" />}
                        {isSendingToTL ? 'Processing...' : latestOutput ? 'Download Rendered' : 'Download'}
                    </button>
                </div>
            </div>

            {/* Post Modal */}
            {showModal && (
                <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm animate-[fadeIn_0.2s_ease-out]">
                    <div className="bg-[#121214] border border-white/10 p-6 rounded-2xl w-full max-w-md shadow-2xl relative max-h-[90vh] overflow-y-auto custom-scrollbar">
                        <button
                            onClick={() => setShowModal(false)}
                            className="absolute top-4 right-4 text-zinc-500 hover:text-white"
                        >
                            <X size={20} />
                        </button>

                        <h3 className="text-lg font-bold text-white mb-4">Post / Schedule</h3>

                        {!uploadPostKey && (
                            <div className="mb-4 p-3 bg-yellow-500/10 border border-yellow-500/20 text-yellow-200 text-xs rounded-lg flex items-start gap-2">
                                <AlertCircle size={14} className="mt-0.5 shrink-0" />
                                <div>Configure API Key in Settings first.</div>
                            </div>
                        )}

                        <div className="space-y-4 mb-6">
                            {/* Title & Description */}
                            <div>
                                <label className="block text-xs font-bold text-zinc-400 mb-1">Video Title</label>
                                <input
                                    type="text"
                                    value={postTitle}
                                    onChange={(e) => setPostTitle(e.target.value)}
                                    className="w-full bg-black/40 border border-white/10 rounded-lg p-2 text-sm text-white focus:outline-none focus:border-primary/50 placeholder-zinc-600"
                                    placeholder="Enter a catchy title..."
                                />
                            </div>

                            <div>
                                <label className="block text-xs font-bold text-zinc-400 mb-1">Caption / Description</label>
                                <textarea
                                    value={postDescription}
                                    onChange={(e) => setPostDescription(e.target.value)}
                                    rows={4}
                                    className="w-full bg-black/40 border border-white/10 rounded-lg p-2 text-sm text-white focus:outline-none focus:border-primary/50 placeholder-zinc-600 resize-none"
                                    placeholder="Write a caption for your post..."
                                />
                            </div>

                            {/* Scheduling */}
                            <div className="p-3 bg-white/5 rounded-lg border border-white/5">
                                <div className="flex items-center justify-between mb-2">
                                    <div className="flex items-center gap-2 text-sm text-white font-medium">
                                        <Calendar size={16} className="text-purple-400" /> Schedule Post
                                    </div>
                                    <label className="relative inline-flex items-center cursor-pointer">
                                        <input type="checkbox" checked={isScheduling} onChange={(e) => setIsScheduling(e.target.checked)} className="sr-only peer" />
                                        <div className="w-9 h-5 bg-zinc-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-purple-600"></div>
                                    </label>
                                </div>

                                {isScheduling && (
                                    <div className="mt-3 animate-[fadeIn_0.2s_ease-out]">
                                        <label className="block text-xs text-zinc-400 mb-1">Select Date & Time</label>
                                        <div className="relative">
                                            <input
                                                type="datetime-local"
                                                value={scheduleDate}
                                                onChange={(e) => setScheduleDate(e.target.value)}
                                                className="w-full bg-black/40 border border-white/10 rounded-lg p-2 pl-9 text-sm text-white focus:outline-none focus:border-purple-500/50 [color-scheme:dark]"
                                            />
                                            <Clock size={14} className="absolute left-3 top-2.5 text-zinc-500" />
                                        </div>
                                    </div>
                                )}
                            </div>

                            {/* Platforms */}
                            <div>
                                <label className="block text-xs font-bold text-zinc-400 mb-2">Select Platforms</label>
                                <div className="grid grid-cols-1 gap-2">
                                    <label className="flex items-center gap-3 p-3 bg-white/5 rounded-lg cursor-pointer hover:bg-white/10 transition-colors border border-white/5">
                                        <input type="checkbox" checked={platforms.tiktok} onChange={e => setPlatforms({ ...platforms, tiktok: e.target.checked })} className="w-4 h-4 rounded border-zinc-600 bg-black/50 text-primary focus:ring-primary" />
                                        <div className="flex items-center gap-2 text-sm text-white"><Video size={16} className="text-cyan-400" /> TikTok</div>
                                    </label>
                                    <label className="flex items-center gap-3 p-3 bg-white/5 rounded-lg cursor-pointer hover:bg-white/10 transition-colors border border-white/5">
                                        <input type="checkbox" checked={platforms.instagram} onChange={e => setPlatforms({ ...platforms, instagram: e.target.checked })} className="w-4 h-4 rounded border-zinc-600 bg-black/50 text-primary focus:ring-primary" />
                                        <div className="flex items-center gap-2 text-sm text-white"><Instagram size={16} className="text-pink-400" /> Instagram</div>
                                    </label>
                                    <label className="flex items-center gap-3 p-3 bg-white/5 rounded-lg cursor-pointer hover:bg-white/10 transition-colors border border-white/5">
                                        <input type="checkbox" checked={platforms.youtube} onChange={e => setPlatforms({ ...platforms, youtube: e.target.checked })} className="w-4 h-4 rounded border-zinc-600 bg-black/50 text-primary focus:ring-primary" />
                                        <div className="flex items-center gap-2 text-sm text-white"><Youtube size={16} className="text-red-400" /> YouTube Shorts</div>
                                    </label>
                                </div>
                            </div>
                        </div>

                        {postResult && (
                            <div className={`mb-4 p-3 rounded-lg text-xs flex items-start gap-2 ${postResult.success ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'}`}>
                                {postResult.success ? <CheckCircle size={14} className="mt-0.5 shrink-0" /> : <AlertCircle size={14} className="mt-0.5 shrink-0" />}
                                <div>{postResult.msg}</div>
                            </div>
                        )}

                        <button
                            onClick={handlePost}
                            disabled={posting || !uploadPostKey}
                            className="w-full py-3 bg-primary hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed rounded-xl text-white font-bold transition-all flex items-center justify-center gap-2"
                        >
                            {posting ? <><Loader2 size={16} className="animate-spin" /> {isScheduling ? 'Scheduling...' : 'Publishing...'}</> : <><Share2 size={16} /> {isScheduling ? 'Schedule Post' : 'Publish Now'}</>}
                        </button>
                    </div>
                </div>
            )}

            <TranslateModal
                isOpen={showTranslateModal}
                onClose={() => setShowTranslateModal(false)}
                onTranslate={handleTranslate}
                isProcessing={isTranslating}
                videoUrl={currentVideoUrl}
                hasApiKey={!!elevenLabsKey}
            />

            <CombinedEditModal
                isOpen={showCombinedModal}
                onClose={() => setShowCombinedModal(false)}
                onSubmit={handleCombinedEdit}
                isProcessing={processingStage !== null}
                processingStage={processingStage}
                videoUrl={originalVideoUrl}
                jobId={jobId}
                clipIndex={index}
                clipDuration={clipDuration}
                apiKey={geminiApiKey || localStorage.getItem('gemini_key')}
                baseUrl={geminiBaseUrl || localStorage.getItem('gemini_base_url')}
                provider={llmProvider || localStorage.getItem('llm_provider') || 'gemini'}
                model={llmModel || localStorage.getItem('llm_model') || ''}
                existingHook={clip.viral_hook_text || ''}
                renderedOutputs={renderedOutputs}
                onDownloadLatest={handleDownloadLatest}
            />

        </div>
    );
}
