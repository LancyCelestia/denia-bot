import json
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

from .model import Scope
from .render import DEFAULT_FONT


class Config(BaseModel):
    bili_enabled: bool=False
    bili_interval: int=Field(default=300,ge=60,le=86400)
    bili_targets: list[str]=Field(default_factory=list,max_length=64)
    bili_state_file: Path=Path(__file__).resolve().parents[1]/'data/bilibili_dynamic/state.sqlite3'
    bili_font_file: Path=DEFAULT_FONT
    bili_resume_source: bool=False
    bili_use_parser_login: bool=False

    @field_validator('bili_targets',mode='before')
    @classmethod
    def parse_targets(cls,values):
        # NoneBot may pass constrained list fields from process env as raw JSON.
        if isinstance(values,str):
            try:values=json.loads(values)
            except ValueError as exc:
                raise ValueError('bili_targets must be a JSON array of target strings') from exc
            if not isinstance(values,list):
                raise ValueError('bili_targets must be a JSON array of target strings')
        return values

    @field_validator('bili_targets')
    @classmethod
    def validate_targets(cls,values):
        for value in values:
            parts=value.split(':')
            if len(parts)!=3:raise ValueError('target must be bot:group:id or bot:private:id')
            try:Scope(*parts)
            except Exception as exc:raise ValueError('invalid target') from exc
        return list(dict.fromkeys(values))

    @model_validator(mode='after')
    def enabled_targets(self):
        if self.bili_enabled and not self.bili_targets:
            raise ValueError('enabled plugin requires explicit receiving targets')
        return self

    def targets(self):return {tuple(value.split(':')) for value in self.bili_targets}
