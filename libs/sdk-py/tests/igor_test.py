import os
from openai import OpenAI
from actguard import BudgetGuard, BudgetExceededError

client = OpenAI(
    # This is the default and can be omitted
    api_key="REMOVED_OPENAI_KEY_2"
)

try:
    with BudgetGuard(user_id="u1", token_limit=100) as guard:
        response = client.responses.create(
            model="gpt-4o-mini",
            instructions="You are a coding assistant that talks like a pirate.",
            input="How do I check if a Python object is an instance of a class?",
        )
        print(response.output_text)
        print(guard.tokens_used)
        print(guard.usd_used)
except BudgetExceededError as e:
    print(e)