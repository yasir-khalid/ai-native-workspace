"""Data connectors exposed as MCP servers (PLAN_PROACTIVE.md F2/F3, §9).

Each connector is a standalone MCP server. PodOS's connector gateway (apps/api/podos_api/connectors.py)
is the only MCP client: it holds source credentials, polls or receives events, and agents only ever see
the data through pod-scoped capabilities.
"""
