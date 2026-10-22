"""Export options forwarded to AutoTune; publication_api validates selected runs."""

from pydantic import BaseModel, Field


class CatalogExportRequest(BaseModel):
    # The catalog's name for the model. A model the catalog already has keeps
    # its own; for a new one, empty = derived from the served model name.
    name: str = ""
    served_name: str = ""
    chart_version: str = ""  # empty = the engine's minimum, as a range
    default_variant: str = ""
    include_env: bool = True
    include_volumes: bool = False
    # Empty = 90 minutes, raised to 3x the measured load when that is longer.
    startup_minutes: int | None = Field(default=None, ge=1, le=240)
    # nvidia.com/gpu.product labels for every variant without its own; empty =
    # the known label of the card each config was measured on.
    gpu_product: list[str] = Field(default_factory=list)
    # metadata.yaml for a model new to the catalog. source.hf is required then:
    # every site finds the weights by it.
    hf: str = ""
    hf_revision: str = ""
    display_name: str = ""
    description: str = ""
    family: str = ""
    tags: list[str] = Field(default_factory=list)
    license: str = ""
    homepage: str = ""
    # A saved perf report (either language) to ship next to the files. It must
    # compare exactly the runs being exported; a tuning result needs one.
    report_id: int | None = None
    # Ask AutoTune to look the model up in its published catalog.
    use_catalog: bool = True
    # Env var name -> recipe | site | secret | platform, for this export only;
    # PUT /catalog-export/env-categories/{name} settles one for every export.
    env_categories: dict[str, str] = Field(default_factory=dict)
