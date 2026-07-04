import { PlayCircleOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { Button, Card, Select, Slider, Space, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { t } from '../i18n';
import { useStore } from '../store';
import type { CutMode } from '../types';

function VolumeSlider({
  label,
  value,
  disabled,
  onCommit,
}: {
  label: string;
  value: number;
  disabled: boolean;
  onCommit: (value: number) => void;
}): JSX.Element {
  const [local, setLocal] = useState(value);
  useEffect(() => setLocal(value), [value]);
  return (
    <div>
      <Typography.Text type="secondary">{label}</Typography.Text>
      <div className="slider-row" style={{ marginTop: 4 }}>
        <Slider
          min={0}
          max={200}
          step={5}
          value={local}
          disabled={disabled}
          onChange={setLocal}
          onChangeComplete={onCommit}
        />
        <span className="slider-value">{local}%</span>
      </div>
    </div>
  );
}

export function OptionsCard(): JSX.Element {
  const store = useStore();
  const lang = store.language;
  const { settings } = store;

  return (
    <Card title={t(lang, 'options')} size="small">
      <div>
        <Typography.Text type="secondary">{t(lang, 'cut_mode')}</Typography.Text>
        <Select<CutMode>
          style={{ width: '100%', marginTop: 6 }}
          value={settings.cut_mode}
          disabled={store.busy}
          onChange={(value) => store.updateSettings({ cut_mode: value })}
          options={[
            { value: 'accurate', label: t(lang, 'mode_accurate') },
            { value: 'fast', label: t(lang, 'mode_fast') },
          ]}
        />
      </div>

      <div style={{ marginTop: 14 }}>
        <VolumeSlider
          label={t(lang, 'original_audio')}
          value={settings.original_volume}
          disabled={store.busy}
          onCommit={(value) => store.updateSettings({ original_volume: value })}
        />
      </div>

      <div style={{ marginTop: 10 }}>
        <VolumeSlider
          label={t(lang, 'reference_audio')}
          value={settings.reference_volume}
          disabled={store.busy}
          onCommit={(value) => store.updateSettings({ reference_volume: value })}
        />
      </div>

      <div style={{ marginTop: 18 }}>
        <Space wrap>
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            loading={store.busy}
            onClick={store.analyze}
          >
            {t(lang, 'analyze')}
          </Button>
          <Button
            icon={<PlayCircleOutlined />}
            disabled={store.busy}
            onClick={store.process}
          >
            {t(lang, 'process')}
          </Button>
        </Space>
      </div>
    </Card>
  );
}
