# main_orchestrator.py (Final Version)

from typing import List, TypedDict, Annotated
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage
import operator
from dotenv import load_dotenv

from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI
from langchain_core.tools import StructuredTool
from langchain.pydantic_v1 import BaseModel, Field

# Import all our tool creation and logic functions
from query_agent_with_rag_and_sql import (
    create_sql_tool, create_rag_tool, create_notification_tool,
    create_results_tool, create_analytics_tool, create_timetable_tool
)
from generate_timetable_pdf import create_timetable_pdf
from scheduler import generate_schedule_logic

# --- CONFIGURATION ---
load_dotenv()
llm = ChatOpenAI(model_name="gpt-4o", temperature=0)

# --- 1. INSTANTIATE ALL TOOLS ---
sql_tool = create_sql_tool()
policy_tool = create_rag_tool()
notification_tool = create_notification_tool()
results_tool = create_results_tool()
analytics_tool = create_analytics_tool()
timetable_tool = create_timetable_tool()

# The PDF tool needs a Pydantic model for its arguments to work correctly in the graph
class PdfInput(BaseModel):
    semester_name: str = Field(description="The name of the semester, e.g., 'Fall 2025'.")
    department_name: str = Field(description="The name of the department, e.g., 'Computer Science'.")

pdf_tool = StructuredTool.from_function(
    func=create_timetable_pdf,
    name="timetable_pdf_generator",
    description="Generates a formatted PDF of the class schedule.",
    args_schema=PdfInput
)

all_tools = [sql_tool, policy_tool, notification_tool, results_tool, analytics_tool, timetable_tool, pdf_tool]

# --- 2. BIND TOOLS TO THE LLM ---
# This tells the LLM what functions it can call.
llm_with_tools = llm.bind_tools(all_tools)

# --- 3. DEFINE THE STATE ---
class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]

# --- 4. DEFINE THE NODES ---

# The primary agent node. It calls the LLM to decide on an action.
def agent_node(state: AgentState):
    print("---AGENT NODE---")
    # ADD THIS LINE TO SEE THE AGENT'S THOUGHTS
    print(f"Messages sent to LLM: {state['messages']}")
    response = llm_with_tools.invoke(state['messages'])
    return {"messages": [response]}

# The node that executes any chosen tool. We use the pre-built ToolNode.
tool_node = ToolNode(all_tools)

# --- 5. DEFINE THE ROUTER (CONDITIONAL EDGE) ---
# This function decides whether to continue using tools or to finish.
def should_continue(state: AgentState):
    last_message = state['messages'][-1]
    # If the last message is an AIMessage with tool_calls, we continue to the tool node.
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "continue"
    # Otherwise, we finish.
    else:
        return "end"

# --- 6. ASSEMBLE THE GRAPH ---
workflow = StateGraph(AgentState)

workflow.add_node("agent", agent_node)
workflow.add_node("action", tool_node)

# The entry point is the agent node.
workflow.set_entry_point("agent")

# Define the conditional routing
workflow.add_conditional_edges(
    "agent",
    should_continue,
    {"continue": "action", "end": END}
)

# After a tool is executed, the flow loops back to the agent node to let it
# process the tool's result.
workflow.add_edge("action", "agent")

# Compile the graph
app = workflow.compile()
print("Orchestrator is ready.")

# --- 7. RUN THE ORCHESTRATOR ---
if __name__ == "__main__":
    print("Starting a new conversation. Type 'exit' to quit.")
    while True:
        user_input = input("You: ")
        if user_input.lower() == 'exit':
            break

        events = app.stream({
            "messages": [HumanMessage(content=user_input)]
        })

        print("\n---AGENT RESPONSE---")
        final_response = None
        for event in events:
            if "agent" in event:
                # Look for the final AIMessage that does NOT have tool_calls
                msg = event["agent"]["messages"][-1]
                if not (hasattr(msg, "tool_calls") and msg.tool_calls):
                    final_response = msg
        
        if final_response:
            # --- THIS IS THE FIX ---
            # Get the raw content from the agent
            raw_content = final_response.content
            
            # Clean the content by splitting it at the metadata part
            clean_content = raw_content.split(', additional_kwargs=')[0]
            
            # If the content started with a quote, remove it
            if clean_content.startswith("'"):
                clean_content = clean_content[1:]

            print(clean_content)

        print("---------------------\n")