import { PauseCircleOutlined, PlayCircleOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Card, Slider, Space } from "antd";
import { useEffect, useRef, useState, type MutableRefObject } from "react";
import { t } from "../../i18n";
import type { OsuAudioPlayer } from "../../osu/audio";
import { drawScene } from "../../osu/render";
import type { RenderScene } from "../../osu/types";
import type { Language } from "../../types";

interface Props {
  language: Language;
  scene: RenderScene | null;
  audioRef: MutableRefObject<OsuAudioPlayer | null>;
  scrollSpeed: number;
  onScrollSpeedChange: (value: number) => void;
  durationMs: number;
  audioReadyKey: string;
}

function formatMs(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function OsuPlayer(props: Props): JSX.Element {
  const { language: lang, scene, audioRef, scrollSpeed, durationMs } = props;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [playing, setPlaying] = useState(false);
  const [uiTime, setUiTime] = useState(0);
  const sceneRef = useRef(scene);
  sceneRef.current = scene;
  const speedRef = useRef(scrollSpeed);
  speedRef.current = scrollSpeed;

  useEffect(() => {
    setPlaying(false);
    setUiTime(0);
    audioRef.current?.seek(0);
  }, [props.audioReadyKey, audioRef]);

  useEffect(() => {
    let raf = 0;
    let lastUi = 0;
    const loop = () => {
      const canvas = canvasRef.current;
      const player = audioRef.current;
      const currentScene = sceneRef.current;
      if (canvas) {
        const dpr = window.devicePixelRatio || 1;
        const width = canvas.clientWidth;
        const height = canvas.clientHeight;
        if (
          canvas.width !== Math.round(width * dpr) ||
          canvas.height !== Math.round(height * dpr)
        ) {
          canvas.width = Math.round(width * dpr);
          canvas.height = Math.round(height * dpr);
        }
        const ctx = canvas.getContext("2d");
        if (ctx) {
          ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
          if (currentScene) {
            const time = player ? player.currentTimeMs() : 0;
            if (player && player.playing && time >= currentScene.chart.durationMs + 2000) {
              player.pause();
              setPlaying(false);
            }
            drawScene(ctx, width, height, currentScene, time, {
              scrollSpeed: speedRef.current,
              showHud: true,
            });
            const now = performance.now();
            if (now - lastUi > 66) {
              setUiTime(time);
              lastUi = now;
            }
          } else {
            ctx.clearRect(0, 0, width, height);
            ctx.fillStyle = "#05070d";
            ctx.fillRect(0, 0, width, height);
          }
        }
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [audioRef]);

  const togglePlay = () => {
    const player = audioRef.current;
    if (!player || !scene) return;
    if (player.playing) {
      player.pause();
      setPlaying(false);
    } else {
      if (player.currentTimeMs() >= durationMs) player.seek(0);
      player.play();
      setPlaying(true);
    }
  };

  const restart = () => {
    audioRef.current?.seek(0);
    setUiTime(0);
  };

  const onSeek = (value: number) => {
    audioRef.current?.seek(value);
    setUiTime(value);
  };

  return (
    <Card title={t(lang, "osu_player_title")} size="small">
      <div className="osu-stage">
        <canvas ref={canvasRef} className="osu-canvas" />
        {!scene && <div className="osu-stage-hint">{t(lang, "osu_no_chart_hint")}</div>}
      </div>

      <div className="osu-transport">
        <Space>
          <Button
            type="primary"
            icon={playing ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
            onClick={togglePlay}
            disabled={!scene}
          >
            {playing ? t(lang, "osu_pause") : t(lang, "osu_play")}
          </Button>
          <Button icon={<ReloadOutlined />} onClick={restart} disabled={!scene}>
            {t(lang, "osu_restart")}
          </Button>
        </Space>
        <Slider
          className="osu-seek"
          min={0}
          max={Math.max(1000, Math.round(durationMs))}
          value={Math.min(uiTime, durationMs)}
          onChange={onSeek}
          tooltip={{ formatter: (v) => formatMs(v ?? 0) }}
          disabled={!scene}
        />
        <span className="time-label">
          {formatMs(uiTime)} / {formatMs(durationMs)}
        </span>
      </div>

      <div className="osu-speed-row">
        <span className="header-label">{t(lang, "osu_scroll_speed")}</span>
        <Slider
          className="osu-seek"
          min={5}
          max={40}
          value={scrollSpeed}
          onChange={props.onScrollSpeedChange}
        />
        <span className="slider-value">{scrollSpeed}</span>
      </div>
    </Card>
  );
}
