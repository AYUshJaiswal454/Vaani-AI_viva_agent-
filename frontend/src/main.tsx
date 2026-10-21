import "./styles.css";
import { createRoot } from "react-dom/client";
import { useEffect, useState } from "react";
import { get } from "./api";
import { Toaster, useLocation, navigate } from "./ui";
import { Join } from "./student/Join";
import { Prepare } from "./student/Prepare";
import { Room } from "./student/Room";
import { Login } from "./faculty/Login";
import { Shell } from "./faculty/Shell";

function App() {
  const path = useLocation();
  const [me, setMe] = useState<any>(undefined);
  const staffArea = path.startsWith("/faculty") || path.startsWith("/login");
  useEffect(() => {
    if (staffArea) get("/api/auth/me").then((r) => setMe(r.user)).catch(() => setMe(null));
  }, [staffArea]);

  if (path.startsWith("/viva/room")) return <Room />;
  if (path.startsWith("/viva")) return <Prepare />;
  if (path.startsWith("/login")) return <Login onLogin={(u) => { setMe(u); navigate("/faculty"); }} />;
  if (path.startsWith("/faculty")) {
    if (me === undefined) return <div className="login"><span className="spinner" style={{ color: "#eef0e8" }} /></div>;
    if (!me || (me.role !== "faculty" && me.role !== "admin")) { navigate("/login"); return null; }
    return <Shell me={me} path={path} onLogout={() => setMe(null)} />;
  }
  return <Join />;
}

createRoot(document.getElementById("root")!).render(<><App /><Toaster /></>);
