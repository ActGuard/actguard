import asyncio
import os
from openai import AsyncOpenAI
from actguard import BudgetGuard, BudgetExceededError

# 1. Use the Async Client
client = AsyncOpenAI(
    api_key="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"
)

async def main():
    try:
        # 2. The Context Manager works normally (it uses contextvars which are async-safe)
        with BudgetGuard(user_id="async_user_1", token_limit=100) as guard:
            
            print(f"--- Starting Request (Limit: {guard.token_limit} tokens) ---")

            # 3. Call the API with await and stream=True
            # ActGuard will automatically inject stream_options={"include_usage": True}
            stream = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a fast-talking auctioneer."},
                    {"role": "user", "content": "Sell me a vintage Python script."}
                ],
                stream=True,
            )

            print("--- Stream Output ---")
            
            # 4. Iterate over the stream asynchronously
            # ActGuard is intercepting chunks in the background here
            async for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        print(delta.content, end="", flush=True)

            print("\n\n--- Final Stats ---")
            # Usage is only available AFTER the stream finishes (in the last chunk)
            print(f"Tokens Used: {guard.tokens_used}")
            print(f"USD Used:    ${guard.usd_used:.6f}")

    except BudgetExceededError as e:
        print(f"\n\n!!! BLOCKED !!!")
        print(f"Budget exceeded limit of {e.token_limit}. Stopped at {e.tokens_used} tokens.")

if __name__ == "__main__":
    # 5. Run the async event loop
    asyncio.run(main())