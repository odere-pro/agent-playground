"""The LangSmith dataset, example, and experiment routes of the fake (CH-12, issue 131).

The bodies are meant to follow the LangSmith REST API (`/api/v1`, the `langsmith` client's
`create_dataset`, `create_example`, `create_project` with `reference_dataset_id`, and `create_run`):

- `POST /api/v1/datasets` (`name`, `description`): 409 when the name is taken.
- `GET /api/v1/datasets?name=`: the datasets with that name, a list.
- `POST /api/v1/examples` (`dataset_id`, `inputs`, `outputs`, `metadata`, optional `id`).
- `POST /api/v1/sessions` (`name`, `reference_dataset_id`, `extra.metadata`): an experiment is a
  session (tracer project) linked to a dataset.
- `POST /api/v1/runs` (`id`, `name`, `run_type`, `inputs`, `outputs`, `session_id`,
  `reference_example_id`, `extra.metadata`): one row of an experiment; the answer is 202.

suggested: these paths and field names are written from memory of the `langsmith` client, not
checked against a real LangSmith yet.

An unknown dataset, session, or example is 404; a missing or null field is 422 (an empty value
such as `inputs: {}` is accepted). A second example with the same `id` is 409.
Only what a CH-12 test needs: no update, no delete, no paging.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

__all__ = ["Dataset", "DatasetStore", "Example", "Experiment", "ExperimentRun", "dataset_routes"]


@dataclass(frozen=True)
class Dataset:
    id: str
    name: str
    description: str


@dataclass(frozen=True)
class Example:
    id: str
    dataset_id: str
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class Experiment:
    id: str
    name: str
    reference_dataset_id: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ExperimentRun:
    id: str
    session_id: str
    reference_example_id: str
    name: str
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    metadata: dict[str, Any]


@dataclass(kw_only=True)
class DatasetStore:
    """Datasets, examples, experiments, and their runs, each in arrival order."""

    datasets: list[Dataset] = field(default_factory=list)
    examples: list[Example] = field(default_factory=list)
    experiments: list[Experiment] = field(default_factory=list)
    runs: list[ExperimentRun] = field(default_factory=list)

    def dataset_by_name(self, name: str) -> Dataset | None:
        return next((d for d in self.datasets if d.name == name), None)

    def examples_of(self, dataset_id: str) -> list[Example]:
        return [e for e in self.examples if e.dataset_id == dataset_id]

    def experiments_of(self, dataset_id: str) -> list[Experiment]:
        return [x for x in self.experiments if x.reference_dataset_id == dataset_id]

    def runs_of(self, session_id: str) -> list[ExperimentRun]:
        return [r for r in self.runs if r.session_id == session_id]


def _error(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


async def _body(request: Request, required: tuple[str, ...]) -> dict[str, Any] | None:
    """The JSON object with every `required` key present and not null, or None.

    Presence, not truth: an example whose `inputs` is `{}` is a real example.
    """
    try:
        data = await request.json()
    except ValueError:
        return None
    if not isinstance(data, dict) or any(data.get(k) is None for k in required):
        return None
    return data


def _metadata(data: dict[str, Any]) -> dict[str, Any]:
    extra = data.get("extra") or {}
    meta = extra.get("metadata") if isinstance(extra, dict) else None
    return dict(meta) if isinstance(meta, dict) else {}


Endpoint = Callable[[Request], Awaitable[Response]]


def dataset_routes(store: DatasetStore, api_key: str) -> list[Route]:
    """The routes over `store`. A missing or wrong `x-api-key` is 401 and stores nothing."""

    def keyed(endpoint: Endpoint) -> Endpoint:
        async def checked(request: Request) -> Response:
            if request.headers.get("x-api-key") != api_key:
                return _error(401, "Unauthorized")
            return await endpoint(request)

        return checked

    async def create_dataset(request: Request) -> Response:
        data = await _body(request, ("name",))
        if data is None:
            return _error(422, "a dataset needs a name")
        if store.dataset_by_name(data["name"]) is not None:
            return _error(409, "Dataset already exists")
        dataset = Dataset(str(uuid.uuid4()), data["name"], str(data.get("description") or ""))
        store.datasets.append(dataset)
        return JSONResponse(vars(dataset))

    async def list_datasets(request: Request) -> Response:
        name = request.query_params.get("name")
        found = [d for d in store.datasets if name is None or d.name == name]
        return JSONResponse([vars(d) for d in found])

    async def create_example(request: Request) -> Response:
        data = await _body(request, ("dataset_id", "inputs"))
        if data is None:
            return _error(422, "an example needs dataset_id and inputs")
        if not any(d.id == data["dataset_id"] for d in store.datasets):
            return _error(404, "Dataset not found")
        example_id = str(data.get("id") or uuid.uuid4())
        if any(e.id == example_id for e in store.examples):
            return _error(409, "Example already exists")
        example = Example(
            example_id,
            data["dataset_id"],
            dict(data["inputs"]),
            dict(data.get("outputs") or {}),
            dict(data.get("metadata") or {}),
        )
        store.examples.append(example)
        return JSONResponse(vars(example))

    async def create_session(request: Request) -> Response:
        data = await _body(request, ("name", "reference_dataset_id"))
        if data is None:
            return _error(422, "an experiment needs name and reference_dataset_id")
        if not any(d.id == data["reference_dataset_id"] for d in store.datasets):
            return _error(404, "Dataset not found")
        if any(x.name == data["name"] for x in store.experiments):
            return _error(409, "Session already exists")
        experiment = Experiment(
            str(uuid.uuid4()), data["name"], data["reference_dataset_id"], _metadata(data)
        )
        store.experiments.append(experiment)
        return JSONResponse(vars(experiment))

    async def create_run(request: Request) -> Response:
        data = await _body(request, ("id", "name", "session_id", "reference_example_id"))
        if data is None:
            return _error(422, "a run needs id, name, session_id, and reference_example_id")
        session = next((x for x in store.experiments if x.id == data["session_id"]), None)
        if session is None:
            return _error(404, "Session not found")
        example_ids = {e.id for e in store.examples_of(session.reference_dataset_id)}
        if data["reference_example_id"] not in example_ids:
            return _error(404, "Example not found in the experiment's dataset")
        store.runs.append(
            ExperimentRun(
                str(data["id"]),
                session.id,
                data["reference_example_id"],
                data["name"],
                dict(data.get("inputs") or {}),
                dict(data.get("outputs") or {}),
                _metadata(data),
            )
        )
        return JSONResponse({}, status_code=202)

    return [
        Route("/api/v1/datasets", keyed(create_dataset), methods=["POST"]),
        Route("/api/v1/datasets", keyed(list_datasets), methods=["GET"]),
        Route("/api/v1/examples", keyed(create_example), methods=["POST"]),
        Route("/api/v1/sessions", keyed(create_session), methods=["POST"]),
        Route("/api/v1/runs", keyed(create_run), methods=["POST"]),
    ]
