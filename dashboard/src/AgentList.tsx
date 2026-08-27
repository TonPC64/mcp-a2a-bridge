import { useState } from "react";
import { DetailDialog } from "./DetailDialog";
import type { TaskActivity } from "./TaskList";

export interface AgentSkill {
  id: string;
  name: string;
  description: string;
  tags: string[];
  examples: string[];
}

export interface Agent {
  name: string;
  configured_url: string;
  reachable: boolean;
  description?: string;
  version?: string;
  url?: string | null;
  streaming?: boolean;
  input_modes?: string[];
  output_modes?: string[];
  skills?: AgentSkill[];
  error?: string;
}

export function AgentList({ agents, tasks = [] }: { agents: Agent[]; tasks?: TaskActivity[] }) {
  if (agents.length === 0) {
    return <p className="empty-state" role="status">No agents configured yet.</p>;
  }

  return (
    <div className="agent-grid agent-grid-compact">
      {agents.map((agent) => <AgentCard agent={agent} key={agent.name} taskCount={tasks.filter((task) => (task.destination ?? task.source ?? task.agent) === agent.name).length} />)}
    </div>
  );
}

function AgentCard({ agent, taskCount }: { agent: Agent; taskCount: number }) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const skills = agent.skills ?? [];
  const headingId = `agent-${agent.name.replace(/[^a-zA-Z0-9_-]/g, "-")}`;

  return (
    <article className={`agent-card${agent.reachable ? "" : " agent-card-unreachable"}`} aria-labelledby={headingId} tabIndex={0} onClick={() => setDetailsOpen(true)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setDetailsOpen(true); } }}>
      <header className="agent-card-header">
        <h3 id={headingId}>{agent.name}</h3>
        <span className={`agent-status-dot agent-status-${agent.reachable ? "reachable" : "unreachable"}`} role="status" aria-live="polite" aria-label={`${agent.name} is ${agent.reachable ? "reachable" : "unreachable"}`} title={`${agent.name} is ${agent.reachable ? "reachable" : "unreachable"}`} />
      </header>

      <p className="agent-task-count">{taskCount} {taskCount === 1 ? "task" : "tasks"}</p>
      {detailsOpen && <DetailDialog title={`${agent.name} details`} closeLabel={`Close ${agent.name} details`} onClose={() => setDetailsOpen(false)}>
        <p>Status <span className={`badge ${agent.reachable ? "badge-ok" : "badge-error"}`}>{agent.reachable ? "reachable" : "unreachable"}</span></p>
        {agent.description && <p className="agent-description">{agent.description}</p>}
        {agent.configured_url && <dl className="agent-metadata"><div><dt>Address</dt><dd><code>{agent.configured_url}</code></dd></div></dl>}
        {!agent.reachable && agent.error && <p className="agent-error" role="alert">Connection issue: {agent.error}</p>}
        {skills.length > 0 && <section className="agent-skills" aria-label={`${agent.name} skills`}><h4>Skills</h4><div className="skill-list">{skills.map((skill) => <span className="skill-chip" key={skill.id}>{skill.name}</span>)}</div></section>}
      </DetailDialog>}
    </article>
  );
}
