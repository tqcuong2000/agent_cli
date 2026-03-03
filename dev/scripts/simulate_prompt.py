import asyncio
import os
from pathlib import Path
from agent_cli.core.bootstrap import create_app, _build_tool_registry
from agent_cli.workspace.sandbox import SandboxWorkspaceManager
from agent_cli.workspace.strict import StrictWorkspaceManager

async def simulate_prompt():
    # 1. Setup minimal workspace for tool registry
    cwd = Path.cwd()
    strict = StrictWorkspaceManager(cwd)
    workspace = SandboxWorkspaceManager(strict)
    
    # 2. Build app context (this handles all registry wiring)
    app = create_app(root_folder=cwd)
    
    # 3. Simulate Prompt Building for the default agent
    agent_name = "default"
    agent = app.agent_registry.get(agent_name)
    if not agent:
        print(f"❌ Agent '{agent_name}' not found.")
        return

    # Use the actual build logic from the agent
    # We'll call the internal build_system_prompt but we need to pass a task context
    task_desc = "Implement a new feature to track user preferences in a local JSON file."
    
    # DefaultAgent.build_system_prompt builds the final string
    prompt = await agent.build_system_prompt(task_desc)
    
    # 6. Output to file for inspection
    output_path = Path("x:\\agent_cli\\debug_simulated_prompt.md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# Simulated System Prompt for Agent: " + agent_name + "\n\n")
        f.write("## Task Description\n")
        f.write(f"> {task_desc}\n\n")
        f.write("---\n\n")
        f.write(prompt)
    
    print(f"✅ Simulated prompt written to: {output_path}")

if __name__ == "__main__":
    asyncio.run(simulate_prompt())
