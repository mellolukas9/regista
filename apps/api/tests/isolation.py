"""Automatic discovery of every route of the app, for the isolation and permission sweeps.

Nothing here lists routes by hand. A route that the sweep cannot exercise safely makes
`discover` raise `Uncovered`, so a new route cannot ship without coverage:

- exactly one route marker (PublicRoute / RequireStage / SelfService / Require / MachineRoute);
- agent routes: everything under `/agent/` is a MachineRoute, except the closed list of public
  agent paths (enroll, challenge, token), which are PublicRoute; MachineRoute never appears
  outside `/agent/`, so a machine credential cannot reach a user route or the other way round;
- every path parameter has an entry in the registry of resources owned by the other client;
- the request body model carries `examples` (the sweep sends the first one);
- no required query parameters (the sweep cannot invent them);
- no mounted sub-apps or raw routes other than FastAPI's documentation routes.
"""

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from pydantic import BaseModel

from regista_api.auth.deps import PublicRoute, RouteMarker
from regista_api.auth.machine import MachineRoute

DOC_PATHS = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})
AGENT_PREFIX = "/agent/"
# The only agent routes that take no access token: they are how an agent gets one.
AGENT_PUBLIC_PATHS = frozenset({"/agent/enroll", "/agent/challenge", "/agent/token"})
IGNORED_METHODS = frozenset({"HEAD", "OPTIONS"})
SAFE_METHODS = frozenset({"GET"})


class Uncovered(AssertionError):
    """The app has a route the sweeps do not know how to exercise."""


@dataclass(frozen=True)
class RouteSpec:
    method: str
    path: str
    marker: RouteMarker
    path_params: tuple[str, ...]
    body_model: type[BaseModel] | None

    @property
    def mutates(self) -> bool:
        return self.method not in SAFE_METHODS

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"

    def url(self, resources: dict[str, str]) -> str:
        return re.sub(r"\{(\w+)(?::\w+)?\}", lambda m: resources[m.group(1)], self.path)

    def body(self) -> dict[str, Any] | None:
        if self.body_model is None:
            return None
        return dict(self.body_model.model_json_schema()["examples"][0])


@dataclass(frozen=True)
class _Endpoint:
    path: str
    methods: frozenset[str]
    dependant: Dependant


def _flatten(routes: Iterable[Any]) -> Iterator[_Endpoint]:
    """Every endpoint of the app, whichever way it was registered.

    FastAPI (0.14x) keeps `include_router` results as lazy objects. Their internals are private,
    so this fails closed: a route type it does not understand raises `Uncovered`, instead of
    silently dropping out of the sweep after a FastAPI upgrade.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield _Endpoint(route.path, frozenset(route.methods or ()), route.dependant)
        elif hasattr(route, "effective_candidates"):  # lazy included router
            yield from _flatten(route.effective_candidates())
            yield from _flatten(route.effective_low_priority_routes())
        elif getattr(route, "dependant", None) is not None and hasattr(route, "original_route"):
            yield _Endpoint(route.path, frozenset(route.methods), route.dependant)
        elif getattr(route, "path", None) in DOC_PATHS:
            continue
        else:
            raise Uncovered(f"{route!r} is not an APIRoute: the sweeps cannot cover it")


def _markers(dependant: Dependant) -> list[RouteMarker]:
    found: list[RouteMarker] = []
    for dep in dependant.dependencies:
        if isinstance(dep.call, RouteMarker):
            found.append(dep.call)
        found.extend(_markers(dep))
    return found


def _check_agent_boundary(path: str, marker: RouteMarker) -> None:
    if path in AGENT_PUBLIC_PATHS:
        if not isinstance(marker, PublicRoute):
            raise Uncovered(f"{path}: this agent path takes no token and must be a PublicRoute")
    elif path.startswith(AGENT_PREFIX):
        if not isinstance(marker, MachineRoute):
            raise Uncovered(
                f"{path}: routes under {AGENT_PREFIX} must be a MachineRoute "
                f"(or be one of {sorted(AGENT_PUBLIC_PATHS)})"
            )
    elif isinstance(marker, MachineRoute):
        raise Uncovered(f"{path}: MachineRoute is only allowed under {AGENT_PREFIX}")


def discover(app: FastAPI, resource_params: set[str]) -> list[RouteSpec]:
    specs: list[RouteSpec] = []
    for route in _flatten(app.routes):
        markers = _markers(route.dependant)
        if len(markers) != 1:
            raise Uncovered(
                f"{route.path}: needs exactly one route marker "
                "(PublicRoute, RequireStage, SelfService, Require or MachineRoute); "
                f"found {len(markers)}"
            )
        _check_agent_boundary(route.path, markers[0])

        params = tuple(p.name for p in route.dependant.path_params)
        unknown = [p for p in params if p not in resource_params]
        if unknown:
            raise Uncovered(
                f"{route.path}: path parameter(s) {unknown} have no entry in the other "
                "client's resource registry"
            )
        required_query = [
            q.name for q in route.dependant.query_params if q.field_info.is_required()
        ]
        if required_query:
            raise Uncovered(f"{route.path}: required query parameter(s) {required_query}")

        body_model: type[BaseModel] | None = None
        bodies = route.dependant.body_params
        if len(bodies) > 1:
            raise Uncovered(f"{route.path}: more than one body parameter")
        if bodies:
            annotation = bodies[0].field_info.annotation
            if not (isinstance(annotation, type) and issubclass(annotation, BaseModel)):
                raise Uncovered(f"{route.path}: body is not a pydantic model")
            if not annotation.model_json_schema().get("examples"):
                raise Uncovered(
                    f"{route.path}: request model {annotation.__name__} has no `examples`"
                )
            body_model = annotation

        for method in sorted(route.methods - IGNORED_METHODS):
            specs.append(RouteSpec(method, route.path, markers[0], params, body_model))
    if not specs:
        raise Uncovered("no routes discovered")
    return specs
