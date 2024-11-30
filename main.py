import asyncio
import os
import motor.motor_asyncio
from motor.motor_asyncio import AsyncIOMotorCollection
import aiohttp
from dotenv import load_dotenv
from quart import Quart
from threading import Thread
from hypercorn.config import Config
from hypercorn.asyncio import serve

while_is_running = True


class ServerInfo:
    max_players: int = 0
    current_players: int = 0
    needed_players: int = 0
    queue: int = 0
    game_id: str = ""
    used_seeders: int = 0


async def get_serverinfo(
    server_name: str, empty_space: int, used_seeders: int
) -> ServerInfo:
    result = ServerInfo()
    result.used_seeders = used_seeders
    async with aiohttp.ClientSession() as session:
        url = "https://api.gametools.network/bf1/detailedserver/"
        retries = 0
        while retries <= 20:
            try:
                async with session.get(
                    url,
                    params={
                        "name": server_name,
                        "lang": "en-us",
                        "platform": "pc",
                    },
                ) as r:
                    new_serverdata: dict = await r.json()
                    result.max_players = new_serverdata.get("maxPlayerAmount", 0)
                    result.current_players = new_serverdata.get("playerAmount", 0)
                    # Leave space for non-bots to join
                    result.needed_players = new_serverdata.get("maxPlayerAmount", 0) - (
                        new_serverdata.get("playerAmount", 0) + empty_space
                    )
                    result.queue = new_serverdata.get("inQueue", 0)
                    result.game_id = new_serverdata.get("gameId", "")
                    return result
            except:
                retries += 1
            else:
                break
            await asyncio.sleep(10)
    return result


async def gather_seeders(mainDb, group_id: str) -> dict[str, dict[str, str | bool]]:
    seeders: dict[str, dict] = {}
    serverManagerDB = mainDb.get_database("serverManager")
    seeder_db: AsyncIOMotorCollection = serverManagerDB.get_collection("seeders")
    async for seeder in seeder_db.find({"groupId": group_id}):
        seeders[seeder["_id"]] = seeder
        # {'_id': '', 'groupId': '', 'isRunning': False, 'timeStamp': ""}
    return seeders


async def gather_seeding(mainDb, group_id: str) -> dict[str, dict[str, str]]:
    serverManagerDB = mainDb.get_database("serverManager")
    seeding_db: AsyncIOMotorCollection = serverManagerDB.get_collection("seeding")
    res = await seeding_db.find_one({"_id": group_id})
    return res.get("keepAliveSeeders", {})


async def update_seeding(
    mainDb, group_id: str, used_seeders: dict[str, dict[str, str]]
):
    serverManagerDB = mainDb.get_database("serverManager")
    seeding_db: AsyncIOMotorCollection = serverManagerDB.get_collection("seeding")
    await seeding_db.update_one(
        {"_id": group_id}, {"$set": {"keepAliveSeeders": used_seeders}}
    )


async def get_seeding_groups(mainDb):
    groups = []
    serverManagerDB = mainDb.get_database("serverManager")
    seeding_db: AsyncIOMotorCollection = serverManagerDB.get_collection("seeding")
    async for group in seeding_db.find({"fillServers": {"$exists": True}}):
        groups.append(group)
    return groups


async def update_server(mainDb, servers: list[str], group_id: str, empty_space: int):
    used_seeders = await gather_seeding(mainDb, group_id)
    server_infos: dict[str, ServerInfo] = {}

    # gather serverinfo with the amount of seeders
    for server_name in servers:
        server_used_seeders = 0
        for data in used_seeders.values():
            if data.get("serverName", "") == server_name:
                server_used_seeders += 1
        server_infos[server_name] = await get_serverinfo(
            server_name, empty_space, server_used_seeders
        )

    seeders = await gather_seeders(mainDb, group_id)

    # remove seeders that are no longer available and seeders that have a server_name not in the list
    for old_seeder, data in used_seeders.copy().items():
        if (
            data.get("serverName", "") not in servers
            or old_seeder not in seeders.keys()
        ):
            del used_seeders[old_seeder]
            continue

        # remove unneeded seeders
        if server_infos.get(data.get("serverName", "")) is not None:
            server: ServerInfo = server_infos[data.get("serverName", "")]
            if server.needed_players - server.used_seeders < 0:
                del used_seeders[old_seeder]
                server.used_seeders -= 1

    # add the unused seeders until list is empty
    unused_seeders = list(filter(lambda x: x not in used_seeders, seeders))
    for server_name, info in server_infos.items():
        while info.needed_players > 0 and len(unused_seeders) > 0:
            used_seeders[unused_seeders[0]] = {
                "gameId": info.game_id,
                "serverName": server_name,
            }
            info.needed_players -= 1
            del unused_seeders[0]

    await update_seeding(mainDb, group_id, used_seeders)


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
