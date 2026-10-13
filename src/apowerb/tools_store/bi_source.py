"""Allowlist of agent tools that a BI dashboard may read as a data source.

A dashboard source ``kind="connector"`` + ``source_type="tool"`` runs one tool
of the portfolio, as the dashboard owner, and uses its answer as a table::

    source_options = {"tool": "<exact tool function name>", "args": {...}}
    connection_config_id = "<tool_config id>"   # only for credentials="tool_config"

Contract for a tool to be readable
----------------------------------
1. It is **read-only** (no write, no send, no side effect) and takes JSON
   arguments (``args``) bound by keyword.
2. It is marked, either with the decorator (the attribute is what is checked)::

       @bi_source                                  # env-var credentials (default)
       @bi_source(credentials="integration")       # per-user integration row
       def tool_x_list_things(limit: int = 100) -> dict: ...

   or by ``register_bi_source(fn, credentials=...)`` from a module that cannot
   decorate (tests, extensions). An unmarked tool is never run, whatever its name.
3. It returns a table, in one of two shapes::

       [ {"col": value, ...}, ... ]                                  # homogeneous dicts
       {"rows": [ {...}, ... ], "columns": ["col", ...]}             # "columns" optional

   Anything else (text, a dict without ``rows``, a failure answer) is a source
   error. A failure is recognised from ``success: False``, or ``status`` =
   ``error``/``failed``, and its message is read from ``error``,
   ``error_message`` or ``message`` and shown to the dashboard author.
   Nested values (lists, dicts) are flattened to JSON text.

Credentials (``credentials=``)
------------------------------
* ``"tool_config"`` (default): the tool reads env vars (``os.getenv``) filled
  from a tool_config. The source must carry ``connection_config_id`` and the
  config is loaded **for the dashboard owner**; the env vars are set for the
  duration of the call only (under a lock), then restored.
* ``"integration"``: the tool resolves the user's integration row through
  ``resolve_integration_user()`` (Google, Odoo...). The owner is bound as the
  invoker for the call only.
* ``"none"``: the tool needs no credential.

Name resolution: first the explicit registry, then every ``tool_*`` function
of the portfolio, by ``"category.tool_name"`` or by bare ``"tool_name"``
(ambiguous bare names are refused).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Literal

logger = logging.getLogger(__name__)

Credentials = Literal["tool_config", "integration", "none"]
_CREDENTIALS = ("tool_config", "integration", "none")

_MARK = "bi_source"
_MARK_CREDENTIALS = "bi_source_credentials"

# name -> function, for tools registered explicitly (not discovered by scanning).
_REGISTRY: dict[str, Callable[..., Any]] = {}

_REFUSED = "outil « {name} » absent de la liste des outils lisibles comme source de données."


class BiSourceError(ValueError):
    """The requested tool cannot be used as a BI source."""


def _mark(fn: Callable[..., Any], credentials: str) -> Callable[..., Any]:
    if credentials not in _CREDENTIALS:
        raise ValueError(f"credentials must be one of {_CREDENTIALS}, got {credentials!r}")
    setattr(fn, _MARK, True)
    setattr(fn, _MARK_CREDENTIALS, credentials)
    return fn


def bi_source(fn: Callable[..., Any] | None = None, *, credentials: Credentials = "tool_config"):
    """Mark a read-only tabular tool as readable by BI dashboards (see module doc)."""
    if fn is not None:
        return _mark(fn, "tool_config")
    return lambda f: _mark(f, credentials)


def register_bi_source(fn: Callable[..., Any], *, name: str | None = None, credentials: Credentials = "tool_config"):
    """Mark ``fn`` and register it under ``name`` (default: its ``__name__``)."""
    _mark(fn, credentials)
    _REGISTRY[name or fn.__name__] = fn
    return fn


def is_bi_source(fn: Any) -> bool:
    return callable(fn) and getattr(fn, _MARK, False) is True


def credentials_of(fn: Callable[..., Any]) -> str:
    return getattr(fn, _MARK_CREDENTIALS, "tool_config")


_index: dict[str, Callable[..., Any]] | None = None


def _portfolio_functions() -> dict[str, Callable[..., Any]]:
    """Every ``tool_*`` function of the portfolio, by ``category.name`` and bare name
    (a bare name shared by two categories maps to ``None``-like ambiguity: dropped)."""
    global _index
    if _index is not None:
        return _index
    import importlib

    from apowerb.tools_store.tool_manager import get_tools_store

    found: dict[str, Callable[..., Any]] = {}
    bare: dict[str, list[Callable[..., Any]]] = {}
    for category, names in get_tools_store().get_all_tools().items():
        if category == "overlay":
            continue
        try:
            module = importlib.import_module(f"apowerb.tools_store.portfolio.{category}")
        except Exception:  # noqa: BLE001 - one broken module must not hide the others
            logger.warning("[BiSource] module %s not importable", category)
            continue
        for dotted in names:
            attr = dotted.split(".", 1)[1]
            fn = getattr(module, attr, None)
            if callable(fn):
                found[dotted] = fn
                bare.setdefault(attr, []).append(fn)
    for attr, fns in bare.items():
        if len(fns) == 1:
            found[attr] = fns[0]
    _index = found
    return found


def reset_index() -> None:
    global _index
    _index = None


def resolve_bi_source(name: str) -> Callable[..., Any]:
    """The allowlisted tool called ``name``, or :class:`BiSourceError`.

    Unknown and not-allowlisted tools get the same message, so the check
    cannot be used to list the portfolio."""
    fn = _REGISTRY.get(name)
    if fn is None:
        fn = _portfolio_functions().get(name)
    if fn is None or not is_bi_source(fn):
        raise BiSourceError(_REFUSED.format(name=name))
    return fn
