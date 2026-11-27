import { Navigate, Route, Routes } from "react-router-dom";
import Shell from "./components/Shell";
import Dashboard from "./views/Dashboard";
import Inbox from "./views/Inbox";
import Agents from "./views/Agents";
import AgentDetail from "./views/AgentDetail";
import Chat from "./views/Chat";
import Sessions from "./views/Sessions";
import Maintainer from "./views/Maintainer";

export default function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/inbox" element={<Inbox />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/agents/:name" element={<AgentDetail />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/chat/:sessionId" element={<Chat />} />
        <Route path="/sessions" element={<Sessions />} />
        <Route path="/maintainer" element={<Maintainer />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
