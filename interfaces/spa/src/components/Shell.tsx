import { ReactNode, useState } from "react";
import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Inbox as InboxIcon,
  Bot,
  MessagesSquare,
  History,
  Sparkles,
} from "lucide-react";
import { useInbox } from "../api";
import Telemetry from "./Telemetry";
import ThemeToggle from "./ThemeToggle";
import "./shell.css";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/inbox", label: "Inbox", icon: InboxIcon },
  { to: "/agents", label: "Agents", icon: Bot },
  { to: "/chat", label: "Chat", icon: MessagesSquare },
  { to: "/sessions", label: "Sessions", icon: History },
];

export default function Shell({ children }: { children: ReactNode }) {
  const { data: inbox } = useInbox();
  const pending = inbox?.length ?? 0;
  const [teleCollapsed, setTeleCollapsed] = useState(
    () => localStorage.getItem("orion-tele-collapsed") === "1",
  );
  const toggleTele = () =>
    setTeleCollapsed((v) => {
      localStorage.setItem("orion-tele-collapsed", v ? "0" : "1");
      return !v;
    });

  return (
    <div className={`shell${teleCollapsed ? " tele-collapsed" : ""}`}>
      <nav className="rail">
        <div className="brand">
          <span className="brand-mark">
            <Sparkles size={17} strokeWidth={2.2} />
          </span>
          <div>
            <div className="brand-name">Orion</div>
            <div className="brand-sub">Knowledge OS</div>
          </div>
        </div>

        <ul className="nav">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <li key={to}>
              <NavLink
                to={to}
                end={end}
                className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
              >
                <Icon size={17} strokeWidth={1.9} />
                <span>{label}</span>
                {to === "/inbox" && pending > 0 && (
                  <span className="count-pill alert">{pending}</span>
                )}
              </NavLink>
            </li>
          ))}
        </ul>

        <div className="nav-foot">
          <ThemeToggle />
        </div>
      </nav>

      <main className="main">{children}</main>

      <Telemetry collapsed={teleCollapsed} onToggle={toggleTele} />
    </div>
  );
}
