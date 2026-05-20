import asyncio
from main import addr_to_coords, estimate_travel
async def test():
    o = await addr_to_coords("천호역")
    print("o:", o)
    d = await addr_to_coords("강남역")
    print("d:", d)
    res = await estimate_travel("천호역", "강남역", "transit")
    print("res:", res)
asyncio.run(test())
