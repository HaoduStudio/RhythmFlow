import { GithubOutlined } from '@ant-design/icons';
import { Button, Modal, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { getApi } from '../bridge';
import { t } from '../i18n';
import { useStore } from '../store';
import type { AboutInfo } from '../types';

export function AboutModal(): JSX.Element {
  const store = useStore();
  const lang = store.language;
  const [info, setInfo] = useState<AboutInfo | null>(null);

  useEffect(() => {
    if (store.aboutOpen && !info) {
      getApi().then((api) => api.about_info().then(setInfo));
    }
  }, [store.aboutOpen, info]);

  const openRepo = () => getApi().then((api) => api.open_repository());

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
          <Button
            type="primary"
            icon={<GithubOutlined />}
            onClick={openRepo}
            style={{ marginTop: 24 }}
          >
            {t(lang, 'open_repository')}
          </Button>
        </div>
      )}
    </Modal>
  );
}
