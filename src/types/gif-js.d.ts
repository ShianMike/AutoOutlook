declare module 'gif.js' {
  type GifFrameSource = HTMLCanvasElement | CanvasRenderingContext2D | HTMLImageElement | ImageData;

  interface GifOptions {
    workers?: number;
    repeat?: number;
    quality?: number;
    background?: string;
    width?: number;
    height?: number;
    workerScript?: string;
    transparent?: string | number;
    dither?: boolean | string;
    debug?: boolean;
  }

  interface GifFrameOptions {
    delay?: number;
    copy?: boolean;
    dispose?: number;
  }

  export default class GIF {
    constructor(options?: GifOptions);
    addFrame(source: GifFrameSource, options?: GifFrameOptions): void;
    on(event: 'finished', callback: (blob: Blob, data: Uint8Array) => void): void;
    on(event: 'progress', callback: (progress: number) => void): void;
    on(event: string, callback: (...args: unknown[]) => void): void;
    render(): void;
    abort(): void;
  }
}
