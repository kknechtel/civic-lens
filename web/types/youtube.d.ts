// Minimal type declarations for the YouTube IFrame Player API.
// Loaded dynamically by /app/meetings/[id]/page.tsx so users can seek-to
// timestamps from the AI summary and transcript pane.
declare namespace YT {
  interface PlayerVars {
    rel?: 0 | 1;
    modestbranding?: 0 | 1;
    autoplay?: 0 | 1;
    start?: number;
  }
  interface PlayerOptions {
    videoId?: string;
    playerVars?: PlayerVars;
    events?: Record<string, (e: unknown) => void>;
  }
  interface Player {
    seekTo(seconds: number, allowSeekAhead: boolean): void;
    playVideo?(): void;
    pauseVideo?(): void;
    getCurrentTime?(): number;
    getDuration?(): number;
    destroy?(): void;
  }
  // Constructor signature matched at runtime by new window.YT.Player(...).
  const Player: {
    new (elementId: string | HTMLElement, options: PlayerOptions): Player;
  };
}

interface Window {
  YT: typeof YT;
  onYouTubeIframeAPIReady: () => void;
}
