import { AgentList, type Agent } from "./AgentList";
import { TaskList, type TaskActivity } from "./TaskList";
import { useEventSource } from "./useEventSource";

export default function App() {
  const agents = useEventSource<{ agents: Agent[] }>("/api/agents/events", "agents");
  const tasks = useEventSource<{ tasks: TaskActivity[] }>("/api/tasks/events", "tasks");

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
