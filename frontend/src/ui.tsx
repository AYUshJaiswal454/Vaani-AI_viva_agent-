import { useEffect, useState, type ReactNode } from "react";

export function navigate(to: string) {
  if (location.pathname + location.search === to) return;
  history.pushState({}, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
  window.scrollTo(0, 0);
}

export function useLocation(): string {
  const [path, setPath] = useState(location.pathname + location.search);
  useEffect(() => {
    const on = () => setPath(location.pathname + location.search);
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  return path;
}

export function Link({ to, children, className }: { to: string; children: ReactNode; className?: string }) {
  return (
    <a href={to} className={className} onClick={(e) => { if (e.metaKey || e.ctrlKey) return; e.preventDefault(); navigate(to); }}>
      {children}
    </a>
  );
}

let toastSet: ((s: string) => void) | null = null;
export function toast(msg: string) { toastSet?.(msg); }
export function Toaster() {
  const [msg, setMsg] = useState("");
  useEffect(() => {
    toastSet = (m) => { setMsg(m); window.setTimeout(() => setMsg(""), 3800); };
    return () => { toastSet = null; };
  }, []);
  return msg ? <div className="toast" role="status">{msg}</div> : null;
}

export function Spinner({ label }: { label?: string }) {
  return <span className="row muted"><span className="spinner" aria-hidden />{label && <span>{label}</span>}</span>;
}

export function Dots({ value, max = 5, pen = false }: { value: number | null | undefined; max?: number; pen?: boolean }) {
  const v = value ?? 0;
  return (
    <span className="dots" aria-label={`${v} out of ${max}`}>
      {Array.from({ length: max }, (_, i) => <i key={i} className={`${i < v ? "on" : ""} ${pen ? "pen" : ""}`} />)}
      <span className="val">{value ?? "–"}<span className="muted">/{max}</span></span>
    </span>
  );
}

export function useAsync<T>(fn: () => Promise<T>, deps: any[]): { data: T | null; error: string; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [n, setN] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    fn().then((d) => { if (alive) { setData(d); setError(""); } })
      .catch((e) => { if (alive) setError(e.message || String(e)); })
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n]);
  return { data, error, loading, reload: () => setN((x) => x + 1) };
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export const DIM_NAMES: Record<string, string> = {
  conceptual: "Conceptual understanding",
  depth: "Depth and reasoning",
  problem_solving: "Problem solving",
  practical: "Practical / project knowledge",
  communication: "Communication",
};
export const DIMS = ["conceptual", "depth", "problem_solving", "practical", "communication"];

export function statusPill(s: string, needsReview?: boolean) {
  if (needsReview) return <span className="pill review">Needs review</span>;
  const map: Record<string, [string, string]> = {
    live: ["live", "In progress"], ended: ["", "Processing"], scoring: ["", "Scoring"], scored: ["ok", "Scored"],
    reviewed: ["ok", "Reviewed"], abandoned: ["red", "Abandoned"], created: ["", "Joined"], consented: ["", "Ready"],
    open: ["open", "Open"], closed: ["", "Closed"], draft: ["", "Draft"],
  };
  const [cls, label] = map[s] || ["", s];
  return <span className={`pill ${cls}`}>{label}</span>;
}
