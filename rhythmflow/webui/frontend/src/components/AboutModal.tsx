import { GithubOutlined, SyncOutlined } from '@ant-design/icons';
import { App as AntApp, Button, Modal, Space, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { getApi, onEvent } from '../bridge';
import { t } from '../i18n';
import { useStore } from '../store';
import type { AboutInfo, UpdateStatusPayload } from '../types';

export function AboutModal(): JSX.Element {
  const store = useStore();
  const lang = store.language;
  const { message } = AntApp.useApp();
  const [info, setInfo] = useState<AboutInfo | null>(null);
  const [updateStatus, setUpdateStatus] = useState<UpdateStatusPayload | null>(null);

  useEffect(() => {
    if (store.aboutOpen && !info) {
      getApi().then((api) => api.about_info().then(setInfo));
    }
  }, [store.aboutOpen, info]);

  useEffect(
    () =>
      onEvent('update_status', (payload: UpdateStatusPayload) => {
        setUpdateStatus(payload);
        if (payload.status === 'up_to_date') {
          message.success(t(lang, 'update_up_to_date'));
        } else if (payload.status === 'restart_pending') {
          message.success(t(lang, 'update_restart_pending', { version: payload.latest_version ?? '' }));
        } else if (payload.status === 'error') {
          message.error(t(lang, 'update_failed', { error: updateErrorText(lang, payload) }));
        }
      }),
    [lang, message],
  );

  const openRepo = () => getApi().then((api) => api.open_repository());
  const checking = updateStatus ? ['checking', 'downloading', 'installing', 'restart_pending'].includes(updateStatus.status) : false;
  const checkUpdate = async () => {
    setUpdateStatus({ status: 'checking' });
    try {
      const api = await getApi();
      const result = await api.check_for_updates();
      if (!result.ok) {
        setUpdateStatus(null);
        message.error(t(lang, result.error ?? 'update_failed'));
      }
    } catch (error) {
      setUpdateStatus({ status: 'error', error: String(error) });
      message.error(t(lang, 'update_failed', { error: String(error) }));
    }
  };

  return (
    <Modal
      title={t(lang, 'about_title')}
      open={store.aboutOpen}
      onCancel={store.closeAbout}
      footer={null}
    >
      {info && (
        <div style={{ textAlign: 'center', padding: '20px 0 8px' }}>
          <Typography.Title level={2} style={{ marginBottom: 8 }}>
            {info.app_name}
          </Typography.Title>
          <div className="about-meta">v{info.version}</div>
          <div className="about-meta">{info.author}</div>
          <Space style={{ marginTop: 24 }} wrap>
            <Button type="primary" icon={<GithubOutlined />} onClick={openRepo}>
              {t(lang, 'open_repository')}
            </Button>
            <Button icon={<SyncOutlined />} loading={checking} onClick={checkUpdate}>
              {updateButtonText(lang, updateStatus)}
            </Button>
          </Space>
          {updateStatus && updateStatus.status !== 'up_to_date' && updateStatus.status !== 'error' && (
            <div className="about-update-status">{updateStatusText(lang, updateStatus)}</div>
          )}
        </div>
      )}
    </Modal>
  );
}

function updateButtonText(lang: 'zh' | 'en', status: UpdateStatusPayload | null): string {
  if (!status) return t(lang, 'check_updates');
  if (status.status === 'checking') return t(lang, 'update_checking');
  if (status.status === 'downloading') return t(lang, 'update_downloading');
  if (status.status === 'installing') return t(lang, 'update_installing');
  if (status.status === 'restart_pending') return t(lang, 'update_restart_pending_short');
  return t(lang, 'check_updates');
}

function updateStatusText(lang: 'zh' | 'en', status: UpdateStatusPayload): string {
  if (status.status === 'downloading' && status.total && status.total > 0) {
    const progress = Math.min(100, Math.round(((status.downloaded ?? 0) / status.total) * 100));
    return t(lang, 'update_downloading_progress', { progress });
  }
  if (status.status === 'downloading') return t(lang, 'update_downloading');
  if (status.status === 'installing') return t(lang, 'update_installing');
  if (status.status === 'restart_pending') {
    return t(lang, 'update_restart_pending', { version: status.latest_version ?? '' });
  }
  return t(lang, 'update_checking');
}

function updateErrorText(lang: 'zh' | 'en', status: UpdateStatusPayload): string {
  if (status.error_key) {
    const translated = t(lang, status.error_key);
    if (translated !== status.error_key) return translated;
  }
  return status.error ?? status.error_key ?? '';
}
