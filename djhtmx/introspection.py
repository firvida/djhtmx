import inspect
import typing as t
import types
from collections import defaultdict
from dataclasses import dataclass

from django.db import models
from django.utils.datastructures import MultiValueDict
from pydantic import BeforeValidator, PlainSerializer

# model


@dataclass(slots=True)
class ModelRelatedField:
    name: str
    relation_name: str
    related_model_name: str


MODEL_RELATED_FIELDS: dict[
    t.Type[models.Model], tuple[ModelRelatedField, ...]
] = {}


def Model(model: t.Type[models.Model]):
    return t.Annotated[
        model,
        BeforeValidator(
            lambda v: v
            if isinstance(v, model)
            else model.objects.filter(pk=v).first()
        ),
        PlainSerializer(
            lambda v: v.pk,
            int if (pk := model().pk) is None else type(pk),
        ),
    ]


def annotate_model(annotation):
    if issubclass_safe(annotation, models.Model):
        return Model(annotation)
    elif isinstance_safe(annotation, types.UnionType):
        return t.Union[*(annotate_model(a) for a in annotation.__args__)]  # type:ignore
    elif type(annotation).__name__ == "_TypedDictMeta":
        return t.TypedDict(
            annotation.__name__,  # type: ignore
            {
                k: annotate_model(v)  # type: ignore
                for k, v in annotation.__annotations__.items()
            },
        )
    else:
        return annotation


def isinstance_safe(o, types):
    try:
        return isinstance(o, types)
    except TypeError:
        return False

def issubclass_safe(o, types):
    try:
        return issubclass(o, types)
    except TypeError:
        return False



def get_related_fields(model):
    related_fields = MODEL_RELATED_FIELDS.get(model)
    if related_fields is None:
        fields = []
        for field in model._meta.get_fields():
            if (
                isinstance(field, models.ForeignKey)
                and (relation_name := field.related_query_name())
                and relation_name != "+"
            ):
                rel_meta = field.related_model._meta  # type: ignore
                fields.append(
                    ModelRelatedField(
                        name=field.attname,
                        relation_name=relation_name,
                        related_model_name=(
                            f"{rel_meta.app_label}.{rel_meta.model_name}"
                        ),
                    )
                )
        related_fields = MODEL_RELATED_FIELDS[model] = tuple(fields)
    return related_fields


# filtering


def filter_parameters(f, kwargs):
    has_kwargs = any(
        param.kind == inspect.Parameter.VAR_KEYWORD
        for param in inspect.signature(f).parameters.values()
    )
    if has_kwargs:
        return kwargs
    else:
        return {
            param: value
            for param, value in kwargs.items()
            if param in inspect.signature(f).parameters.keys()
        }


# Decoder for client requests


def parse_request_data(data: MultiValueDict[str, t.Any]):
    return _parse_obj(_extract_data(data))


def _extract_data(data: MultiValueDict[str, t.Any]):
    for key in set(data):
        if key.endswith("[]"):
            value = data.getlist(key)
            key = key.removesuffix("[]")
        else:
            value = data.get(key)
        yield key.split("."), value


def _parse_obj(
    data: t.Iterable[tuple[list[str], t.Any]], output=None
) -> dict[str, t.Any] | t.Any:
    output = output or {}
    arrays = defaultdict(lambda: defaultdict(dict))  # field -> index -> value
    for key, value in data:
        fragment, *tail = key
        if "[" in fragment:
            field_name = fragment[: fragment.index("[")]
            index = int(fragment[fragment.index("[") + 1 : -1])
            arrays[field_name][index] = (
                _parse_obj([(tail, value)], arrays[field_name][index])
                if tail
                else value
            )
        else:
            output[fragment] = _parse_obj([(tail, value)]) if tail else value

    for field, items in arrays.items():
        output[field] = [
            v for _, v in sorted(items.items(), key=lambda kv: kv[0])
        ]
    return output
