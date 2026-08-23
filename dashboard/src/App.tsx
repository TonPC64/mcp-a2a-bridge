import { AgentList, type Agent } from "./AgentList";
import { TaskList, type TaskActivity } from "./TaskList";
import { useApi } from "./useApi";

const POLL_INTERVAL_MS = 3000;

export default function App() {
  const agents = useApi<{ agents: Agent[] }>("/api/agents", POLL_INTERVAL_MS);
  const tasks = useApi<{ tasks: TaskActivity[] }>("/api/tasks", POLL_INTERVAL_MS);

  return (
    <main>
      <h1>A2A Bridge Dashboard</h1>

      <section>
        <h2>Agents</h2>
        {agents.error && <p className="error">{agents.error}</p>}
        <AgentList agents={agents.data?.agents ?? []} />
      </section>

      <section>
        <h2>Task activity</h2>
        {tasks.error && <p className="error">{tasks.error}</p>}
        <TaskList tasks={tasks.data?.tasks ?? []} />
      </section>
    </main>
  );
}
