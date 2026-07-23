"""
SDK mode example — direct litellm.completion() usage.

Before running:
  pip install moesif-litellm
  export OPENAI_API_KEY=sk-...
  export MOESIF_APPLICATION_ID=your-app-id
"""

import litellm
from moesif_litellm import MoesifLogger

litellm.callbacks = [MoesifLogger()]

response = litellm.completion(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
    user="alice",                          # user_id in Moesif
    metadata={"company_id": "acme-corp"},  # company_id in Moesif
)

print(response.choices[0].message.content)
