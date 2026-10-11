Step-by-Step Deploy Runbook for Agent Engine
1. Directory Structure
Keep your project folder clean with just the agent code and dependencies:

text
Copy
cme-recon/
├── agent.py            # Contains root_agent = LlmAgent(...)
└── requirements.txt    # Production dependencies
2. requirements.txt
text
Copy
google-adk>=0.1.0
google-genai>=1.0.0
google-cloud-aiplatform>=1.75.0
pypdf
3. Deploy via the ADK CLI (Single Command)
Authenticate your GCP environment and run adk deploy agent_engine:

bash
Copy
# 1. Set your GCP environment
export GOOGLE_CLOUD_PROJECT="283243243997"
export GOOGLE_CLOUD_LOCATION="us-central1"

# 2. Deploy directly to Vertex AI Agent Engine
adk deploy agent_engine \
  --project=$GOOGLE_CLOUD_PROJECT \
  --region=$GOOGLE_CLOUD_LOCATION \
  --display_name="Commodity Reconciliation Agent" \
  .
(If prompted for a staging Cloud Storage bucket, pass --staging_bucket=gs://<YOUR_BUCKET_NAME>).

What Happens Under the Hood:
ADK packages root_agent into a managed runtime payload.
Google provisions the execution environment and deploys it to the Vertex AI Agent Engine.
The terminal outputs your unique Reasoning Engine Resource Name:
text
Copy
AgentEngine created.
Resource name: projects/283243243997/locations/us-central1/reasoningEngines/751619551677906944
4. Grant IAM Permissions to the Agent Engine Service Identity
When running in Agent Engine, Google provisions a managed Service Agent for your project. Ensure it has permissions to execute Vertex AI and access required resources:

bash
Copy
PROJECT_NUMBER=$(gcloud projects describe $GOOGLE_CLOUD_PROJECT --format="value(projectNumber)")
SERVICE_ACCOUNT="service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"

# Grant Vertex AI user role
gcloud projects add-iam-policy-binding $GOOGLE_CLOUD_PROJECT \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/aiplatform.user"
5. Testing Your Deployed Agent
Option A: Google Cloud Console Playground (No Code)
Go to Google Cloud Console → Vertex AI → Agent Engine (or Reasoning Engines).
Click Commodity Reconciliation Agent.
Click the Playground tab to chat with your agent in real-time, view tool execution graphs, and inspect live stdout/logs.
Option B: Programmatic Python Query
python
Copy
import vertexai

vertexai.init(project="283243243997", location="us-central1")

# Load your deployed agent engine
agent_engine = vertexai.agent_engines.get(
    "projects/283243243997/locations/us-central1/reasoningEngines/751619551677906944"
)

# Multi-turn conversational session works out-of-the-box
session = agent_engine.create_session(user_id="ankit.virmani@virufy.org")

response = agent_engine.query(
    user_id="ankit.virmani@virufy.org",
    session_id=session.id,
    message="Reconcile today's spot trades using Drive folder 1tW3zXN0fTvVAioE6j6VMNAP0D3M2e7B8."
)

print(response)
