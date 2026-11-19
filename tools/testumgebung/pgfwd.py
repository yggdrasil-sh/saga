"""Testumgebung: TCP 127.0.0.1:<port> -> Unix-Socket von Postgres (im eigenen Netz-Namensraum ist der TCP-Port des
Hosts nicht erreichbar, der Socket unter /tmp schon). Aufruf: python pgfwd.py <port> <socket>"""
import asyncio, sys

PORT, SOCK = int(sys.argv[1]), sys.argv[2]


async def pipe(r, w):
    try:
        while True:
            b = await r.read(65536)
            if not b:
                break
            w.write(b)
            await w.drain()
    except Exception:
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass


async def handle(r1, w1):
    try:
        r2, w2 = await asyncio.open_unix_connection(SOCK)
    except Exception:
        w1.close()
        return
    await asyncio.gather(pipe(r1, w2), pipe(r2, w1))


async def main():
    s = await asyncio.start_server(handle, "127.0.0.1", PORT)
    async with s:
        await s.serve_forever()

asyncio.run(main())
