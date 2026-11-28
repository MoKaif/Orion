import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import Shell from "./components/Shell";
import Dashboard from "./views/Dashboard";
import Inbox from "./views/Inbox";
import Agents from "./views/Agents";
import AgentDetail from "./views/AgentDetail";
import Chat from "./views/Chat";
import Sessions from "./views/Sessions";

function MaintainerRedirect() {
  const { search, hash } = useLocation();
  return <Navigate to={`/agents/maintainer${search}${hash}`} replace />;
}

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
        <Route path="/maintainer" element={<MaintainerRedirect />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
