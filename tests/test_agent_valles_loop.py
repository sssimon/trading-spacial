import asyncio


def test_refusal_event_serializes():
    from api.agent.loop import Refusal
    from api.agent.streaming import sse_serialize

    async def gen():
        yield Refusal(user_message="no decido, te leo hechos")

    async def collect():
        out = []
        async for frame in sse_serialize(gen(), keepalive_seconds=999):
            out.append(frame.decode())
        return out

    frames = asyncio.run(collect())
    assert any('"type": "refusal"' in f and "no decido" in f for f in frames)
