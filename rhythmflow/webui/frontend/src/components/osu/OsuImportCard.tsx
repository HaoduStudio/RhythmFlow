import { FileZipOutlined, PlayCircleOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Select, Space, Spin, Upload } from 'antd';
import { t } from '../../i18n';
import type { ManiaChart, OszDifficulty, ReplayData } from '../../osu/types';
import type { Language } from '../../types';

interface Props {
  language: Language;
  difficulties: OszDifficulty[];
  selectedDifficulty: string | null;
  chart: ManiaChart | null;
  replay: ReplayData | null;
  loadingOsz: boolean;
  loadingReplay: boolean;
  error: string | null;
  onOsz: (file: File) => void;
  onReplay: (file: File) => void;
  onSelectDifficulty: (filename: string) => void;
}

export function OsuImportCard(props: Props): JSX.Element {
  const { language: lang } = props;

  return (
    <Card title={t(lang, 'osu_import_title')} size="small">
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <div className="field-row">
          <Upload
            accept=".osz"
            showUploadList={false}
            maxCount={1}
            beforeUpload={(file) => {
              props.onOsz(file as File);
              return false;
            }}
          >
            <Button icon={<FileZipOutlined />} loading={props.loadingOsz}>
              {t(lang, 'osu_load_osz')}
            </Button>
          </Upload>
          <Upload
            accept=".osr"
            showUploadList={false}
            maxCount={1}
            beforeUpload={(file) => {
              props.onReplay(file as File);
              return false;
            }}
          >
            <Button icon={<PlayCircleOutlined />} loading={props.loadingReplay} disabled={!props.chart}>
              {t(lang, 'osu_load_osr')}
            </Button>
          </Upload>
        </div>

        {props.difficulties.length > 0 && (
          <div className="field-row">
            <span className="header-label">{t(lang, 'osu_difficulty')}</span>
            <Select
              style={{ flex: 1 }}
              value={props.selectedDifficulty ?? undefined}
              onChange={props.onSelectDifficulty}
              options={props.difficulties.map((diff) => ({
                value: diff.filename,
                label: `[${diff.version}] · ${diff.keyCount}K`,
              }))}
            />
          </div>
        )}

        {props.loadingOsz && <Spin size="small" />}

        {props.error && <Alert type="error" showIcon message={t(lang, props.error)} />}

        {props.chart && (
          <Alert
            type="success"
            showIcon
            message={t(lang, 'osu_chart_loaded', { title: props.chart.title })}
            description={`${props.chart.artist} · ${props.chart.keyCount}K · ${props.chart.notes.length} notes`}
          />
        )}

        {props.replay && (
          <Alert
            type="info"
            showIcon
            message={t(lang, 'osu_replay_loaded', { player: props.replay.stats.playerName || '—' })}
            description={`${(props.replay.stats.accuracy * 100).toFixed(2)}% · ${props.replay.stats.maxCombo}x · ${props.replay.stats.mods.join(' ') || 'NM'}`}
          />
        )}
      </Space>
    </Card>
  );
}
