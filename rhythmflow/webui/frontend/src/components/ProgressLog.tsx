import { Card, Progress } from "antd";
import { useEffect, useRef } from "react";
import { t } from "../i18n";
import { useStore } from "../store";

export function ProgressLog(): JSX.Element {
  const store = useStore();
  const lang = store.language;
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [store.log]);

  return (
    <Card title={t(lang, "progress")} size="small">
      <Progress percent={store.progress} status={store.busy ? "active" : "normal"} />
      <div className="log-panel" ref={logRef} style={{ marginTop: 10 }}>
        {store.log.length === 0 ? (
          <span className="log-empty">{t(lang, "log_empty")}</span>
        ) : (
          store.log.map((line, index) => <div key={index}>{line}</div>)
        )}
      </div>
    </Card>
  );
}
