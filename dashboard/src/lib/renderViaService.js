import { getApiUrl, RENDER_SERVICE_URL } from '../config';

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
 * @returns {Promise<{blobUrl: string, serverUrl: string, filename: string}>}
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

    // Resolve the video URL: if relative, prepend API base
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
            console.log(`[renderViaService] Render done! Output URL: ${outputUrl}`);
            break;
        }

        if (status.status === 'error') {
            throw new Error(`Render service error: ${status.error}`);
        }
    }

    // Step 3: Download output and create blob URL
    console.log('[renderViaService] Downloading output...');
    const outputResponse = await fetch(outputUrl, { signal });
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

    let serverUrl = blobUrl; // fallback

    try {
        const saveResponse = await fetch(getApiUrl('/api/render/save'), {
            method: 'POST',
            body: formData,
            signal,
        });

        if (saveResponse.ok) {
            const saveData = await saveResponse.json();
            serverUrl = getApiUrl(saveData.video_url);
            console.log(`[renderViaService] Saved to server: ${serverUrl}`);
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
        filename: `${jobId}_clip_${clipIndex}.mp4`,
    };
}
