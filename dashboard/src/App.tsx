import { useEffect, useState } from "react";
import { AgentList, type Agent } from "./AgentList";
import { TaskList, type TaskActivity } from "./TaskList";
import { useEventSource } from "./useEventSource";

export default function App() {
  const [showJumpToTop, setShowJumpToTop] = useState(false);
  const agents = useEventSource<{ agents: Agent[] }>("/api/agents/events", "agents");
  const tasks = useEventSource<{ tasks: TaskActivity[] }>("/api/tasks/events", "tasks");
  const reconnecting = Boolean(agents.error || tasks.error);
  const agentsLoading = agents.data === null && !agents.error;
  const tasksLoading = tasks.data === null && !tasks.error;

  useEffect(() => {
    const updateJumpToTop = () => setShowJumpToTop(window.scrollY > 300);
    updateJumpToTop();
    window.addEventListener("scroll", updateJumpToTop, { passive: true });
    return () => window.removeEventListener("scroll", updateJumpToTop);
  }, []);

  return (
    <main className="dashboard-shell">
      <header className="dashboard-header">
        <div>
          <p className="eyebrow">Live operations</p>
          <h1>A2A Bridge Dashboard</h1>
          <p className="lede">A clear view of your connected agents and the work moving between them.</p>
        </div>
        <p className={`live-indicator${reconnecting ? " reconnecting" : ""}`}><span aria-hidden="true" />{reconnecting ? "Reconnecting" : "Live updates"}</p>
      </header>

      <section className="glass-panel" aria-labelledby="agents-heading" aria-busy={agentsLoading}>
        <div className="section-heading">
          <div>
            <p className="eyebrow">Directory</p>
            <h2 id="agents-heading">Agents</h2>
          </div>
          {agents.data && <span className="item-count">{agents.data.agents.length} configured</span>}
        </div>
        {agents.error && <p className="error" role="alert">{agents.error}</p>}
        {agentsLoading ? <p className="loading-state" role="status" aria-label="Loading agents">Loading agents<span aria-hidden="true">…</span></p> : agents.data && <AgentList agents={agents.data.agents} tasks={tasks.data?.tasks} />}
      </section>

      <section className="glass-panel" aria-labelledby="tasks-heading" aria-busy={tasksLoading}>
        <div className="section-heading">
          <div>
            <p className="eyebrow">Rolling history</p>
            <h2 id="tasks-heading">Task activity</h2>
          </div>
          {tasks.data && <span className="item-count">{tasks.data.tasks.length} recent</span>}
        </div>
        {tasks.error && <p className="error" role="alert">{tasks.error}</p>}
        {tasksLoading ? <p className="loading-state" role="status" aria-label="Loading task activity">Loading task activity<span aria-hidden="true">…</span></p> : tasks.data && <TaskList tasks={tasks.data.tasks} />}
      </section>
      {showJumpToTop && <button className="jump-to-top" type="button" aria-label="Jump to top" onClick={() => window.scrollTo({ top: 0, behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" })}>↑</button>}
    </main>
  );
}
