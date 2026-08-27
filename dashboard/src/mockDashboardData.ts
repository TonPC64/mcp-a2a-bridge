import type { Agent } from "./AgentList";
import type { TaskActivity } from "./TaskList";

const now = Math.floor(Date.now() / 1000);

export const mockAgents: Agent[] = [
  { name: "Release coordinator", configured_url: "https://release.example.test", reachable: true, description: "Coordinates staged releases and rollback checks.", version: "2.4.1", streaming: true, input_modes: ["text"], output_modes: ["text"], skills: [{ id: "release-plan", name: "Release planning", description: "", tags: [], examples: [] }] },
  { name: "Code reviewer", configured_url: "https://review.example.test", reachable: true, description: "Reviews changes for correctness and regressions.", version: "1.8.0", streaming: true, input_modes: ["text"], output_modes: ["text"], skills: [{ id: "review", name: "Code review", description: "", tags: [], examples: [] }] },
  { name: "Build runner", configured_url: "https://build.example.test", reachable: true, description: "Runs checks and reports build artifacts.", version: "3.1.2", streaming: false, input_modes: ["text"], output_modes: ["text"], skills: [] },
  { name: "Incident analyst", configured_url: "https://incident.example.test", reachable: false, description: "Investigates production signals.", error: "Last health check timed out", skills: [{ id: "triage", name: "Incident triage", description: "", tags: [], examples: [] }] },
];

const messages = [
  "Validated the deployment checklist and handed the release to the build runner for final verification, including the migration window, rollback owner, smoke-test evidence, and the final approver's decision.",
  "Reviewing the pull request for retry behavior, error handling, and API compatibility before approval.",
  "The integration suite is running against the staging configuration and will publish the result shortly.",
  "Waiting for an operator to confirm whether the maintenance window can begin.",
  "Captured diagnostics from the failing health check and attached the relevant request metadata.",
  "Generated a rollback plan with the affected services, owners, and verification steps.",
];
const states = ["completed", "working", "completed", "input_required", "failed", "completed"];
const kinds = ["send_message", "a2a_receive", "a2a_call"];

export const mockTasks: TaskActivity[] = Array.from({ length: 28 }, (_, index) => {
  const agent = mockAgents[index % mockAgents.length].name;
  const destination = mockAgents[(index + 1) % mockAgents.length].name;
  const offset = index * 137;
  return {
    id: `mock-task-${String(index + 1).padStart(3, "0")}-a2a-bridge`,
    agent,
    source: index % 3 === 0 ? "mcp-a2a-bridge" : agent,
    destination,
    kind: kinds[index % kinds.length],
    state: states[index % states.length],
    text: messages[index % messages.length],
    created_at: now - offset - 45,
    updated_at: now - offset,
  };
});
