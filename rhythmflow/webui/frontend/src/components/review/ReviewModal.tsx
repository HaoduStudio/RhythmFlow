import {
  PauseOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SoundOutlined,
  StopOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import { App, Button, Checkbox, InputNumber, Modal, Slider, Space, Spin, Table, Tooltip, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useCallback, useEffect, useRef, useState } from 'react';
import { t } from '../../i18n';
import { useStore } from '../../store';
import type { ReviewSegment, WaveformData } from '../../types';
import { adjustedSpans, formatSeconds, formatSpan } from './segmentMath';
import { WaveformCanvas } from './WaveformCanvas';

type Source = 'reference' | 'video';

export function ReviewModal(): JSX.Element {
  const store = useStore();

  return (
    <Modal
      title={t(store.language, 'review_dialog_title')}
      open={store.reviewOpen}
      width={1080}
      destroyOnHidden
      onCancel={store.closeReview}
      footer={null}
    >
      {store.reviewOpen && <ReviewBody segments={store.reviewSegments} />}
    </Modal>
  );
}

function ReviewBody({ segments }: { segments: ReviewSegment[] }): JSX.Element {
  const store = useStore();
  const lang = store.language;
  const { message } = App.useApp();

  const [selectedId, setSelectedId] = useState(segments[0]?.id ?? '');
  const [deltas, setDeltas] = useState<Record<string, number>>({});
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [waveform, setWaveform] = useState<WaveformData | null>(null);
  const [waveformError, setWaveformError] = useState<string | null>(null);
  const [waveformLoading, setWaveformLoading] = useState(false);
  const [activeSource, setActiveSource] = useState<Source | null>(null);
  const [playing, setPlaying] = useState(false);
  const [positionSec, setPositionSec] = useState(0);

  const videoRef = useRef<HTMLVideoElement>(null);
  const rangeRef = useRef<{ start: number; end: number } | null>(null);
  const pendingSeekRef = useRef<number | null>(null);
  const loadTokenRef = useRef(0);

  const selected = segments.find((s) => s.id === selectedId) ?? null;
  const deltaFor = (id: string) => deltas[id] ?? 0;
  const bounds = waveform?.bounds ?? { lower: -2, upper: 2 };
  const allChecked = segments.length > 0 && segments.every((s) => checked[s.id]);

  const stopPreview = useCallback(() => {
    const video = videoRef.current;
    if (video) video.pause();
    rangeRef.current = null;
    pendingSeekRef.current = null;
    setActiveSource(null);
    setPlaying(false);
    setPositionSec(0);
  }, []);

  // Load the waveform whenever the selected segment changes.
  useEffect(() => {
    if (!selected) return;
    stopPreview();
    setWaveform(null);
    setWaveformError(null);
    setWaveformLoading(true);
    const token = (loadTokenRef.current += 1);
    store
      .getWaveform(selected)
      .then((data) => {
        if (token !== loadTokenRef.current) return;
        if (data.ok) setWaveform(data);
        else setWaveformError(t(lang, 'review_waveform_error'));
      })
      .catch(() => {
        if (token === loadTokenRef.current) setWaveformError(t(lang, 'review_waveform_error'));
      })
      .finally(() => {
        if (token === loadTokenRef.current) setWaveformLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  const setDelta = (id: string, value: number) => {
    const clamped = Math.min(bounds.upper, Math.max(bounds.lower, value));
    setDeltas((prev) => ({ ...prev, [id]: clamped }));
  };

  const play = (source: Source) => {
    if (!selected) {
      message.info(t(lang, 'review_select_segment'));
      return;
    }
    const [rs, re, vs, ve] = adjustedSpans(selected, deltaFor(selected.id));
    const isRef = source === 'reference';
    const url = isRef ? selected.reference_url : selected.video_url;
    if (!url) return;
    const start = isRef ? rs : vs;
    const end = Math.max(start + 0.05, isRef ? re : ve);
    rangeRef.current = { start, end };
    setActiveSource(source);
    setPlaying(true);
    setPositionSec(0);
    const video = videoRef.current;
    if (!video) return;
    if (video.getAttribute('data-src') !== url) {
      video.setAttribute('data-src', url);
      pendingSeekRef.current = start;
      video.src = url;
      video.load();
    } else {
      video.currentTime = start;
      void video.play();
    }
  };

  const togglePause = () => {
    const video = videoRef.current;
    const range = rangeRef.current;
    if (!video || !range || !activeSource) return;
    if (playing) {
      video.pause();
      setPlaying(false);
    } else {
      if (video.currentTime >= range.end) video.currentTime = range.start;
      void video.play();
      setPlaying(true);
    }
  };

  const onLoadedMetadata = () => {
    const video = videoRef.current;
    if (video && pendingSeekRef.current != null) {
      video.currentTime = pendingSeekRef.current;
      pendingSeekRef.current = null;
      void video.play();
    }
  };

  const onTimeUpdate = () => {
    const video = videoRef.current;
    const range = rangeRef.current;
    if (!video || !range) return;
    if (video.currentTime >= range.end) {
      video.pause();
      video.currentTime = range.end;
      setPlaying(false);
    }
    setPositionSec(Math.max(0, Math.min(video.currentTime - range.start, range.end - range.start)));
  };

  const seek = (value: number) => {
    const video = videoRef.current;
    const range = rangeRef.current;
    if (!video || !range) return;
    video.currentTime = Math.min(range.start + value, range.end);
  };

  const accept = () => {
    if (!allChecked) {
      message.warning(t(lang, 'review_incomplete'));
      return;
    }
    const payload = segments.map((s) => ({
      row: s.row,
      segment_index: s.segment_index,
      delta_s: deltaFor(s.id),
    }));
    void store.acceptReview(payload);
  };

  const durationSec = rangeRef.current ? rangeRef.current.end - rangeRef.current.start : 0;

  const columns: ColumnsType<ReviewSegment> = [
    { title: t(lang, 'review_table_file'), dataIndex: 'file_name', ellipsis: true, width: 120 },
    {
      title: t(lang, 'review_table_segment'),
      width: 96,
      render: (_v, seg) => {
        const notes = seg.notes.map((n) => t(lang, n.key, n.params)).join('\n');
        return <Tooltip title={notes}>{t(lang, seg.label_key, seg.label_params)}</Tooltip>;
      },
    },
    {
      title: t(lang, 'review_table_reference'),
      width: 130,
      render: (_v, seg) => {
        const [rs, re] = adjustedSpans(seg, deltaFor(seg.id));
        return formatSpan(rs, re);
      },
    },
    {
      title: t(lang, 'review_table_video'),
      width: 130,
      render: (_v, seg) => {
        const [, , vs, ve] = adjustedSpans(seg, deltaFor(seg.id));
        return formatSpan(vs, ve);
      },
    },
    {
      title: t(lang, 'review_table_confirm'),
      width: 76,
      align: 'center',
      render: (_v, seg) => (
        <Checkbox
          checked={!!checked[seg.id]}
          onChange={(e) => setChecked((prev) => ({ ...prev, [seg.id]: e.target.checked }))}
        />
      ),
    },
  ];

  return (
    <>
      <Typography.Paragraph>
        {t(lang, 'review_dialog_summary', { count: segments.length })}
      </Typography.Paragraph>
      <div className="review-body">
        <Table<ReviewSegment>
          size="small"
          rowKey="id"
          dataSource={segments}
          columns={columns}
          pagination={false}
          scroll={{ y: 360 }}
          rowClassName={(seg) => (seg.id === selectedId ? 'ant-table-row-selected' : '')}
          onRow={(seg) => ({ onClick: () => setSelectedId(seg.id) })}
        />

        <div>
          <video
            ref={videoRef}
            className="preview-media"
            controls={false}
            onLoadedMetadata={onLoadedMetadata}
            onTimeUpdate={onTimeUpdate}
            onError={() => activeSource && message.error(t(lang, 'review_preview_error'))}
          />
          <div className="preview-controls">
            <Button icon={<SoundOutlined />} disabled={!selected} onClick={() => play('reference')}>
              {t(lang, 'review_play_reference')}
            </Button>
            <Button icon={<VideoCameraOutlined />} disabled={!selected} onClick={() => play('video')}>
              {t(lang, 'review_play_video')}
            </Button>
            <Button icon={<PauseOutlined />} disabled={!activeSource} onClick={togglePause}>
              {playing ? t(lang, 'review_pause') : t(lang, 'review_resume')}
            </Button>
            <Button icon={<StopOutlined />} disabled={!activeSource} onClick={stopPreview}>
              {t(lang, 'review_stop')}
            </Button>
          </div>
          <div className="timeline-row">
            <Slider
              min={0}
              max={Math.max(0.001, durationSec)}
              step={0.01}
              value={positionSec}
              disabled={!activeSource}
              tooltip={{ open: false }}
              onChange={seek}
            />
            <span className="time-label">
              {formatSeconds(positionSec)} / {formatSeconds(durationSec)}
            </span>
          </div>

          <div style={{ marginTop: 12 }}>
            {waveformLoading && (
              <div style={{ height: 200, display: 'grid', placeItems: 'center' }}>
                <Spin tip={t(lang, 'review_waveform_loading')} />
              </div>
            )}
            {!waveformLoading && waveformError && (
              <div style={{ height: 200, display: 'grid', placeItems: 'center', color: '#fca5a5' }}>
                {waveformError}
              </div>
            )}
            {!waveformLoading && !waveformError && waveform && selected && (
              <WaveformCanvas
                data={waveform}
                segment={selected}
                delta={deltaFor(selected.id)}
                bounds={bounds}
                language={lang}
                onAdjust={(value) => setDelta(selected.id, value)}
              />
            )}
          </div>

          <div className="adjust-row">
            <span style={{ color: '#94a3b8' }}>{t(lang, 'review_adjustment')}</span>
            <InputNumber
              value={selected ? deltaFor(selected.id) : 0}
              min={bounds.lower}
              max={bounds.upper}
              step={0.01}
              precision={3}
              suffix="s"
              disabled={!selected}
              onChange={(value) => selected && setDelta(selected.id, typeof value === 'number' ? value : 0)}
            />
            <Button
              icon={<ReloadOutlined />}
              disabled={!selected}
              onClick={() => selected && setDelta(selected.id, 0)}
            >
              {t(lang, 'review_reset_adjustment')}
            </Button>
            <div style={{ flex: 1 }} />
            <Button
              onClick={() => setChecked(Object.fromEntries(segments.map((s) => [s.id, true])))}
            >
              {t(lang, 'review_confirm_all')}
            </Button>
          </div>
        </div>
      </div>

      <div style={{ marginTop: 16, textAlign: 'right' }}>
        <Space>
          <Button onClick={store.closeReview}>{t(lang, 'review_cancel')}</Button>
          <Button type="primary" icon={<PlayCircleOutlined />} disabled={!allChecked} onClick={accept}>
            {t(lang, 'review_accept')}
          </Button>
        </Space>
      </div>
    </>
  );
}
