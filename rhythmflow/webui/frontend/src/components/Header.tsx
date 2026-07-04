import { InfoCircleOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons';
import { Button, Select, Space, Switch, Tooltip } from 'antd';
import { useMemo } from 'react';
import { t } from '../i18n';
import { useStore } from '../store';
import { pickSubtitle } from '../subtitles';
import { useThemeMode } from '../themeMode';
import type { Language } from '../types';

export function AppHeader(): JSX.Element {
  const store = useStore();
  const { mode, toggle } = useThemeMode();
  const lang = store.language;
  const subtitle = useMemo(() => pickSubtitle(lang), [lang]);

  return (
    <header className="app-header">
      <div className="app-brand">
        <div className="app-title">RhythmFlow</div>
        <div className="app-subtitle">{subtitle}</div>
      </div>
      <div style={{ flex: 1 }} />
      <Space size={12}>
        <span className="header-label">{t(lang, 'language')}</span>
        <Select<Language>
          value={lang}
          style={{ width: 128 }}
          onChange={(value) => store.updateSettings({ language: value })}
          options={[
            { value: 'zh', label: '中文' },
            { value: 'en', label: 'English' },
          ]}
        />
        <Tooltip title={t(lang, 'about_button')}>
          <Button
            shape="circle"
            icon={<InfoCircleOutlined />}
            onClick={store.openAbout}
            aria-label={t(lang, 'about_button')}
          />
        </Tooltip>
        <Tooltip title={t(lang, 'theme_toggle')}>
          <Switch
            checked={mode === 'dark'}
            checkedChildren={<MoonOutlined />}
            unCheckedChildren={<SunOutlined />}
            onChange={toggle}
            aria-label={t(lang, 'theme_toggle')}
          />
        </Tooltip>
      </Space>
    </header>
  );
}
