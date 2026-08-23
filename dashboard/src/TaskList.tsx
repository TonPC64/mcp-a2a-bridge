export interface TaskActivity {
  id: string;
  agent: string;
  kind: string;
  state: string;
  text: string;
  created_at: number;
  updated_at: number;
}

function formatTime(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString();
}

export function TaskList({ tasks }: { tasks: TaskActivity[] }) {
  if (tasks.length === 0) {
    return <p>No task activity yet.</p>;
  }

  return (
    <table>
      <thead>
        <tr>
          <th>Task</th>
          <th>Agent</th>
          <th>Kind</th>
          <th>State</th>
          <th>Last update</th>
          <th>Text</th>
        </tr>
      </thead>
      <tbody>
        {tasks.map((task) => (
          <tr key={task.id}>
            <td>{task.id.slice(0, 8)}</td>
            <td>{task.agent}</td>
            <td>{task.kind}</td>
            <td>
              <span className={`badge badge-${task.state}`}>{task.state}</span>
            </td>
            <td>{formatTime(task.updated_at)}</td>
            <td>{task.text}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
