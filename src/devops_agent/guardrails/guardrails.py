from guardrails import Guard, OnFailAction
from guardrails.hub import DetectJailbreak, RestrictToTopic, LlamaGuard7B
from openai import OpenAI


def make_llm_request(prompt: str) -> str:
    client = OpenAI(api_key="EMPTY", base_url="http://localhost:8000/v1")

    messages = [
        {"role": "developer", "content": "You are a helpful assistant."},
        {"role": "user", "content": prompt},
    ]

    chat_response = client.chat.completions.create(
        model="",
        messages=messages,
        max_completion_tokens=1000,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    content = chat_response.choices[0].message.content.strip()

    jailbreak_guardian = Guard().use(
        DetectJailbreak,
        on_fail=OnFailAction.EXCEPTION,
    )

    topic_guard = Guard().use(
        RestrictToTopic,
        valid_topics=[
            "fishing",
            "fish",
            "aquatic life",
            "fishing equipment",
            "fishing techniques",
        ],
        invalid_topics=["politics", "violence", "drugs", "hacking", "weapons"],
        disable_classifier=True,
        disable_llm=False,
        on_fail="exception",
    )

    guard = Guard().use(LlamaGuard7B, on_fail=OnFailAction.EXCEPTION)
    guards = [jailbreak_guardian, topic_guard, guard]
    try:
        for guard in guards:
            guard.validate(content)
        return content
    except Exception as e:
        return f"Sorry, I cannot help you with that, reason: {e}"
