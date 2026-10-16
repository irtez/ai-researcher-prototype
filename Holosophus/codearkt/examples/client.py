import fire  # type: ignore

from codearkt.client import query_agent
from codearkt.llm import ChatMessage

DEFAULT_QUERY = "Find an abstract of the PingPong paper by Ilya Gusev"


def main(query: str = DEFAULT_QUERY) -> None:
    messages = [ChatMessage(role="user", content=query)]
    for event in query_agent(messages):
        if event.content:
            print(event.content, end="", flush=True)


if __name__ == "__main__":
    fire.Fire(main)
