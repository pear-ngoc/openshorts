import { renderMediaOnWeb } from '@remotion/web-renderer';
import { ShortVideo } from '../remotion/compositions/ShortVideo';

/**
 * Renders a Remotion composition directly in the browser using WebCodecs.
 * Returns a blob URL to the rendered MP4.
 *
 * @param {object} params
 * @param {string} params.videoUrl - Source video URL
 * @param {number} params.durationInSeconds - Video duration
 * @param {object|null} params.subtitles - SubtitleConfig
 * @param {object|null} params.hook - HookConfig
 * @param {object|null} params.effects - EffectsConfig
 * @param {function} [params.onProgress] - Progress callback (0-1)
 * @param {AbortSignal} [params.signal] - Abort signal for cancellation
 * @returns {Promise<string>} Blob URL of the rendered MP4
 */
export async function renderInBrowser({
    videoUrl,
    durationInSeconds = 30,
    subtitles = null,
    hook = null,
    effects = null,
    onProgress,
    signal,
}) {
    console.log('[renderInBrowser] Starting render...');
    console.log('[renderInBrowser] videoUrl:', videoUrl);
    console.log('[renderInBrowser] durationInSeconds:', durationInSeconds);
    console.log('[renderInBrowser] subtitles:', subtitles);
    console.log('[renderInBrowser] hook:', hook);
    console.log('[renderInBrowser] effects:', effects);

    const fps = 30;
    const durationInFrames = Math.max(1, Math.round(durationInSeconds * fps));
    console.log('[renderInBrowser] fps:', fps, '| durationInFrames:', durationInFrames);

    // Fetch video into a Blob URL so Remotion WebCodecs can access it
    // (Remotion runs in a Web Worker where Vite proxy / relative URLs may not resolve)
    console.log('[renderInBrowser] Fetching video into memory...');
    const videoFetchStart = performance.now();
    let resolvedVideoUrl = videoUrl;
    if (!videoUrl.startsWith('blob:')) {
        try {
            console.log('[renderInBrowser] Fetching from:', videoUrl);
            const response = await fetch(videoUrl, { signal });
            if (!response.ok) {
                throw new Error(`Failed to fetch video: ${response.status} ${response.statusText}`);
            }
            const videoBlob = await response.blob();
            console.log('[renderInBrowser] Video fetched, size:', videoBlob.size, 'bytes, type:', videoBlob.type, 'elapsed:', Math.round(performance.now() - videoFetchStart) + 'ms');
            resolvedVideoUrl = URL.createObjectURL(videoBlob);
            console.log('[renderInBrowser] Blob URL:', resolvedVideoUrl);
        } catch (fetchErr) {
            console.error('[renderInBrowser] Video fetch error:', fetchErr.message);
            throw fetchErr;
        }
    } else {
        console.log('[renderInBrowser] Video already a blob URL, using as-is.');
    }

    console.log('[renderInBrowser] Calling renderMediaOnWeb...');
    const startTime = performance.now();

    let getBlob;
    try {
        console.log('[renderInBrowser] Checking WebCodecs support...');
        if (!('VideoEncoder' in window)) {
            console.error('[renderInBrowser] WebCodecs NOT supported in this browser!');
            throw new Error('WebCodecs is not supported in this browser. Please use Chrome 94+ or Edge 94+.');
        }
        console.log('[renderInBrowser] WebCodecs supported.');

        const result = await renderMediaOnWeb({
            licenseKey: 'free-license',
            delayRenderTimeoutInMilliseconds: 300000,
            chromiumOptions: {
                hardwareAcceleration: 'enable',
            },
            composition: {
                component: ShortVideo,
                durationInFrames,
                fps,
                width: 1080,
                height: 1920,
                id: 'ShortVideo',
                calculateMetadata: null,
            },
            inputProps: {
                videoUrl: resolvedVideoUrl,
                durationInFrames,
                fps,
                width: 1080,
                height: 1920,
                subtitles,
                hook,
                effects,
            },
            logLevel: 'verbose',
            onProgress: onProgress
                ? ({ progress }) => {
                      console.log('[renderInBrowser] Progress:', Math.round(progress * 100) + '%');
                      onProgress(progress);
                  }
                : undefined,
            signal,
        });
        getBlob = result.getBlob;
        console.log('[renderInBrowser] renderMediaOnWeb returned, elapsed:', Math.round(performance.now() - startTime) + 'ms');
    } catch (err) {
        console.error('[renderInBrowser] renderMediaOnWeb ERROR:', err.message);
        console.error('[renderInBrowser] Error stack:', err.stack);
        throw err;
    }

    console.log('[renderInBrowser] Getting blob...');
    const blobStart = performance.now();
    const blob = await getBlob();
    console.log('[renderInBrowser] getBlob elapsed:', Math.round(performance.now() - blobStart) + 'ms', '| blob size:', blob?.size, 'bytes | blob type:', blob?.type);

    const blobUrl = URL.createObjectURL(blob);
    console.log('[renderInBrowser] Blob URL created:', blobUrl);
    console.log('[renderInBrowser] Render complete, total elapsed:', Math.round(performance.now() - startTime) + 'ms');

    // Cleanup the blob URL we created (don't clean external blob: or http: URLs)
    if (!videoUrl.startsWith('blob:') && resolvedVideoUrl.startsWith('blob:')) {
        URL.revokeObjectURL(resolvedVideoUrl);
        console.log('[renderInBrowser] Revoked temp blob URL.');
    }

    return blobUrl;
}

/**
 * Triggers a download of a blob URL as an MP4 file.
 */
export function downloadBlobUrl(blobUrl, filename = 'output.mp4') {
    const link = document.createElement('a');
    link.href = blobUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}
