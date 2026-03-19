from typing import TypeVar

_T = TypeVar("_T")

def replace_refs(
    obj: _T,
    base_uri: str = ...,
    loader: object = ...,
    jsonschema: bool = ...,
    load_on_repr: bool = ...,
    merge_props: bool = ...,
    proxies: bool = ...,
    lazy_load: bool = ...,
) -> _T: ...
