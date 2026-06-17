from typing import Any

from pydantic import BaseModel, ConfigDict


class AlgoChartSeries(BaseModel):
    name: str
    values: list[float | None]


class AlgoChart(BaseModel):
    title: str
    x: list[str]
    series: list[AlgoChartSeries]


class AlgoFindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    algorithm_name: str
    category: str
    action: str
    confidence_score: float
    status: str
    data_requirements: str
    reason: str
    logic: str
    indicators: dict[str, Any]
    chart: AlgoChart | None = None
