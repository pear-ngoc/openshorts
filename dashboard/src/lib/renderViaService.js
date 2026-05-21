import { getApiUrl, RENDER_SERVICE_URL } from '../config';

/**
 * Renders via the server-side render-service (FFmpeg/Remotion Lambda).
 * Polls for completion and returns a blob URL to the output MP4.
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
 * @returns {Promise<string>} Blob URL of the rendered MP4
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

    // Submit render job
    const submitStart = performance.now();
    const submitResponse = await fetch(`${RENDER_SERVICE_URL}/render`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            jobId,
            clipIndex,
            props: {
                videoUrl,
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

    // Poll for completion
    const pollStart = performance.now();
    while (true) {
        if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');

        await new Promise(r => setTimeout(r, 1000));

        const statusResponse = await fetch(`${RENDER_SERVICE_URL}/render/${renderId}`, { signal });
        if (!statusResponse.ok) {
            throw new Error(`Poll failed: ${statusResponse.status}`);
        }

        const status = await statusResponse.json();
        console.log(`[renderViaService] Status: ${status.status} | Progress: ${Math.round((status.progress || 0) * 100)}%`);

        if (onProgress && typeof onProgress === 'function') {
            onProgress(status.progress || 0);
        }

        if (status.status === 'done') {
            console.log(`[renderViaService] Render done! Output: ${status.outputUrl}`);
            console.log(`[renderViaService] Total elapsed: ${Math.round(performance.now() - pollStart)}ms`);

            // Download output into a blob URL so callers can use it like renderInBrowser
            const outputResponse = await fetch(status.outputUrl, { signal });
            if (!outputResponse.ok) throw new Error(`Failed to fetch output: ${outputResponse.status}`);
            const blob = await outputResponse.blob();
            return URL.createObjectURL(blob);
        }

        if (status.status === 'error') {
            throw new Error(`Render service error: ${status.error}`);
        }
    }
}
