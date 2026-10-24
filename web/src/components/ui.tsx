import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../api/client";

export function errText(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  return e instanceof Error ? e.message : String(e);
}

const TONE: Record<string, string> = {
  succeeded: "ok",
  PASS: "ok",
  completed: "ok",
  failed: "bad",
  FAIL: "bad",
  cancelled: "warn",
  running: "run",
  queued: "",
  waiting: "warn",
  open: "warn",
  skipped: "",
};

export function Badge({ value }: { value: string | null | undefined }) {
  if (!value) return null;
  return <span className={`badge ${TONE[value] ?? ""}`}>{value}</span>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="err" role="alert">
      {errText(error)}
    </div>
  );
}

export function fmtTime(s?: string | null): string {
  if (!s) return "";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? s : d.toLocaleString();
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KiB`;
  return `${(n / 1048576).toFixed(1)} MiB`;
}

export interface Loaded<T> {
  data: T | undefined;
  error: unknown;
  loading: boolean;
  reload: () => Promise<void>;
}

/** Load once per key and on demand; keeps the previous data while reloading. */
export function useLoad<T>(fn: () => Promise<T>, key: string): Loaded<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<unknown>();
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const seq = useRef(0);
  const reload = useCallback(async () => {
    const my = ++seq.current;
    try {
      const v = await fnRef.current();
      if (my === seq.current) {
        setData(v);
        setError(undefined);
      }
    } catch (e) {
      if (my === seq.current) setError(e);
    } finally {
      if (my === seq.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    setLoading(true);
    void reload();
    return () => {
      seq.current++;
    };
  }, [key, reload]);
  return { data, error, loading, reload };
}

export function Dialog({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <div className="dialog-bg" onClick={onClose} role="presentation">
      <div className="card" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </div>
  );
}
