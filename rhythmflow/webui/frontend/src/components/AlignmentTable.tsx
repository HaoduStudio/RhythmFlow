import { Card, InputNumber, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { t } from '../i18n';
import { useStore } from '../store';
import type { RowState } from '../types';

type ReviewStatus = 'error' | 'pending' | 'required' | 'confirmed' | 'ok';

function statusOf(row: RowState): ReviewStatus {
  if (row.error) return 'error';
  if (!row.analyzed) return 'pending';
  if (row.needs_review && !row.review_confirmed) return 'required';
  if (row.needs_review && row.review_confirmed) return 'confirmed';
  return 'ok';
}

const STATUS_META: Record<ReviewStatus, { color: string; key: string; rowClass: string }> = {
  error: { color: 'error', key: 'error', rowClass: 'row-error' },
  pending: { color: 'default', key: 'review_pending', rowClass: '' },
  required: { color: 'warning', key: 'review_required', rowClass: 'row-review' },
  confirmed: { color: 'success', key: 'review_confirmed', rowClass: 'row-ok' },
  ok: { color: 'success', key: 'review_ok', rowClass: 'row-ok' },
};

export function AlignmentTable(): JSX.Element {
  const store = useStore();
  const lang = store.language;

  const columns: ColumnsType<RowState> = [
    {
      title: t(lang, 'table_file'),
      dataIndex: 'file_name',
      ellipsis: true,
      render: (name: string, row) => (
        <Tooltip title={row.warnings.length ? row.warnings.join(', ') : row.video_path}>{name}</Tooltip>
      ),
    },
    {
      title: t(lang, 'table_offset'),
      width: 100,
      align: 'right',
      render: (_v, row) =>
        row.error ? t(lang, 'error') : row.detected_offset == null ? '' : row.detected_offset.toFixed(3),
    },
    {
      title: t(lang, 'table_confidence'),
      width: 84,
      align: 'right',
      render: (_v, row) => (row.confidence == null ? '' : row.confidence.toFixed(2)),
    },
    {
      title: t(lang, 'table_nudge'),
      width: 128,
      render: (_v, row, index) => (
        <InputNumber
          size="small"
          value={row.nudge}
          step={0.01}
          precision={3}
          min={-60}
          max={60}
          disabled={store.busy || !row.analyzed}
          style={{ width: '100%' }}
          onChange={(value) => store.setNudge(index, typeof value === 'number' ? value : 0)}
        />
      ),
    },
    {
      title: t(lang, 'table_final'),
      width: 96,
      align: 'right',
      render: (_v, row) => (row.final_offset == null ? '' : row.final_offset.toFixed(3)),
    },
    {
      title: t(lang, 'table_smart_trim'),
      width: 120,
      align: 'right',
      render: (_v, row) => (row.analyzed && !row.error ? `${row.smart_trim_s.toFixed(2)}s / ${row.smart_trim_count}` : ''),
    },
    {
      title: t(lang, 'table_ai_confidence'),
      width: 96,
      align: 'right',
      render: (_v, row) =>
        !row.analyzed || row.error ? '' : row.smart_confidence == null ? '-' : row.smart_confidence.toFixed(2),
    },
    {
      title: t(lang, 'table_review'),
      width: 110,
      align: 'center',
      render: (_v, row) => {
        const meta = STATUS_META[statusOf(row)];
        return <Tag color={meta.color}>{t(lang, meta.key)}</Tag>;
      },
    },
  ];

  return (
    <Card title={t(lang, 'alignment')} size="small">
      <Table<RowState>
        size="small"
        rowKey="video_path"
        dataSource={store.rows}
        columns={columns}
        pagination={false}
        scroll={{ x: 820, y: 240 }}
        rowClassName={(row) => STATUS_META[statusOf(row)].rowClass}
        locale={{ emptyText: t(lang, 'no_videos') }}
      />
    </Card>
  );
}
