import { theme, type ThemeConfig } from "antd";
import type { ThemeMode } from "./themeMode";

const fontFamily =
  '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "PingFang SC", system-ui, sans-serif';

const darkTheme: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: "#14b8a6",
    colorInfo: "#14b8a6",
    colorBgBase: "#0f172a",
    colorLink: "#5eead4",
    borderRadius: 8,
    fontFamily,
  },
  components: {
    Layout: {
      headerBg: "#111c33",
      bodyBg: "#0f172a",
    },
    Card: {
      colorBgContainer: "#111c33",
    },
    Table: {
      headerBg: "#16233d",
      colorBgContainer: "#0f1a2e",
    },
    Modal: {
      contentBg: "#111c33",
      headerBg: "#111c33",
    },
  },
};

const lightTheme: ThemeConfig = {
  algorithm: theme.defaultAlgorithm,
  token: {
    colorPrimary: "#0f766e",
    colorInfo: "#0f766e",
    colorBgBase: "#f8fafc",
    colorLink: "#0f766e",
    borderRadius: 8,
    fontFamily,
  },
  components: {
    Layout: {
      headerBg: "#ffffff",
      bodyBg: "#f8fafc",
    },
    Card: {
      colorBgContainer: "#ffffff",
    },
    Table: {
      headerBg: "#f1f5f9",
      colorBgContainer: "#ffffff",
    },
    Modal: {
      contentBg: "#ffffff",
      headerBg: "#ffffff",
    },
  },
};

export function getAppTheme(mode: ThemeMode): ThemeConfig {
  return mode === "dark" ? darkTheme : lightTheme;
}
