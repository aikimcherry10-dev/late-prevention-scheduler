import asyncio
import sys
from main import get_odsay_transit

async def test():
    print("starting test", file=sys.stdout)
    res = await get_odsay_transit(37.53595, 127.12271, 37.50023, 127.0268)
    print("res is None?", res is None, file=sys.stdout)
    if res:
        print("res len:", len(res), file=sys.stdout)

if __name__ == "__main__":
    asyncio.run(test())
