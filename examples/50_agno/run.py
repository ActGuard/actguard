from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.openai import OpenAIChat
from agno.os import AgentOS
from agno.tools.mcp import MCPTools

from actguard import Client
from actguard.integrations.agno import ActGuardMiddleware

agc = Client(
    api_key="ag_live_...",
    gateway_url="https://api.actguard.ai",
)

agno_assist = Agent(
    name="Agno Assist",
    model=OpenAIChat(id="gpt-4o-mini"),
    db=SqliteDb(db_file="agno.db"),
    tools=[MCPTools(url="https://docs.agno.com/mcp")],
    add_history_to_context=True,
    num_history_runs=3,
    markdown=True,
)

agent_os = AgentOS(agents=[agno_assist], tracing=True)
app = agent_os.get_app()

# Add actGuard middleware — wraps each request with budget tracking
app.add_middleware(
    ActGuardMiddleware,
    client=agc,
    usd_limit=0.5,
    default_user_id="agno_test_user",
    # Optional: custom handler for budget exceeded errors.
    # If omitted, a default 402 JSON response is returned.
    # on_budget_exceeded=my_custom_handler,
)

if __name__ == "__main__":
    agent_os.serve(app="run:app", reload=True)
