import { get } from "../api";
import { DIM_NAMES, DIMS, Empty, navigate, Spinner, useAsync } from "../ui";

export function Calibration() {
  const { data, error, loading } = useAsync(() => get("/api/calibration"), []);
  if (loading) return <Spinner label="Loading calibration" />;
  if (error) return <p className="error-text">{error}</p>;
  return (
    <>
      <div className="page-head">
        <div><h1>Calibration</h1><p className="sub">How close the AI's marks are to yours. Mark vivas blind (from a viva's review page) to add points here.</p></div>
      </div>
      {!data.n ? <div className="panel"><Empty><p>No faculty marks yet.</p><p>Open any scored viva and choose “Mark blind”. Ten or more vivas give a meaningful comparison.</p></Empty></div> : (
        <div className="stack">
          <div className="panel panel-pad row" style={{ gap: 36 }}>
            <Stat value={data.n} label="vivas compared" />
            <Stat value={data.mae_total} label="average gap (marks /100)" />
            <Stat value={data.pearson ?? "–"} label="correlation (Pearson r)" />
            <Stat value={data.spearman ?? "–"} label="rank correlation (Spearman)" />
            <Stat value={`${Math.round(data.within_10 * 100)}%`} label="within 10 marks" />
            <Stat value={(data.bias_total > 0 ? "+" : "") + data.bias_total} label="AI minus faculty, on average" />
          </div>
          <div className="grid-2">
            <div className="panel panel-pad stack">
              <h2>AI total vs your total</h2>
              <Scatter pairs={data.pairs} />
            </div>
            <div className="panel panel-pad stack">
              <h2>By dimension (0–5 scale)</h2>
              <table className="tbl">
                <thead><tr><th>Dimension</th><th className="r">Avg gap</th><th className="r">Bias</th><th className="r">Exact</th><th className="r">Within 1</th></tr></thead>
                <tbody>{DIMS.map((k) => {
                  const d = data.per_dimension[k];
                  return <tr key={k}><td>{DIM_NAMES[k]}</td><td className="r num">{d.mae}</td><td className="r num">{d.bias > 0 ? "+" : ""}{d.bias}</td><td className="r num">{Math.round(d.exact * 100)}%</td><td className="r num">{Math.round(d.within_1 * 100)}%</td></tr>;
                })}</tbody>
              </table>
            </div>
          </div>
          <div className="panel">
            <table className="tbl">
              <thead><tr><th>Student</th><th className="r">Your total</th><th className="r">AI total</th><th className="r">Gap</th><th>Blind</th></tr></thead>
              <tbody>{data.pairs.map((p: any) => (
                <tr key={p.session_id} className="click" onClick={() => navigate(`/faculty/sessions/${p.session_id}`)}>
                  <td>{p.student} <span className="muted">{p.roll_no}</span></td><td className="r num">{p.faculty_total}</td><td className="r num">{p.ai_total}</td>
                  <td className="r num"><b>{p.gap > 0 ? "+" : ""}{p.gap}</b></td><td>{p.blind ? "yes" : "no"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}

function Stat({ value, label }: { value: any; label: string }) {
  return <div className="stat"><b style={{ fontSize: 26 }}>{value}</b><span>{label}</span></div>;
}

function Scatter({ pairs }: { pairs: any[] }) {
  const W = 360, H = 300, P = 36;
  const x = (v: number) => P + (v / 100) * (W - P - 10);
  const y = (v: number) => H - P - (v / 100) * (H - P - 10);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="scatter" role="img" aria-label="AI total plotted against faculty total">
      <rect x={P} y={10} width={W - P - 10} height={H - P - 10} fill="#fafbf8" stroke="#d9ded8" />
      {[0, 25, 50, 75, 100].map((v) => (
        <g key={v}>
          <line x1={x(v)} x2={x(v)} y1={10} y2={H - P} stroke="#e8ebe6" />
          <line y1={y(v)} y2={y(v)} x1={P} x2={W - 10} stroke="#e8ebe6" />
          <text x={x(v)} y={H - P + 16} fontSize="11" textAnchor="middle" fill="#7a877f">{v}</text>
          <text x={P - 6} y={y(v) + 4} fontSize="11" textAnchor="end" fill="#7a877f">{v}</text>
        </g>
      ))}
      <polygon points={`${x(0)},${y(10)} ${x(90)},${y(100)} ${x(100)},${y(100)} ${x(100)},${y(90)} ${x(10)},${y(0)} ${x(0)},${y(0)}`} fill="#ffe68a55" />
      <line x1={x(0)} y1={y(0)} x2={x(100)} y2={y(100)} stroke="#a9b3ac" strokeDasharray="4 4" />
      {pairs.map((p) => <circle key={p.session_id} cx={x(p.faculty_total)} cy={y(p.ai_total)} r={5} fill="#1e2a25"><title>{`${p.student}: faculty ${p.faculty_total}, AI ${p.ai_total}`}</title></circle>)}
      <text x={(W + P) / 2} y={H - 4} fontSize="12" textAnchor="middle" fill="#4a5a53">Your total</text>
      <text x={12} y={H / 2} fontSize="12" textAnchor="middle" fill="#4a5a53" transform={`rotate(-90 12 ${H / 2})`}>AI total</text>
    </svg>
  );
}
