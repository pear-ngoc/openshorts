import { getApiUrl, RENDER_SERVICE_URL } from '../config';

/**
 * Normalizes a render-service output URL to a /videos/ path consumable by the app.
 *
 * Handles:
 *   - /render/output/<jobId>/<filename>           -> /videos/<jobId>/<filename>
 *   - http://renderer:3100/render/output/...     -> /videos/<jobId>/<filename>
 *   - /videos/<jobId>/<filename>                 -> /videos/<jobId>/<filename> (passthrough)
 *
 * Returns null if the URL cannot be normalized.
 */
function normalizeOutputUrl(rawUrl, jobId) {
    if (!rawUrl) return null;

    if (rawUrl.startsWith('/videos/')) return rawUrl;

    const rendererBase = `${RENDER_SERVICE_URL}/render/output/`;
    if (rawUrl.startsWith(rendererBase)) {
        const filename = rawUrl.slice(rendererBase.length);
        return `/videos/${jobId}/${filename}`;
    }

    const relPrefix = '/render/output/';
    if (rawUrl.startsWith(relPrefix)) {
        const withoutPrefix = rawUrl.slice(relPrefix.length); // "<jobId>/<filename>"
        const firstSlash = withoutPrefix.indexOf('/');
        if (firstSlash > 0) {
            const rid = withoutPrefix.slice(0, firstSlash);
            const filename = withoutPrefix.slice(firstSlash + 1);
            return `/videos/${rid}/${filename}`;
        }
    }

    console.warn(`[renderViaService] Unknown output URL format: ${rawUrl}`);
    return null;
}

/**
 * Renders via the server-side render-service (Chromium + FFmpeg).
 * Polls for completion, downloads the output, and uploads it to the backend.
 *
 * @param {object} params
 * @param {string} params.jobId - Backend job ID
 * @param {number} params.clipIndex - Clip index within the job
 * @param {string} params.videoUrl - Source video URL (relative or absolute)
 * @param {number} params.durationInSeconds - Video duration
 * @param {object|null} params.subtitles - SubtitleConfig
 * @param {object|null} params.hook - HookConfig
 * @param {object|null} params.effects - EffectsConfig
 * @param {function} [params.onProgress] - Progress callback (0-1)
 * @param {AbortSignal} [params.signal] - Abort signal for cancellation
 * @returns {Promise<{blobUrl: string, serverUrl: string, filename: string, version: number|null}>}
 */
export async function renderViaService({
    jobId,
    clipIndex,
    videoUrl,
    durationInSeconds = 30,
    subtitles = null,
    hook = null,
    effects = null,
    onProgress,
    signal,
}) {
    const fps = 30;
    const durationInFrames = Math.max(1, Math.round(durationInSeconds * fps));

    console.log('[renderViaService] Submitting render job...');
    console.log('[renderViaService] jobId:', jobId, '| clipIndex:', clipIndex);
    console.log('[renderViaService] videoUrl:', videoUrl);
    console.log('[renderViaService] durationInFrames:', durationInFrames);

    const resolvedVideoUrl = videoUrl.startsWith('http')
        ? videoUrl
        : `${getApiUrl('')}${videoUrl}`;

    // Step 1: Submit render job
    const submitResponse = await fetch(`${RENDER_SERVICE_URL}/render`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            jobId,
            clipIndex,
            props: {
                videoUrl: resolvedVideoUrl,
                durationInFrames,
                fps,
                width: 1080,
                height: 1920,
                subtitles,
                hook,
                effects,
            },
        }),
        signal,
    });

    if (!submitResponse.ok) {
        const errText = await submitResponse.text();
        throw new Error(`Render service submission failed: ${submitResponse.status} ${errText}`);
    }

    const { renderId } = await submitResponse.json();
    console.log(`[renderViaService] Job submitted: ${renderId}`);

    // Step 2: Poll for completion
    const pollStart = performance.now();
    let outputUrl = null;

    while (true) {
        if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');

        await new Promise(r => setTimeout(r, 2000));

        const statusResponse = await fetch(`${RENDER_SERVICE_URL}/render/${renderId}`, { signal });
        if (!statusResponse.ok) {
            throw new Error(`Poll failed: ${statusResponse.status}`);
        }

        const status = await statusResponse.json();
        const pct = Math.round((status.progress || 0) * 100);
        console.log(`[renderViaService] Status: ${status.status} | Progress: ${pct}% | Elapsed: ${Math.round(performance.now() - pollStart)}ms`);

        if (onProgress && typeof onProgress === 'function') {
            onProgress(status.progress || 0);
        }

        if (status.status === 'done') {
            outputUrl = status.outputUrl;
            // Normalize to app-consumable /videos/ path
            const normalized = normalizeOutputUrl(outputUrl, jobId);
            if (normalized) {
                outputUrl = normalized;
            } else {
                console.warn(`[renderViaService] Could not normalize: ${outputUrl}, will use save endpoint fallback`);
            }
            console.log(`[renderViaService] Render done! Output URL: ${outputUrl} (raw: ${status.outputUrl})`);
            break;
        }

        if (status.status === 'error') {
            throw new Error(`Render service error: ${status.error}`);
        }
    }

    // Step 3: Download output and create blob URL
    // Reconstruct renderer path for the actual fetch; normalized path is for app consumption
    let fetchUrl = outputUrl;
    if (!outputUrl.startsWith('http') && !outputUrl.startsWith('blob:') && !outputUrl.startsWith('/render/')) {
        if (outputUrl.startsWith('/videos/')) {
            const filename = outputUrl.replace(`/videos/${jobId}/`, '');
            fetchUrl = `${RENDER_SERVICE_URL}/render/output/${jobId}/${filename}`;
        }
    }

    console.log('[renderViaService] Downloading output from:', fetchUrl);
    const outputResponse = await fetch(fetchUrl, { signal });
    if (!outputResponse.ok) {
        throw new Error(`Failed to fetch output: ${outputResponse.status}`);
    }

    const blob = await outputResponse.blob();
    const blobUrl = URL.createObjectURL(blob);
    console.log(`[renderViaService] Blob created: ${blobUrl} (${blob.size} bytes)`);

    // Step 4: Upload blob to backend for persistence
    console.log('[renderViaService] Uploading to backend for persistence...');
    const formData = new FormData();
    formData.append('job_id', jobId);
    formData.append('clip_index', String(clipIndex));
    formData.append('file', blob, `rendered_clip_${clipIndex}.mp4`);

    let serverUrl = blobUrl;
    let downloadFilename = `${jobId}_clip_${clipIndex + 1}.mp4`;
    let serverVersion = null;

    try {
        const saveResponse = await fetch(getApiUrl('/api/render/save'), {
            method: 'POST',
            body: formData,
            signal,
        });

        if (saveResponse.ok) {
            const saveData = await saveResponse.json();
            serverUrl = getApiUrl(saveData.video_url);
            downloadFilename = saveData.download_filename || downloadFilename;
            serverVersion = saveData.version || null;
            console.log(`[renderViaService] Saved to server: ${serverUrl}, version: ${serverVersion}`);
        } else {
            const errText = await saveResponse.text();
            console.warn(`[renderViaService] Save failed (${saveResponse.status}): ${errText}, using blob URL`);
        }
    } catch (saveErr) {
        console.warn('[renderViaService] Save error:', saveErr.message, '- using blob URL');
    }

    return {
        blobUrl,
        serverUrl,
        filename: downloadFilename,
        version: serverVersion,
    };
}
