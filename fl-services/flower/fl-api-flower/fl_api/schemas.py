from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    status: str


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="allow")


class FlowerCommandResponse(BaseModel):
    model_config = ConfigDict(extra="allow")


class ServerInfoModel(BaseModel):
    status: str


class ClientInfoModel(BaseModel):
    name: str
    status: str


class ErrorResponse(BaseModel):
    detail: str
