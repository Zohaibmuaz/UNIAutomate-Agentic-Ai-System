# api_server.py (Final Corrected Version)

from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# Import your LangGraph app from the orchestrator file
from main_orchestrator import app as langgraph_app
from langchain_core.messages import HumanMessage

api = FastAPI()
templates = Jinja2Templates(directory="templates")

@api.get("/", response_class=HTMLResponse)
async def get_chat_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@api.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            
            # --- THIS IS THE CORRECTED LOGIC ---
            async for event in langgraph_app.astream({"messages": [HumanMessage(content=data)]}):
                # We iterate through the events as they happen
                for key, value in event.items():
                    if key == "agent":
                        # If the event is from the agent, check if it's the final answer
                        msg = value["messages"][-1]
                        if not (hasattr(msg, "tool_calls") and msg.tool_calls):
                            # It's the final answer! Clean it up.
                            raw_content = msg.content
                            clean_content = raw_content.split(', additional_kwargs=')[0]
                            if clean_content.startswith("'"):
                                clean_content = clean_content[1:]
                            # Send the clean answer to the UI
                            await websocket.send_json({"type": "final_answer", "data": clean_content})
                    else:
                        # For intermediate steps (like tool calls), send them for "Thinking..." state
                        await websocket.send_json({"type": key, "data": str(value)})
            
            # Send a final "done" message to let the UI know the process is complete
            await websocket.send_json({"type": "done", "data": "Workflow complete."})

    except Exception as e:
        print(f"WebSocket Error: {e}")
    finally:
        await websocket.close()
# To run this server, use the command in your terminal:
# uvicorn api_server:api --reload