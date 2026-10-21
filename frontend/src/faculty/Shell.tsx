import { post } from "../api";
import { Link, navigate } from "../ui";
import { Vivas, NewViva } from "./Vivas";
import { VivaDetail } from "./VivaDetail";
import { Review } from "./Review";
import { Banks, BankEditor } from "./Banks";
import { Calibration } from "./Calibration";
import { Lab } from "./Lab";
import { System } from "./System";

export function Shell({ me, path, onLogout }: { me: any; path: string; onLogout: () => void }) {
  const p = path.split("?")[0];
  const nav = [
    ["/faculty", "Vivas"], ["/faculty/banks", "Question banks"], ["/faculty/calibration", "Calibration"],
    ["/faculty/lab", "Speech lab"], ["/faculty/system", "System"],
  ];
  const active = (href: string) => (href === "/faculty" ? p === "/faculty" || p.startsWith("/faculty/vivas") || p.startsWith("/faculty/sessions") : p.startsWith(href));
  let page;
  let m: RegExpMatchArray | null;
  if ((m = p.match(/^\/faculty\/sessions\/(\d+)/))) page = <Review id={+m[1]} />;
  else if (p === "/faculty/vivas/new") page = <NewViva />;
  else if ((m = p.match(/^\/faculty\/vivas\/(\d+)/))) page = <VivaDetail id={+m[1]} />;
  else if ((m = p.match(/^\/faculty\/banks\/(\d+)/))) page = <BankEditor id={+m[1]} />;
  else if (p.startsWith("/faculty/banks")) page = <Banks />;
  else if (p.startsWith("/faculty/calibration")) page = <Calibration />;
  else if (p.startsWith("/faculty/lab")) page = <Lab />;
  else if (p.startsWith("/faculty/system")) page = <System />;
  else page = <Vivas />;
  return (
    <div className="paper">
      <aside className="side">
        <div className="wordmark">vaani<span className="dot" style={{ color: "#f2c94c" }}>.</span></div>
        <nav aria-label="Main">{nav.map(([href, label]) => <Link key={href} to={href} className={active(href) ? "active" : ""}>{label}</Link>)}</nav>
        <div className="who">
          <b>{me.name}</b>
          <span>{me.email}</span>
          <button className="btn link" onClick={async () => { await post("/api/auth/logout"); onLogout(); navigate("/login"); }}>Sign out</button>
        </div>
      </aside>
      <main className="page">{page}</main>
    </div>
  );
}
