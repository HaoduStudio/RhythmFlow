import { useEffect, useMemo, useRef, useState } from "react";
import { OsuAudioPlayer } from "../../osu/audio";
import { simulateReplay } from "../../osu/judge";
import { buildChart, findFile, loadOsz, parseReplay } from "../../osu/parse";
import type { ManiaChart, OszContent, ReplayData, RenderScene, SimResult } from "../../osu/types";
import { useStore } from "../../store";
import { OsuExportPanel } from "./OsuExportPanel";
import { OsuImportCard } from "./OsuImportCard";
import { OsuPlayer } from "./OsuPlayer";

export function OsuAssistantPage(): JSX.Element {
  const store = useStore();
  const lang = store.language;

  const audioRef = useRef<OsuAudioPlayer | null>(null);
  const [content, setContent] = useState<OszContent | null>(null);
  const [selectedDifficulty, setSelectedDifficulty] = useState<string | null>(null);
  const [chart, setChart] = useState<ManiaChart | null>(null);
  const [replayFile, setReplayFile] = useState<File | null>(null);
  const [replay, setReplay] = useState<ReplayData | null>(null);
  const [sim, setSim] = useState<SimResult | null>(null);
  const [background, setBackground] = useState<ImageBitmap | null>(null);
  const [audioBuffer, setAudioBuffer] = useState<AudioBuffer | null>(null);
  const [scrollSpeed, setScrollSpeed] = useState(20);
  const [loadingOsz, setLoadingOsz] = useState(false);
  const [loadingReplay, setLoadingReplay] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [audioReadyKey, setAudioReadyKey] = useState("");

  useEffect(() => {
    const player = new OsuAudioPlayer();
    audioRef.current = player;
    return () => {
      player.dispose();
      audioRef.current = null;
    };
  }, []);

  const onOsz = async (file: File) => {
    setLoadingOsz(true);
    setError(null);
    setReplayFile(null);
    setReplay(null);
    setSim(null);
    audioRef.current?.setRate(1);
    try {
      const loaded = await loadOsz(file);
      if (loaded.difficulties.length === 0) {
        setContent(null);
        setChart(null);
        setError("osu_no_mania");
        return;
      }
      setContent(loaded);
      setSelectedDifficulty(loaded.difficulties[0].filename);
    } catch {
      setError("osu_parse_failed");
    } finally {
      setLoadingOsz(false);
    }
  };

  useEffect(() => {
    if (!content || !selectedDifficulty) return;
    const diff = content.difficulties.find((d) => d.filename === selectedDifficulty);
    if (!diff) return;
    let cancelled = false;

    void (async () => {
      try {
        const built = buildChart(diff.osuText);
        if (cancelled) return;
        setChart(built);
        setAudioReadyKey(`${selectedDifficulty}:${Date.now()}`);

        const audioBytes = findFile(content, built.audioFilename);
        if (audioBytes && audioRef.current) {
          await audioRef.current.load(audioBytes);
          if (!cancelled) setAudioBuffer(audioRef.current.audioBuffer);
        } else {
          setAudioBuffer(null);
        }

        const bgBytes = findFile(content, built.backgroundFilename);
        let bitmap: ImageBitmap | null = null;
        if (bgBytes) {
          try {
            bitmap = await createImageBitmap(new Blob([new Uint8Array(bgBytes)]));
          } catch {
            bitmap = null;
          }
        }
        if (cancelled) {
          bitmap?.close();
          return;
        }
        setBackground((prev) => {
          prev?.close();
          return bitmap;
        });
      } catch {
        if (!cancelled) setError("osu_parse_failed");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [content, selectedDifficulty]);

  useEffect(() => {
    if (!chart || !replayFile) return;
    let cancelled = false;
    setLoadingReplay(true);
    void (async () => {
      try {
        const parsed = await parseReplay(replayFile, chart.keyCount);
        if (cancelled) return;
        setReplay(parsed);
        setSim(simulateReplay(chart, parsed));
        audioRef.current?.setRate(parsed.rate);
      } catch {
        if (!cancelled) setError("osu_parse_failed");
      } finally {
        if (!cancelled) setLoadingReplay(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [chart, replayFile]);

  const scene = useMemo<RenderScene | null>(
    () => (chart ? { chart, replay, sim, background } : null),
    [chart, replay, sim, background],
  );

  const durationMs = Math.max(
    chart?.durationMs ?? 0,
    audioBuffer ? audioBuffer.duration * 1000 : 0,
  );

  return (
    <>
      <div className="cards-grid">
        <OsuImportCard
          language={lang}
          difficulties={content?.difficulties ?? []}
          selectedDifficulty={selectedDifficulty}
          chart={chart}
          replay={replay}
          loadingOsz={loadingOsz}
          loadingReplay={loadingReplay}
          error={error}
          onOsz={onOsz}
          onReplay={setReplayFile}
          onSelectDifficulty={setSelectedDifficulty}
        />
        <OsuExportPanel
          language={lang}
          scene={scene}
          audioBuffer={audioBuffer}
          scrollSpeed={scrollSpeed}
        />
      </div>
      <OsuPlayer
        language={lang}
        scene={scene}
        audioRef={audioRef}
        scrollSpeed={scrollSpeed}
        onScrollSpeedChange={setScrollSpeed}
        durationMs={durationMs}
        audioReadyKey={audioReadyKey}
      />
    </>
  );
}
