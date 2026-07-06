import {
  ClearOutlined,
  DeleteOutlined,
  DownloadOutlined,
  FolderOpenOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import { Button, Card, Input, Space, Table, Tooltip, Typography } from "antd";
import { useEffect, useState } from "react";
import { t } from "../i18n";
import { useStore } from "../store";
import { ReferenceAudioPickerModal } from "./ReferenceAudioPickerModal";

export function InputsCard(): JSX.Element {
  const store = useStore();
  const lang = store.language;
  const { settings } = store;
  const [selected, setSelected] = useState<string[]>([]);
  const [outputDir, setOutputDir] = useState(settings.output_dir);
  const [pattern, setPattern] = useState(settings.output_pattern);
  const [referencePickerOpen, setReferencePickerOpen] = useState(false);

  useEffect(() => setOutputDir(settings.output_dir), [settings.output_dir]);
  useEffect(() => setPattern(settings.output_pattern), [settings.output_pattern]);

  return (
    <Card title={t(lang, "inputs")} size="small">
      <Typography.Text type="secondary">{t(lang, "handcam_videos")}</Typography.Text>
      <Table
        size="small"
        rowKey="video_path"
        style={{ marginTop: 8 }}
        dataSource={store.rows}
        pagination={false}
        scroll={{ y: 168 }}
        locale={{ emptyText: t(lang, "no_videos") }}
        rowSelection={{
          selectedRowKeys: selected,
          onChange: (keys) => setSelected(keys as string[]),
        }}
        columns={[
          {
            title: t(lang, "table_file"),
            dataIndex: "file_name",
            ellipsis: true,
            render: (name: string, row) => <Tooltip title={row.video_path}>{name}</Tooltip>,
          },
        ]}
      />
      <Space style={{ marginTop: 10 }} wrap>
        <Button icon={<PlusOutlined />} onClick={store.addVideos} disabled={store.busy}>
          {t(lang, "add")}
        </Button>
        <Button
          icon={<DeleteOutlined />}
          disabled={store.busy || selected.length === 0}
          onClick={async () => {
            await store.removeVideos(selected);
            setSelected([]);
          }}
        >
          {t(lang, "remove")}
        </Button>
        <Button
          icon={<ClearOutlined />}
          disabled={store.busy || store.rows.length === 0}
          onClick={async () => {
            await store.clearVideos();
            setSelected([]);
          }}
        >
          {t(lang, "clear")}
        </Button>
      </Space>

      <div style={{ marginTop: 16 }}>
        <Space size={6}>
          <Typography.Text type="secondary">{t(lang, "reference_audio")}</Typography.Text>
          <Tooltip title={t(lang, "reference_audio_library")}>
            <Button
              type="text"
              size="small"
              aria-label={t(lang, "reference_audio_library")}
              icon={<DownloadOutlined />}
              disabled={store.busy}
              onClick={() => setReferencePickerOpen(true)}
            />
          </Tooltip>
        </Space>
        <div className="field-row path-row" style={{ marginTop: 6 }}>
          <Input
            value={store.reference}
            placeholder={t(lang, "reference_placeholder")}
            disabled={store.busy}
            onChange={(e) => store.setReference(e.target.value)}
          />
          <Button icon={<FolderOpenOutlined />} onClick={store.pickReference} disabled={store.busy}>
            {t(lang, "browse")}
          </Button>
        </div>
      </div>
      <ReferenceAudioPickerModal
        open={referencePickerOpen}
        onClose={() => setReferencePickerOpen(false)}
      />

      <div style={{ marginTop: 12 }}>
        <Typography.Text type="secondary">{t(lang, "output_directory")}</Typography.Text>
        <div className="field-row path-row" style={{ marginTop: 6 }}>
          <Input
            value={outputDir}
            placeholder={t(lang, "output_placeholder")}
            disabled={store.busy}
            onChange={(e) => setOutputDir(e.target.value)}
            onBlur={() => store.updateSettings({ output_dir: outputDir })}
          />
          <Button icon={<FolderOpenOutlined />} onClick={store.pickOutputDir} disabled={store.busy}>
            {t(lang, "browse")}
          </Button>
        </div>
      </div>

      <div style={{ marginTop: 12 }}>
        <Typography.Text type="secondary">{t(lang, "filename_pattern")}</Typography.Text>
        <Input
          style={{ marginTop: 6 }}
          value={pattern}
          disabled={store.busy}
          onChange={(e) => setPattern(e.target.value)}
          onBlur={() => store.updateSettings({ output_pattern: pattern })}
        />
      </div>
    </Card>
  );
}
