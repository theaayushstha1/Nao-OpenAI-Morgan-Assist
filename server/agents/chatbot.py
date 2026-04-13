"""Chatbot specialist — Morgan State CS knowledge base RAG."""
from agents import Agent
from server import config
from server.tools.pinecone_search import pinecone_search
from server.tools.nao_actions import nod_head, change_eye_color

SYSTEM = (
    "You are a Morgan State University Computer Science department assistant on a "
    "NAO robot. For any factual question about the CS department, courses, faculty, "
    "or programs, call `pinecone_search` first and ground your answer in the "
    "returned passages. Keep replies under 3 sentences. Say 'I'm not sure' if "
    "search returns nothing useful."
)

chatbot_agent = Agent(
    name="chatbot",
    instructions=SYSTEM,
    model=config.CHATBOT_MODEL,
    tools=[pinecone_search, nod_head, change_eye_color],
)
