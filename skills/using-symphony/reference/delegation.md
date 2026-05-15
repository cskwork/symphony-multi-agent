# Delegating sub-tasks to Symphony

When the user gives a large task that decomposes into N independent
sub-tasks, you (the main conversational agent) can offload each sub-task
to Symphony rather than doing them inline. Each spawned worker runs in a
**fresh LLM session** with its own context window — so the calling agent's
context only carries the orchestration overhead, not all sub-implementations.

## Recipe

1. **Decompose** the user's request into independent tickets, each with a
   self-contained spec. Independence is critical: Symphony runs eligible
   tickets concurrently up to `agent.max_concurrent_agents`. Use `blocked_by`
   for real dependencies, and use ticket IDs to express FIFO order among
   otherwise eligible tickets.

2. **Register** each as a Symphony ticket with a rich description (this
   description is the only context the worker gets, plus the WORKFLOW.md
   prompt template):

   ```bash
   symphony board new TASK-001 "<title>" \
     --priority 2 \
     --description "<full spec + acceptance criteria + file pointers>"
   ```

   Number tickets in the same order you created the task list. Do not let a
   later task receive a lower suffix, because the dispatcher treats
   `TASK-001` as earlier work than `TASK-002` regardless of priority.

3. **Launch headless** (TUI requires a TTY you don't have):

   ```bash
   symphony ./WORKFLOW.md --port 9999 2>> log/symphony.log &
   ```

4. **Poll for completion** at sensible intervals (don't tight-loop):

   ```bash
   curl -s http://127.0.0.1:9999/api/v1/state \
     | jq '.counts, .running[].issue_identifier'
   symphony board ls --state Done
   ```

5. **Collect results** by reading the `## Resolution` section of each
   completed ticket file:

   ```bash
   symphony board show TASK-A
   ```

## When this pattern wins

- Large, parallelizable work (N independent features / fixes / migrations).
- Each sub-task fits in a worker's context — the orchestrator can't help
  if a sub-task itself is too big.
- The user is happy to wait for a polling cycle rather than streaming
  output.

## When this pattern *doesn't* win

- Sub-tasks have ordering dependencies you cannot encode with `blocked_by`.
- The user expects real-time visibility into each sub-agent's reasoning
  (Symphony exposes only event-level logs, not the agent's stream).
- There is no callback / push notification — the calling agent must poll.

## Distinction from in-session TodoWrite

| Aspect       | Claude Code TodoWrite                  | Symphony delegation                       |
|--------------|----------------------------------------|-------------------------------------------|
| Execution    | same agent, same session               | N separate subprocesses, fresh sessions   |
| Context      | within calling agent's context         | each worker has its own context           |
| Sync         | synchronous, inline                    | asynchronous, polling required            |
| Best for     | steps within one conversation          | independent work units, large fan-out     |

The two compose: one Symphony worker can use TodoWrite internally to
track the sub-steps of *its* sub-task.

## Quality of decomposition matters more than mechanism

Symphony will faithfully run whatever you put in front of it. The hard
part is human-side decomposition:

- Each ticket's `description` should read like a self-contained spec —
  acceptance criteria, file pointers, test commands. The worker has no
  conversation history.
- Avoid sub-tasks that require shared in-memory state. If two tickets
  must agree on a schema, write the schema down in a third ticket (or in
  a file the workspace will pick up via `after_create`) before launching
  them.
- Time-box experiments: start with `agent.max_turns: 5` and `agent.max_concurrent_agents: 2`
  while validating the decomposition. Crank up only after seeing a clean
  run.
