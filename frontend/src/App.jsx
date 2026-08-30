import { NavLink, Route, Routes } from "react-router-dom";
import Dashboard from "./pages/Dashboard.jsx";
import Registry from "./pages/Registry.jsx";
import LiveViewer from "./pages/LiveViewer.jsx";

const linkStyle = ({ isActive }) => ({
  marginRight: 16,
  fontWeight: isActive ? 700 : 400,
  textDecoration: "none",
});

export default function App() {
  return (
    <div style={{ fontFamily: "sans-serif", padding: 16 }}>
      <header style={{ marginBottom: 24 }}>
        <h1 style={{ marginBottom: 4 }}>netra</h1>
        <p style={{ marginTop: 0, color: "#666" }}>
          Unified CCTV registry &amp; live viewing — Sentinel Gujarat
        </p>
        <nav>
          <NavLink to="/" end style={linkStyle}>
            Dashboard
          </NavLink>
          <NavLink to="/registry" style={linkStyle}>
            Registry &amp; GIS (Model 1)
          </NavLink>
          <NavLink to="/live" style={linkStyle}>
            Live Viewer (Model 2)
          </NavLink>
        </nav>
      </header>

      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/registry" element={<Registry />} />
        <Route path="/live" element={<LiveViewer />} />
      </Routes>
    </div>
  );
}
