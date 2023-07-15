import json
import logging
import os
import sys
from datetime import date
from typing import Dict, Optional

import pandas as pd
import uvicorn as uvicorn
from azure.cosmos import CosmosClient, DatabaseProxy
from azure.storage.blob import BlobServiceClient
from azure.storage.blob import ContainerClient as BlobContainerClient
# from dotenv import load_dotenv
from fastapi import FastAPI

from daily_updater.parliament.initiatives import votes
from src.app.apis import schemas
from src.elections.extract import extract_legislativas_2019

# load_dotenv(dotenv_path=".env")

logger = logging.getLogger(__name__)
handler = logging.StreamHandler(sys.stdout)
logger.addHandler(handler)


#########################
##### Load clients  #####
#########################


def get_database_client() -> DatabaseProxy:
    """
    Connects to Azure Cosmos DB and return its client. Expects to load from
    env vars all needed information, otherwise will fail.
    """

    try:
        database_name = os.environ["DATABASE_NAME"]
        url = os.environ["ACCOUNT_URI"]
        key = os.environ["ACCOUNT_KEY"]
    except Exception:
        logger.exception("Error collection env vars to access database:")
        raise

    try:
        client = CosmosClient(url, key)
        logger.debug("Connect to Cosmos successfully.")
        database = client.get_database_client(database_name)
        logger.info("Connect to Database successfully.")
    except Exception:
        logger.exception("Unknow error connectiong to database:")
        raise

    return database


def get_blob_container() -> BlobContainerClient:
    """
    Connects to Azure Blob Storage and return its client. Expects to load from
    env vars all needed information, otherwise will fail.
    """

    try:
        connection_string = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        container_name = os.environ["AZURE_STORAGE_CONTAINER"]
    except Exception:
        logger.exception("Error collection env vars to access blob storage:")
        raise

    try:
        blob_service_client = BlobServiceClient.from_connection_string(
            connection_string
        )
        container_client = blob_service_client.get_container_client(container_name)
    except Exception:
        logger.exception(
            f"Error connecting to blob storage container {container_name}:"
        )
        raise

    return container_client


# Get Blob Storage client
blob_storage_container_client = get_blob_container()

# Get Cosmos DB client
# database = get_database_client()


####################################
##### Load things into memory  #####
####################################


ALL_LEGISLATURES = ["XIV", "XV"]


def load_party_approvals(
    legislature: str, phase: str, container_client: BlobContainerClient
) -> pd.DataFrame:
    """
    Load party approvals for a full legislature from Blob Storage
    """

    data = container_client.get_blob_client(
        f"{legislature}_party_approvals_{phase}.json"
    )
    return pd.DataFrame.from_dict(
        json.loads(data.download_blob().readall()), orient="index"
    )


def load_party_correlations(
    legislature: str, phase: str, container_client: BlobContainerClient
) -> pd.DataFrame:
    """
    Load party correlations for a full legislature from Blob Storage
    """

    data = container_client.get_blob_client(
        f"{legislature}_party_correlations_{phase}.json"
    )
    return pd.DataFrame.from_dict(
        json.loads(data.download_blob().readall()), orient="index"
    )


def load_legislatures_fields(
    legislature: str, container_client: BlobContainerClient
) -> Dict:
    """
    Load initiative votes of a certain legislature from Blob Storage
    """

    data = container_client.get_blob_client(f"{legislature}_legislatures.json")

    return json.loads(data.download_blob().readall())


def load_initiative_votes(
    legislature: str, container_client: BlobContainerClient
) -> pd.DataFrame:
    """
    Load initiative votes of a certain legislature from Blob Storage
    """

    data = container_client.get_blob_client(f"{legislature}_initiatives_votes.json")
    df = pd.DataFrame.from_dict(
        json.loads(data.download_blob().readall()), orient="index"
    )

    df["iniciativa_evento_data"] = pd.to_datetime(
        df["iniciativa_evento_data"], unit="ms"
    )
    return df


party_approvals = None
party_correlations = None
initiative_votes = None
parties_legislativas_2019 = None
candidates_legislativas_2019 = None


