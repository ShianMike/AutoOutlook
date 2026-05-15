import GIF from 'gif.js';

export interface GifRecorderOptions {
  width: number;
  height: number;
  delayMs: number;
  quality?: number;
  workerScript?: string;
  signal?: AbortSignal;
  onProgress?: (progress: number) => void;
}

export function recordCanvasesToGif(
  frames: HTMLCanvasElement[],
  {
    width,
    height,
    delayMs,
    quality = 10,
    workerScript = '/gif.worker.js',
    signal,
    onProgress,
  }: GifRecorderOptions,
): Promise<Blob> {
  if (frames.length === 0) {
    return Promise.reject(new Error('No GIF frames were captured.'));
  }

  return new Promise((resolve, reject) => {
    let settled = false;
    if (signal?.aborted) {
      reject(new Error('GIF export cancelled.'));
      return;
    }
    const gif = new GIF({
      workers: 2,
      quality,
      width,
      height,
      workerScript,
      repeat: 0,
      background: '#f5f0e6',
    });
    const settle = (callback: () => void) => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener('abort', handleAbort);
      callback();
    };
    const handleAbort = () => {
      gif.abort();
      settle(() => reject(new Error('GIF export cancelled.')));
    };

    frames.forEach((frame) => {
      gif.addFrame(frame, { delay: delayMs, copy: true });
    });

    gif.on('progress', (progress) => {
      onProgress?.(progress);
    });

    gif.on('finished', (blob) => {
      settle(() => resolve(blob));
    });

    signal?.addEventListener('abort', handleAbort, { once: true });

    try {
      gif.render();
    } catch (error) {
      settle(() => reject(error));
    }
  });
}
