import { App as AntApp, ConfigProvider } from 'antd';
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { StoreProvider } from './store';
import { getAppTheme } from './theme';
import { ThemeModeProvider, useThemeMode } from './themeMode';
import './styles.css';

function ThemedRoot(): JSX.Element {
  const { mode } = useThemeMode();

  return (
    <ConfigProvider theme={getAppTheme(mode)}>
      <AntApp>
        <StoreProvider>
          <App />
        </StoreProvider>
      </AntApp>
    </ConfigProvider>
  );
}

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <ThemeModeProvider>
      <ThemedRoot />
    </ThemeModeProvider>
  </React.StrictMode>,
);
