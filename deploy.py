import vertexai
from vertexai import agent_engines
from vertexai.agent_engines import AdkApp
from clinic_agent.agent import root_agent

vertexai.init(
    project="artifex-482515",
    location="us-central1",
    staging_bucket="gs://artifex-agent-staging",
)

app = AdkApp(
    agent=root_agent,
    enable_tracing=False,
)

print("Iniciando deploy... (5-10 minutos)")

remote_agent = agent_engines.create(
    agent_engine=app,
    requirements=[
        "google-cloud-aiplatform[adk,agent_engines]",
        "psycopg2-binary",
        "python-dotenv",
    ],
    extra_packages=["./clinic_agent"],
    display_name="Clinic Appointment Agent",
    env_vars={
        "DB_NAME": "artifex_db",
        "DB_USER": "artifex_user",
        "DB_PASSWORD": "artifex_pass_dev",
        "DB_HOST": "72.60.143.18",
        "DB_PORT": "5432",
        "GOOGLE_GENAI_USE_VERTEXAI": "TRUE",
        "MODEL": "gemini-1.5-flash",
    }
)

print(f"✓ Deploy concluído!")
print(f"Resource name: {remote_agent.resource_name}")
