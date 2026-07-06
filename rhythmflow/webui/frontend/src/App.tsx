import { ConfigProvider, Layout, Spin } from "antd";
import enUS from "antd/locale/en_US";
import zhCN from "antd/locale/zh_CN";
import { AboutModal } from "./components/AboutModal";
import { AppHeader } from "./components/Header";
import { MainPage } from "./components/MainPage";
import { OsuAssistantPage } from "./components/osu/OsuAssistantPage";
import { ReviewModal } from "./components/review/ReviewModal";
import { useStore } from "./store";

export default function App(): JSX.Element {
  const store = useStore();

  if (!store.ready) {
    return (
      <div style={{ height: "100%", display: "grid", placeItems: "center" }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <ConfigProvider locale={store.language === "en" ? enUS : zhCN}>
      <Layout className="app-layout">
        <AppHeader />
        <Layout.Content className="app-content">
          {store.page === "osu" ? <OsuAssistantPage /> : <MainPage />}
        </Layout.Content>
      </Layout>
      <ReviewModal />
      <AboutModal />
    </ConfigProvider>
  );
}
