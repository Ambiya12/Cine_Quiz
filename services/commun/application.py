import os
from contextlib import asynccontextmanager, ExitStack

import pymongo
from fastapi import FastAPI, HTTPException, Request
from pymongo.errors import PyMongoError
from redis import Redis
from redis.exceptions import RedisError


def creer_application(service: str, *, utiliser_mongo: bool) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with ExitStack() as ressources:
            app.state.redis = ressources.enter_context(
                Redis.from_url(
                    os.environ["REDIS_URL"],
                    decode_responses=True,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                )
            )
            app.state.redis_prefix = os.environ["REDIS_PREFIX"]
            if utiliser_mongo:
                mongo = ressources.enter_context(
                    pymongo.MongoClient(os.environ["MONGO_URI"])
                )
                app.state.db = mongo[os.environ["MONGO_DB"]]
            yield

    app = FastAPI(title=f"CinéQuiz - {service}", lifespan=lifespan)

    @app.get("/health")
    def health(request: Request) -> dict[str, str]:
        try:
            request.app.state.redis.ping()
        except RedisError as erreur:
            raise HTTPException(503, detail="Redis indisponible") from erreur

        if utiliser_mongo:
            try:
                with pymongo.timeout(2):
                    hello = request.app.state.db.client.admin.command("hello")
            except PyMongoError as erreur:
                raise HTTPException(503, detail="MongoDB indisponible") from erreur
            if not (hello.get("setName") and hello.get("isWritablePrimary")):
                raise HTTPException(503, detail="Primaire du replica set indisponible")

        return {"service": service, "statut": "ok"}

    return app
