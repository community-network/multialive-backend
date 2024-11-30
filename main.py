import asyncio
import os
import motor.motor_asyncio
from dotenv import load_dotenv
from quart import Quart
from threading import Thread
from hypercorn.config import Config
from hypercorn.asyncio import serve
from multialive.multialive import get_seeding_groups, update_server


while_is_running = True


async def main(mainDb):
    while True:
        groups = await get_seeding_groups(mainDb)
        for group in groups:
            await update_server(
                mainDb,
                group.get("fillServers", []),
                group.get("_id", ""),
                group.get("emptySpace", 10),
            )
        await asyncio.sleep(30)


def startBackgroundGetter():
    load_dotenv()
    mainDb = motor.motor_asyncio.AsyncIOMotorClient(
        os.getenv("MONGO_DETAILS_STRING", None)
    )
    asyncio.run(main(mainDb))
    global while_is_running
    while_is_running = False


if __name__ == "__main__":
    app = Quart(__name__)

    @app.before_serving
    async def run_on_start():
        thread = Thread(target=startBackgroundGetter)
        thread.start()

    @app.route("/")
    def health_check():
        global while_is_running
        if while_is_running:
            return {"status": "ok"}
        else:
            return {"status": "failed"}, 500

    if __name__ == "__main__":
        config = Config.from_mapping(
            bind="0.0.0.0:80",
            statsd_host="0.0.0.0:80",
        )
        asyncio.run(serve(app, config))
