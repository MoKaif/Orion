import { useEffect, useState } from "react";
import { Check, Gauge, Monitor, Moon, PanelRight, Sun, X } from "lucide-react";
import "./settings-modal.css";

type Theme = "dark" | "light";
type Density = "comfortable" | "compact";

function apply(theme: Theme, density: Density, reducedMotion: boolean) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.density = density;
  document.documentElement.dataset.motion = reducedMotion ? "reduced" : "full";
}

export default function SettingsModal({
  open,
  onClose,
  telemetryCollapsed,
  onTelemetryToggle,
}: {
  open: boolean;
  onClose: () => void;
  telemetryCollapsed: boolean;
  onTelemetryToggle: () => void;
}) {
  const [theme, setTheme] = useState<Theme>(() =>
    localStorage.getItem("orion-theme") === "light" ? "light" : "dark",
  );
  const [density, setDensity] = useState<Density>(() =>
    localStorage.getItem("orion-density") === "compact" ? "compact" : "comfortable",
  );
  const [reducedMotion, setReducedMotion] = useState(
    () => localStorage.getItem("orion-motion") === "reduced",
  );

  useEffect(() => {
    apply(theme, density, reducedMotion);
    localStorage.setItem("orion-theme", theme);
    localStorage.setItem("orion-density", density);
    localStorage.setItem("orion-motion", reducedMotion ? "reduced" : "full");
  }, [theme, density, reducedMotion]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title"
        onMouseDown={(event) => event.stopPropagation()}>
        <header className="modal-head">
          <div>
            <p className="eyebrow">Workspace</p>
            <h2 id="settings-title">Settings</h2>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close settings"><X size={18} /></button>
        </header>

        <div className="setting-block">
          <div className="setting-copy"><Monitor size={17} /><div><strong>Appearance</strong><span>Choose the room Orion works in.</span></div></div>
          <div className="segmented">
            <button className={theme === "dark" ? "selected" : ""} onClick={() => setTheme("dark")}><Moon size={15} /> Void {theme === "dark" && <Check size={13} />}</button>
            <button className={theme === "light" ? "selected" : ""} onClick={() => setTheme("light")}><Sun size={15} /> Light {theme === "light" && <Check size={13} />}</button>
          </div>
        </div>

        <div className="setting-block">
          <div className="setting-copy"><Gauge size={17} /><div><strong>Density</strong><span>Control how much fits on screen.</span></div></div>
          <div className="segmented">
            <button className={density === "comfortable" ? "selected" : ""} onClick={() => setDensity("comfortable")}>Comfortable</button>
            <button className={density === "compact" ? "selected" : ""} onClick={() => setDensity("compact")}>Compact</button>
          </div>
        </div>

        <label className="setting-switch">
          <span className="setting-copy"><PanelRight size={17} /><span><strong>Telemetry rail</strong><small>Provider and world-model vitals.</small></span></span>
          <input type="checkbox" checked={!telemetryCollapsed} onChange={onTelemetryToggle} />
          <i aria-hidden />
        </label>
        <label className="setting-switch">
          <span className="setting-copy"><Gauge size={17} /><span><strong>Reduce motion</strong><small>Disable decorative transitions.</small></span></span>
          <input type="checkbox" checked={reducedMotion} onChange={(e) => setReducedMotion(e.target.checked)} />
          <i aria-hidden />
        </label>
      </section>
    </div>
  );
}