def load_data():
    """
    Load all data into memory
    """
    # non thread safe, to be fixed when the api usage justify
    global party_approvals
    global party_correlations
    global initiative_votes
    global parties_legislativas_2019
    global candidates_legislativas_2019
    global legislature_fields

    # parliament data
    party_approvals = {
        legislature: {
            phase.value: load_party_approvals(
                legislature, phase.name.lower(), blob_storage_container_client
            )
            for phase in schemas.EventPhase
        }
        for legislature in ALL_LEGISLATURES
    }

    legislature_fields = {
        legislature: load_legislatures_fields(
            legislature=legislature, container_client=blob_storage_container_client
        )
        for legislature in ALL_LEGISLATURES
    }

    party_correlations = {
        legislature: {
            phase.value: load_party_correlations(
                legislature, phase.name.lower(), blob_storage_container_client
            )
            for phase in schemas.EventPhase
        }
        for legislature in ALL_LEGISLATURES
    }

    initiative_votes = {
        legislature: load_initiative_votes(legislature, blob_storage_container_client)
        for legislature in ALL_LEGISLATURES
    }

    # legislativas data
    (
        parties_legislativas_2019,
        candidates_legislativas_2019,
    ) = extract_legislativas_2019()


######################
##### Endpoints  #####
######################


# Create FastAPI client
tags_metadata = [
    {
        "name": "Parliament",
        "description": "Information from Portuguese Parliament API.",
    },
    {
        "name": "Elections",
        "description": "Information from Portuguese previous elections.",
    },
]

app = FastAPI(openapi_tags=tags_metadata)


@app.on_event("startup")
async def startup_event():
    load_data()


@app.get(
    "/parliament/party-approvals",
    response_model=schemas.PartyApprovalsOut,
    tags=["Parliament"],
)
def get_party_approvals(
    legislature: Optional[str] = "XV",
    event_phase: schemas.EventPhase = schemas.EventPhase.GENERALIDADE,
    type: Optional[str] = None,
    dt_ini: Optional[date] = None,
    dt_fin: Optional[date] = None,
):
    if dt_ini or dt_fin or type:
        data_initiatives_votes_ = initiative_votes[legislature]

        if event_phase != schemas.EventPhase.ALL:
            data_initiatives_votes_ = data_initiatives_votes_[
                data_initiatives_votes_["iniciativa_evento_fase"] == event_phase
            ]

        if dt_ini:
            data_initiatives_votes_ = data_initiatives_votes_[
                data_initiatives_votes_["iniciativa_evento_data"].dt.date >= dt_ini
            ]

        if dt_fin:
            data_initiatives_votes_ = data_initiatives_votes_[
                data_initiatives_votes_["iniciativa_evento_data"].dt.date <= dt_fin
            ]

        if type:
            data_initiatives_votes_ = data_initiatives_votes_[
                data_initiatives_votes_["iniciativa_tipo"] == type
            ]

        _party_approvals = votes.get_party_approvals(data_initiatives_votes_).to_json(
            orient="index"
        )
    else:
        _party_approvals = party_approvals[legislature][event_phase.value].to_json(
            orient="index"
        )

    # transform to the expected schema
    approvals = []
    for autor, value in json.loads(_party_approvals).items():
        data = {
            "id": autor.lower()
            .replace(" ", "-")
            .replace("cristina-rodrigues", "cr")
            .replace("joacine-katar-moreira", "jkm"),
            "nome": autor,
            "total_iniciativas": value["total_iniciativas"],
            "total_iniciativas_aprovadas": value["total_iniciativas_aprovadas"],
        }

        data["aprovacoes"] = {
            k.replace("iniciativa_votacao_", ""): v
            for k, v in value.items()
            if k.startswith("iniciativa_votacao_")
        }

        approvals.append(data)

    return {"autores": approvals}


@app.get(
    "/parliament/party-correlations",
    response_model=schemas.PartyCorrelationsOut,
    tags=["Parliament"],
)
def get_party_correlations(
    legislature: Optional[str] = "XV",
    event_phase: schemas.EventPhase = schemas.EventPhase.GENERALIDADE,
    type: Optional[str] = None,
    dt_ini: Optional[date] = None,
    dt_fin: Optional[date] = None,
):
    if dt_ini or dt_fin or type:
        data_initiatives_votes_ = initiative_votes[legislature]

        if event_phase != schemas.EventPhase.ALL:
            data_initiatives_votes_ = data_initiatives_votes_[
                data_initiatives_votes_["iniciativa_evento_fase"] == event_phase
            ]

        if dt_ini:
            data_initiatives_votes_ = data_initiatives_votes_[
                data_initiatives_votes_["iniciativa_evento_data"].dt.date >= dt_ini
            ]

        if dt_fin:
            data_initiatives_votes_ = data_initiatives_votes_[
                data_initiatives_votes_["iniciativa_evento_data"].dt.date <= dt_fin
            ]

        if type:
            data_initiatives_votes_ = data_initiatives_votes_[
                data_initiatives_votes_["iniciativa_tipo"] == type
            ]

        _party_corr = votes.get_party_correlations(data_initiatives_votes_).to_json(
            orient="index"
        )
    else:
        _party_corr = party_correlations[legislature][event_phase.value].to_json(
            orient="index"
        )

    # transform to the expected schema
    res = []
    for _, corr in json.loads(_party_corr).items():
        res.append(
            {
                "nome": corr.pop("nome").replace("iniciativa_votacao_", ""),
                "correlacoes": {
                    k.replace("iniciativa_votacao_", ""): v for k, v in corr.items()
                },
            }
        )

    return {"partido": res}


