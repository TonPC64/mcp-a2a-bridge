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

export function AgentList({ agents }: { agents: Agent[] }) {
  if (agents.length === 0) {
    return <p>No agents configured.</p>;
  }

  return (
    <table>
      <thead>
        <tr>
          <th>Name</th>
          <th>Status</th>
          <th>URL</th>
          <th>Skills</th>
        </tr>
      </thead>
      <tbody>
        {agents.map((agent) => (
          <tr key={agent.name}>
            <td>{agent.name}</td>
            <td>
              {agent.reachable ? (
                <span className="badge badge-ok">reachable</span>
              ) : (
                <span className="badge badge-error" title={agent.error}>
                  unreachable
                </span>
              )}
            </td>
            <td>{agent.configured_url}</td>
            <td>{(agent.skills ?? []).map((skill) => skill.name).join(", ") || "\u2014"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
