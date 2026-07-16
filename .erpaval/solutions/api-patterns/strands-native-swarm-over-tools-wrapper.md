---
title: Use native strands.multiagent.Swarm, not strands_tools.swarm, for programmatic teams
track: knowledge
category: api-patterns
module: packages/strands-teams/src/strands_teams/teams.py
component: strands-agents (>=1.47), strands-agents-tools
severity: warning
tags: [strands, swarm, multiagent, bedrock, invocation-state, tool-context]
applies_when:
  - Building a coordinator agent that dispatches sub-teams programmatically
  - Migrating off strands_tools.swarm.swarm
  - Needing per-node models, progress events, or shared state in a swarm
pattern: |
  strands_tools.swarm.swarm (the @tool wrapper) is sync-only, returns a lossy
  markdown blob instead of the SwarmResult, passes no hooks/entry_point/
  invocation_state to the Swarm, and — the real bug — sets per-agent
  model_provider/model_settings as plain attributes the SDK never reads, so
  every sub-agent silently runs on the DEFAULT BedrockModel/region.

  Construct native strands.multiagent.Swarm directly instead:
  - dispatch_team is `async def @tool(context=True)`; build fresh Agent nodes
    (duplicate instances and nodes with their own session_manager are rejected),
    each with an EXPLICIT BedrockModel, then `await swarm.invoke_async(task, invocation_state=...)`.
  - Shared per-team state (output dir, tool registry, timeouts, rubric) flows
    through invocation_state, read in tools via tool_context.invocation_state —
    this replaces module-level mutable globals / a configure() setter.
  - Preserve ping-pong detection explicitly: the SDK defaults
    repetitive_handoff_detection_window/min_unique_agents to 0/0, but the old
    wrapper used 8/3.
  - Swarm-level hooks (BeforeNodeCallEvent/AfterNodeCallEvent) see node
    transitions but NOT inner tool calls; for a per-tool TUI feed attach a hook
    to each node Agent or consume stream_async's multiagent_node_stream events.
  - BedrockModel: cache_config=CacheConfig(strategy="auto") (cache_prompt is
    deprecated); adaptive thinking / output_config effort / context-1m beta go
    through additional_request_fields unchanged. CacheConfig imports cleanly
    from strands.models.bedrock at runtime (pyright wrongly flags it; ty passes).
  - Structured output: Agent(structured_output_model=M) + result.structured_output;
    agent.structured_output() is deprecated.
example_files:
  - packages/strands-teams/src/strands_teams/teams.py
  - packages/strands-teams/src/strands_teams/coordinator.py
---

# Why this matters

The wrapper's silent default-model behavior means your carefully-configured
sub-agents don't actually use the model you think they do — a correctness bug
invisible until you inspect token/latency telemetry. Going native also unlocks
progress streaming and structured results the wrapper throws away.

# Example

```python
@tool(context=True)
async def dispatch_team(team_id: str, mandate: str, agents: list[dict],
                        tool_context: ToolContext) -> str:
    inv = tool_context.invocation_state
    registry, layout = inv["tool_registry"], inv["layout"]
    nodes = [Agent(model=BedrockModel(**inv["node_model_config"]),
                   name=a["name"], system_prompt=a["system_prompt"],
                   tools=_resolve(a["tools"], registry) + [make_write_file(layout)])
             for a in agents]
    swarm = Swarm(nodes=nodes, execution_timeout=inv["execution_timeout"],
                  node_timeout=inv["node_timeout"],
                  repetitive_handoff_detection_window=8,
                  repetitive_handoff_min_unique_agents=3)
    result = await swarm.invoke_async(mandate, invocation_state=inv)
```
