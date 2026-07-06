import { DownloadOutlined } from "@ant-design/icons";
import { App, Button, Card, InputNumber, Modal, Progress, Select, Space } from "antd";
import { useMemo, useRef, useState } from "react";
import { getApi } from "../../bridge";
import { t } from "../../i18n";
import { exportVideo, webCodecsAvailable } from "../../osu/export";
import { drawScene } from "../../osu/render";
import type { ExportConfig, RenderScene } from "../../osu/types";
import type { Language } from "../../types";

interface Props {
  language: Language;
  scene: RenderScene | null;
  audioBuffer: AudioBuffer | null;
  scrollSpeed: number;
}

const PRESETS: Record<string, { width: number; height: number }> = {
  "720p": { width: 1280, height: 720 },
  "1080p": { width: 1920, height: 1080 },
  "1440p": { width: 2560, height: 1440 },
};
const SAVE_CHUNK_SIZE = 1024 * 1024;

function sanitize(name: string): string {
  const sanitized = Array.from(name, (char) =>
    char.charCodeAt(0) < 32 || '<>:"/\\|?*'.includes(char) ? "_" : char,
  ).join("");
  return sanitized.slice(0, 120) || "replay";
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const stride = 0x8000;
  for (let index = 0; index < bytes.length; index += stride) {
    binary += String.fromCharCode(...bytes.subarray(index, index + stride));
  }
  return btoa(binary);
}

async function saveBlobToOutput(blob: Blob, filename: string): Promise<string> {
  const api = await getApi();
  const started = await api.begin_osu_export(filename);
  if (!started.ok || !started.token || !started.output_path) {
    throw new Error(started.error || "osu_export_failed");
  }

  try {
    for (let offset = 0; offset < blob.size; offset += SAVE_CHUNK_SIZE) {
      const buffer = await blob.slice(offset, offset + SAVE_CHUNK_SIZE).arrayBuffer();
      const appended = await api.append_osu_export_chunk(
        started.token,
        bytesToBase64(new Uint8Array(buffer)),
      );
      if (!appended.ok) throw new Error(appended.error || "osu_export_failed");
    }

    const finished = await api.finish_osu_export(started.token);
    if (!finished.ok || !finished.output_path) {
      throw new Error(finished.error || "osu_export_failed");
    }
    return finished.output_path;
  } catch (err) {
    await api.abort_osu_export(started.token).catch(() => undefined);
    throw err;
  }
}

export function OsuExportPanel(props: Props): JSX.Element {
  const { language: lang, scene, audioBuffer, scrollSpeed } = props;
  const { message } = App.useApp();
  const [preset, setPreset] = useState("1080p");
  const [config, setConfig] = useState<ExportConfig>({
    width: 1920,
    height: 1080,
    fps: 60,
    container: "mp4",
  });
  const [exporting, setExporting] = useState(false);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const abortRef = useRef<AbortController | null>(null);

  const percent = useMemo(
    () => (progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0),
    [progress],
  );

  const applyPreset = (value: string) => {
    setPreset(value);
    if (value !== "custom" && PRESETS[value]) {
      setConfig((prev) => ({ ...prev, ...PRESETS[value] }));
    }
  };

  const patch = (change: Partial<ExportConfig>) => {
    setPreset("custom");
    setConfig((prev) => ({ ...prev, ...change }));
  };

  const startExport = async () => {
    if (!scene) return;
    if (!webCodecsAvailable()) {
      message.error(t(lang, "osu_export_unsupported"));
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setExporting(true);
    setProgress({ done: 0, total: 0 });
    const { width, height, fps, container } = config;
    try {
      const result = await exportVideo({
        width,
        height,
        fps,
        container,
        durationMs: scene.chart.durationMs + 2000,
        audioBuffer,
        renderFrame: (ctx, timeMs) =>
          drawScene(ctx, width, height, scene, timeMs, { scrollSpeed, showHud: true }),
        onProgress: (done, total) => setProgress({ done, total }),
        signal: controller.signal,
      });
      const name = `${scene.chart.title}_${scene.chart.version}_${width}x${height}_${fps}fps.${result.container}`;
      const outputPath = await saveBlobToOutput(result.blob, sanitize(name));
      message.success(t(lang, "osu_export_done_path", { path: outputPath }));
    } catch (err) {
      if ((err as DOMException).name !== "AbortError") {
        const code = err instanceof Error ? err.message : "osu_export_failed";
        const key =
          code === "codec_unavailable" || code === "webcodecs_unavailable"
            ? "osu_export_unsupported"
            : "osu_export_failed";
        message.error(t(lang, key));
      }
    } finally {
      setExporting(false);
      abortRef.current = null;
    }
  };

  const isCustom = preset === "custom";

  return (
    <Card title={t(lang, "osu_export_title")} size="small">
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <div className="field-row">
          <span className="header-label">{t(lang, "osu_resolution")}</span>
          <Select
            style={{ width: 220 }}
            value={preset}
            onChange={applyPreset}
            options={[
              { value: "720p", label: "720p (1280×720)" },
              { value: "1080p", label: "1080p (1920×1080)" },
              { value: "1440p", label: "1440p (2560×1440)" },
              { value: "custom", label: t(lang, "osu_res_custom") },
            ]}
          />
        </div>

        <div className="field-row">
          <span className="header-label">{t(lang, "osu_width")}</span>
          <InputNumber
            min={320}
            max={3840}
            step={2}
            value={config.width}
            disabled={!isCustom}
            onChange={(v) => patch({ width: Math.round(v ?? 1920) })}
          />
          <span className="header-label">{t(lang, "osu_height")}</span>
          <InputNumber
            min={240}
            max={2160}
            step={2}
            value={config.height}
            disabled={!isCustom}
            onChange={(v) => patch({ height: Math.round(v ?? 1080) })}
          />
        </div>

        <div className="field-row">
          <span className="header-label">{t(lang, "osu_fps")}</span>
          <InputNumber
            min={24}
            max={120}
            value={config.fps}
            onChange={(v) => setConfig((prev) => ({ ...prev, fps: Math.round(v ?? 60) }))}
          />
          <span className="header-label">{t(lang, "osu_container")}</span>
          <Select
            style={{ width: 150 }}
            value={config.container}
            onChange={(value) =>
              setConfig((prev) => ({ ...prev, container: value as "mp4" | "webm" }))
            }
            options={[
              { value: "mp4", label: "MP4 (H.264)" },
              { value: "webm", label: "WebM (VP9)" },
            ]}
          />
        </div>

        <Button
          type="primary"
          icon={<DownloadOutlined />}
          onClick={startExport}
          loading={exporting}
          disabled={!scene}
          block
        >
          {t(lang, "osu_export_button")}
        </Button>
      </Space>

      <Modal
        open={exporting}
        closable={false}
        maskClosable={false}
        title={t(lang, "osu_export_title")}
        footer={[
          <Button key="cancel" danger onClick={() => abortRef.current?.abort()}>
            {t(lang, "osu_export_cancel")}
          </Button>,
        ]}
      >
        <Progress percent={percent} status="active" />
        <div className="header-label" style={{ marginTop: 8 }}>
          {t(lang, "osu_exporting", { done: progress.done, total: progress.total })}
        </div>
      </Modal>
    </Card>
  );
}
