import json
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ⚠️ AJUSTE AQUI para o nome do arquivo onde está seu root_agent
from agent import root_agent  

app = FastAPI()


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


# =========================
# 🔹 Endpoint Normal (sem streaming)
# =========================
@app.post("/run")
async def chat(req: ChatRequest):
    try:
        response = await root_agent.run(
            input=req.message,
            session_id=req.session_id,
        )

        return {
            "status": "success",
            "response": response,
        }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
        }


# =========================
# 🔹 Endpoint com Streaming
# =========================
@app.post("/run_sse")
async def chat_stream(req: ChatRequest):

    async def event_generator():
        try:
            async for event in root_agent.stream(
                input=req.message,
                session_id=req.session_id,
            ):

                # 🔹 TOKEN GERADO
                if event.type == "token":
                    yield f"data: {json.dumps({'type': 'token', 'content': event.content})}\n\n"

                # 🔹 TOOL SENDO CHAMADA
                elif event.type == "tool_call":
                    yield f"data: {json.dumps({'type': 'tool_call', 'tool': event.tool_name})}\n\n"

                # 🔹 RESULTADO DA TOOL
                elif event.type == "tool_result":
                    yield f"data: {json.dumps({'type': 'tool_result'})}\n\n"

                # 🔹 FINALIZAÇÃO
                elif event.type == "final":
                    yield f"data: {json.dumps({'type': 'final'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )
