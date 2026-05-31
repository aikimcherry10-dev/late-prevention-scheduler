import asyncio
from main import addr_to_coords, estimate_travel

async def test():
    o = await addr_to_coords("강남역 3번 출구")
    d = await addr_to_coords("서울시청")
    print(f"Coords: {o} -> {d}")
    
    for mode in ["car", "transit", "walk", "bike"]:
        t, _, _, _, _, _, _ = await estimate_travel(o, d, mode)
        print(f"Mode: {mode}, Time: {t}")

asyncio.run(test())