@app.get("/parliament/legislatures", tags=["Parliament"])
def get_legislatures(
    legislature: Optional[str] = "XV",
):
    return legislature_fields[legislature]


@app.get("/parliament/initiatives", tags=["Parliament"])
def get_initiatives(
    legislature: Optional[str] = "XV",
    event_phase: schemas.EventPhase = schemas.EventPhase.GENERALIDADE,
    name_filter: Optional[str] = None,
    party: Optional[str] = None,
    deputy: Optional[str] = None,
    dt_ini: Optional[date] = None,
    dt_fin: Optional[date] = None,
    limit: Optional[int] = 20,
    offset: Optional[int] = 0,
):
    data_initiatives_votes_ = initiative_votes[legislature]

    if event_phase != schemas.EventPhase.ALL:
        data_initiatives_votes_ = data_initiatives_votes_[
            data_initiatives_votes_["iniciativa_evento_fase"] == event_phase
        ]

    if dt_ini:
        data_initiatives_votes_ = data_initiatives_votes_[
            data_initiatives_votes_["iniciativa_evento_data"].dt.date >= dt_ini
        ]

    if dt_fin:
        data_initiatives_votes_ = data_initiatives_votes_[
            data_initiatives_votes_["iniciativa_evento_data"].dt.date <= dt_fin
        ]

    if name_filter:
        data_initiatives_votes_ = data_initiatives_votes_[
            data_initiatives_votes_["iniciativa_titulo"]
            .str.lower()
            .str.contains(name_filter.lower())
        ]

    if party:
        data_initiatives_votes_ = data_initiatives_votes_[
            data_initiatives_votes_["iniciativa_autor"].str.lower() == party.lower()
        ]

    if deputy:
        data_initiatives_votes_ = data_initiatives_votes_[
            data_initiatives_votes_["iniciativa_autor_deputado"]
            .str.lower()
            .str.contains(deputy.lower())
        ]

    initiatives = (
        votes.get_initiatives(data_initiatives_votes_)
        .sort_values("iniciativa_data")
        .head(limit + offset)
        .tail(limit)
        .to_json(orient="index")
    )

    # transform to the expected schema
    res = []
    for _, initiative in json.loads(initiatives).items():
        res.append(initiative)

    return {"initiativas": res}


@app.get("/elections/parties", tags=["Elections"])
def get_elections_parties(
    type: Optional[str] = "Legislativas", year: Optional[int] = 2019
):
    # ignoring input parameters until we have other elections

    return {"parties": json.loads(parties_legislativas_2019.to_json(orient="index"))}


@app.get("/elections/candidates", tags=["Elections"])
def get_party_candidates(
    party: Optional[str],
    type: Optional[str] = "Legislativas",
    year: Optional[int] = 2019,
):
    # ignoring some input parameters until we have other elections

    candidates_legislativas_2019_ = candidates_legislativas_2019[
        candidates_legislativas_2019["party"] == party
    ]

    return {
        "candidates": json.loads(
            candidates_legislativas_2019_.to_json(orient="records")
        )
    }


@app.get("/elections/candidates-district", tags=["Elections"])
def get_district_candidates(
    district: str,
    type: Optional[str] = "Legislativas",
    year: Optional[int] = 2019,
    party: Optional[str] = None,
):
    # ignoring some input parameters until we have other elections

    candidates_legislativas_2019_ = candidates_legislativas_2019

    if party:
        candidates_legislativas_2019_ = candidates_legislativas_2019_[
            candidates_legislativas_2019_["party"] == party
        ]

    candidates_legislativas_2019_ = candidates_legislativas_2019_[
        candidates_legislativas_2019_["district"] == district
    ]

    return {
        "candidates": json.loads(
            candidates_legislativas_2019_.to_json(orient="records")
        )
    }


@app.get("/update")
def update():
    logger.info("Loading new data..")
    load_data()
    logger.info("New data loaded.")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
